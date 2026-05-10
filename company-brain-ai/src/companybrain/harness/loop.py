"""HarnessLoop — prompt-controlled tool dispatch (ADR-0051 P1, extended in P4).

The loop owns the conversation history. Each iteration:
  1. Send {system + user + tool-result history} to the LLM.
  2. If the response has no tool_calls → return the text as the final answer.
  3. If it has tool_calls → look each up in TOOL_REGISTRY, dispatch in parallel,
     append one tool-result message per call, then loop.

This is the single replacement for the linear stage machine in
`pipeline/orchestrator.py`. New capabilities are added by registering a new
tool, not by editing this loop.

Phase-4 additions
-----------------
* **Permissions** — before dispatching, the loop asks
  :class:`WorkspaceGrants` whether the tool's required capabilities are
  granted. ``deny`` short-circuits to a tool-result error; ``ask`` is
  resolved via the interactive/auto-approve gate.
* **Hooks** — :func:`hooks.fire` is invoked at ``session_start``,
  ``session_end``, and around extraction/storage tool calls.
* **Cost** — every assistant turn's token usage is rolled into a
  :class:`CostTracker`, and any LLM-using tool's reported cost is
  attributed to that tool.
* **TodoList** — when a :class:`TodoList` is supplied (typically from a
  :class:`Session`), each tool call adds an item and transitions through
  pending → in_progress → completed/failed. The SSE endpoint subscribes.
* **Compaction** — after each turn, the loop checks
  :func:`compaction.needs_compaction`; on hit, the message history is
  compacted in place and the run continues.

The loop is provider-agnostic: it talks to whatever LLMProvider the factory
returns. Existing tests don't pass the new kwargs and the loop falls back
to "no permissions, no hooks, no streaming" — fully backward compatible.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from companybrain.harness import compaction as compaction_mod
from companybrain.harness import hooks as hooks_mod
from companybrain.harness.cost import CostTracker
from companybrain.harness.permissions import (
    Decision,
    WorkspaceGrants,
    is_auto_approve_env,
    resolve_ask,
)
from companybrain.harness.progress import TodoItem, TodoList, TodoStatus
from companybrain.harness.system_prompt import build_system_prompt
from companybrain.harness.tools import TOOL_REGISTRY
from companybrain.llm import get_provider
from companybrain.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    TaskRole,
    ToolCall,
    compute_cost_usd,
)

log = structlog.get_logger(__name__)


# Mapping from tool name → (pre_event, post_event) hook names. Tools not in
# this map fire no hooks. The events line up with the canonical pipeline
# stages so a hook author can target "pre_extraction" without caring whether
# the model used `extract_methods_from_class` directly or fanned it out via
# `spawn_extractor`.
_TOOL_HOOK_EVENTS: dict[str, tuple[str, str]] = {
    "extract_methods_from_class": ("pre_extraction", "post_extraction"),
    "spawn_extractor":            ("pre_extraction", "post_extraction"),
    "write_to_brain":             ("pre_storage",    "post_storage"),
    "finalize_brain":             ("pre_storage",    "post_storage"),
}


@dataclass
class HarnessResult:
    """What HarnessLoop.run returns to its caller."""
    final_text: str = ""
    iterations: int = 0
    # One entry per dispatched tool: {name, arguments, ok, error}.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)  # full transcript for debugging
    cost: dict[str, Any] = field(default_factory=dict)         # CostTracker.summary()

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def succeeded_tool_calls(self) -> int:
        return sum(1 for c in self.tool_calls if c.get("ok"))


class HarnessLoop:
    """Drives a tool-using conversation until the model emits a text-only turn.

    Parameters
    ----------
    max_iterations
        Hard cap on assistant turns. Reaching the cap is treated as a
        controlled stop, not an error — whatever text the model last produced
        becomes `final_text` so callers always get a result back.
    role
        TaskRole used to pick the model. BALANCED defaults to Sonnet on
        Anthropic, which has the strongest tool-calling reliability.
    max_tokens
        Per-turn output cap forwarded to the provider.
    tool_timeout_seconds
        Per-tool-call wall-clock cap. Exceeding it converts to a tool-result
        error (so the model can retry or give up) rather than aborting the run.
    permissions
        Per-workspace grants table. ``None`` (default) disables enforcement —
        used by tests and by the legacy code paths that pre-date P4.
    interactive
        Whether the run is attached to a terminal; only meaningful when an
        ``ASK`` decision is encountered. Non-interactive ASK becomes DENY
        unless ``auto_approve`` overrides.
    auto_approve
        ``True`` to convert any ``ASK`` to ``AUTO``. CLI ``--yes`` and the
        ``BRAIN_AUTOAPPROVE`` env var both flow into this.
    cost, todo, hook_repo_path, session_id, hook_timeout_s
        Wiring for the P4 surface — supply when the harness is being driven
        from a :class:`Session`. All optional.
    compaction_threshold, context_limit_tokens
        Override the compaction trigger. Defaults are sensible for Claude.
    """

    def __init__(
        self,
        *,
        max_iterations: int = 50,
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4_000,
        tool_timeout_seconds: float = 60.0,
        provider: LLMProvider | None = None,
        # ── ADR-0051 P4 ────────────────────────────────────────────────────
        permissions: WorkspaceGrants | None = None,
        interactive: bool = False,
        auto_approve: bool | None = None,
        cost: CostTracker | None = None,
        todo: TodoList | None = None,
        hook_repo_path: str | None = None,
        hook_timeout_s: int = 30,
        session_id: str | None = None,
        compaction_threshold: float = compaction_mod.COMPACT_THRESHOLD,
        context_limit_tokens: int = compaction_mod.CONTEXT_LIMIT_TOKENS,
        enable_hooks: bool = True,
    ):
        # Defer provider lookup until run-time so tests can inject a mock
        # without needing real API credentials configured.
        self._provider = provider if provider is not None else get_provider()
        self._max_iter = max_iterations
        self._role = role
        self._max_tokens = max_tokens
        self._tool_timeout = tool_timeout_seconds

        # P4 wiring
        self._permissions = permissions
        self._interactive = interactive
        self._auto_approve = (
            auto_approve if auto_approve is not None else is_auto_approve_env()
        )
        self._cost = cost if cost is not None else CostTracker()
        self._todo = todo
        self._hook_repo_path = hook_repo_path
        self._hook_timeout_s = int(hook_timeout_s)
        self._session_id = session_id
        self._enable_hooks = bool(enable_hooks)
        self._compaction_threshold = float(compaction_threshold)
        self._context_limit_tokens = int(context_limit_tokens)

        # Internal counters surfaced through telemetry.
        self._compactions: int = 0
        self._max_context_used: int = 0
        self._hook_counts: dict[str, int] = {}

    async def run(self, user_message: str, *, context: dict[str, Any]) -> HarnessResult:
        """Run the loop on one user message; return the final text + telemetry.

        `context` is opaque to the loop and forwarded verbatim to every tool
        handler. Use it to pass workspace_id, repo_path, FileCache, etc.
        """
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=build_system_prompt(context)),
            ChatMessage(role="user",   content=user_message),
        ]
        result = HarnessResult(messages=messages)
        tool_defs = [t.definition for t in TOOL_REGISTRY.values()]
        started = time.monotonic()

        # session_start hook — fires once per run. Failures are logged but
        # never abort the run (see hooks.fire).
        await self._fire_hook("session_start", {
            "session_id": self._session_id,
            "endpoint":   context.get("endpoint_path"),
            "method":     context.get("http_method"),
            "repo_path":  context.get("repo_path"),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

        usage_total = 0  # running input-token usage; threshold for compaction.

        for i in range(self._max_iter):
            result.iterations = i + 1

            response: ChatResponse = await self._provider.chat_with_tools(
                messages=messages,
                tools=tool_defs,
                role=self._role,
                max_tokens=self._max_tokens,
            )

            # Track token usage at the loop level. Sub-agent costs flow back
            # via _dispatch when a spawn_* tool returns a `total_cost_usd`.
            usage_total += int(response.input_tokens or 0)
            usage_total += int(response.output_tokens or 0)
            self._max_context_used = max(self._max_context_used, usage_total)
            self._accrue_loop_cost(response)

            log.debug(
                "harness.turn",
                iteration=result.iterations,
                wants_tools=response.wants_tool_call,
                tools_requested=[tc.name for tc in response.tool_calls],
                content_len=len(response.content or ""),
                usage_total=usage_total,
            )

            if not response.tool_calls:
                # Text-only turn → that's the final answer.
                result.final_text = response.content or ""
                break

            # Append the assistant turn (verbatim, with its tool_calls attached so
            # providers that need the full history get it back on the next call).
            messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            # Fan out tool calls in parallel — one assistant turn can request
            # multiple independent calls (the same pattern Claude Code uses).
            tool_results = await asyncio.gather(
                *[self._dispatch(tc, context) for tc in response.tool_calls],
                return_exceptions=False,
            )

            for tc, (content_str, ok, err) in zip(response.tool_calls, tool_results, strict=False):
                result.tool_calls.append({
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "ok": ok,
                    "error": err,
                })
                messages.append(ChatMessage(
                    role="tool",
                    content=content_str,
                    tool_call_id=tc.call_id,
                ))

            # ── compaction gate ───────────────────────────────────────────
            if compaction_mod.needs_compaction(
                messages, usage_total,
                context_limit=self._context_limit_tokens,
                threshold=self._compaction_threshold,
            ):
                messages, decision = compaction_mod.compact(
                    messages,
                    usage_total_before=usage_total,
                    context_limit=self._context_limit_tokens,
                    threshold=self._compaction_threshold,
                )
                if decision.compacted:
                    self._compactions += 1
                    # Reset usage_total to a small post-compaction estimate
                    # — the next turn will replenish it.
                    usage_total = max(0, usage_total // 4)
                # Replace the result.messages reference so callers see the
                # post-compaction transcript live.
                result.messages = messages
        else:
            log.warning(
                "harness.max_iterations_reached",
                iterations=self._max_iter,
                tool_calls=result.tool_call_count,
            )
            # Salvage the last assistant text we have so callers still get something.
            for m in reversed(messages):
                if m.role == "assistant" and m.content:
                    result.final_text = m.content
                    break

        # session_end hook — fires once per run, regardless of success.
        wall_time_seconds = round(time.monotonic() - started, 3)
        await self._fire_hook("session_end", {
            "session_id": self._session_id,
            "status":     "success" if result.final_text else "incomplete",
            "duration_s": wall_time_seconds,
            "tool_calls": result.tool_call_count,
            "cost_usd":   round(self._cost.total_cost_usd, 4),
        })

        result.cost = self._cost.summary()
        result.telemetry = {
            "iterations": result.iterations,
            "tool_calls_total": result.tool_call_count,
            "tool_calls_ok": result.succeeded_tool_calls,
            "wall_time_seconds": wall_time_seconds,
            "provider": self._provider.provider_name,
            "model": self._provider.model_for_role(self._role),
            # ADR-0051 P3 — set by build_system_prompt. None if no match.
            "skill_loaded":    context.get("skill_loaded"),
            "brain_md_loaded": bool(context.get("brain_md_loaded")),
            # ADR-0051 P4
            "cost":                  result.cost,
            "max_context_used":      self._max_context_used,
            "compaction_invocations": self._compactions,
            "hook_invocations":      dict(self._hook_counts),
        }
        return result

    # ── internals ───────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        tc: ToolCall,
        context: dict[str, Any],
    ) -> tuple[str, bool, str | None]:
        """Resolve one tool_call → (content_string, ok, error).

        Always returns a string so the conversation stays well-formed even when
        the tool fails — the model sees the error and decides what to do next.
        """
        tool = TOOL_REGISTRY.get(tc.name)
        if tool is None:
            err = f"Unknown tool: {tc.name!r}. Available: {sorted(TOOL_REGISTRY)}"
            log.warning("harness.unknown_tool", tool=tc.name)
            return _to_str({"error": err}), False, err

        # Permission gate. ``self._permissions is None`` → no enforcement, the
        # legacy/test path. With a grants table set, deny short-circuits and
        # ask is resolved via the interactive/auto-approve resolver.
        if self._permissions is not None:
            decision = self._permissions.decide(list(tool.requires))
            if decision == Decision.ASK:
                decision = resolve_ask(
                    interactive=self._interactive,
                    auto_approve=self._auto_approve,
                )
            if decision == Decision.DENY:
                err = (
                    f"Tool {tc.name!r} denied by workspace grants "
                    f"(requires {[c.value for c in tool.requires]})"
                )
                log.warning("harness.tool_denied", tool=tc.name,
                            requires=[c.value for c in tool.requires])
                return _to_str({"error": err}), False, err

        # TodoList: open an item before dispatch, close it after.
        todo_id = self._begin_todo(tc)

        # Hook firing — emit pre_*. The hook may return modifiers; we surface
        # the whole dict in `context["hook_<event>"]` so tools can opt in.
        pre_event, post_event = _TOOL_HOOK_EVENTS.get(tc.name, ("", ""))
        if pre_event:
            mods = await self._fire_hook(pre_event, {
                "tool":      tc.name,
                "arguments": tc.arguments,
            })
            if mods:
                context[f"hook_{pre_event}"] = mods

        try:
            raw = await asyncio.wait_for(
                tool.invoke(tc.arguments, context=context),
                timeout=self._tool_timeout,
            )
        except TimeoutError:
            err = f"Tool {tc.name} timed out after {self._tool_timeout}s"
            log.warning("harness.tool_timeout", tool=tc.name, timeout=self._tool_timeout)
            self._end_todo(todo_id, status=TodoStatus.FAILED, error=err)
            return _to_str({"error": err}), False, err
        except Exception as exc:  # noqa: BLE001 — surface any failure to the model
            log.exception("harness.tool_error", tool=tc.name)
            err_str = f"{type(exc).__name__}: {exc}"
            self._end_todo(todo_id, status=TodoStatus.FAILED, error=err_str)
            return _to_str({"error": err_str}), False, err_str

        # Cost attribution — sub-agent fan-out tools report aggregate cost in
        # the result dict; fold that into the per-tool tracker so the
        # cost.by_tool breakdown reflects "where the money actually went".
        self._attribute_tool_cost(tc.name, raw)

        if post_event:
            await self._fire_hook(post_event, {
                "tool":      tc.name,
                "arguments": tc.arguments,
                "result":    _summarise_for_hook(raw),
            })

        self._end_todo(todo_id, status=TodoStatus.COMPLETED)
        return _to_str(raw), True, None

    # ── todo helpers ────────────────────────────────────────────────────────

    def _begin_todo(self, tc: ToolCall) -> str | None:
        """Open a TodoItem for one tool call; return its id (or None if no list)."""
        if self._todo is None:
            return None
        # Per-call id; doesn't have to be globally unique outside this run.
        item_id = tc.call_id or f"call-{id(tc)}"
        item = TodoItem(id=item_id, title=f"{tc.name}", metadata={"args": dict(tc.arguments)})
        self._todo.add(item)
        self._todo.update(item_id, status=TodoStatus.IN_PROGRESS)
        return item_id

    def _end_todo(
        self,
        item_id: str | None,
        *,
        status: TodoStatus,
        error: str | None = None,
    ) -> None:
        if item_id is None or self._todo is None:
            return
        meta = {"error": error} if error else None
        try:
            self._todo.update(item_id, status=status, metadata=meta or {})
        except KeyError:
            # Item was never registered — should not happen but is harmless.
            pass

    # ── hook helpers ────────────────────────────────────────────────────────

    async def _fire_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke a hook if hooks are enabled and we have a repo path. Counts firings."""
        if not self._enable_hooks or not self._hook_repo_path:
            return {}
        try:
            mods = await hooks_mod.fire(
                event,
                payload,
                repo_path=self._hook_repo_path,
                timeout_s=self._hook_timeout_s,
            )
        except Exception:  # noqa: BLE001 — hooks must never break the run
            log.exception("harness.hook_unexpected_error", hook=event)
            return {}
        # Count even no-op invocations (script absent → fire returns {}). The
        # acceptance test asserts hooks ran; the count must reflect the
        # script-was-present case. We only increment when the script existed.
        # `fire` returns {} for "absent or error" both — to disambiguate we
        # check the file-system marker here.
        if _hook_script_exists(self._hook_repo_path, event):
            self._hook_counts[event] = self._hook_counts.get(event, 0) + 1
        return mods

    # ── cost helpers ────────────────────────────────────────────────────────

    def _accrue_loop_cost(self, response: ChatResponse) -> None:
        """Roll the loop's own LLM cost into the tracker under '_loop'."""
        ip = int(response.input_tokens or 0)
        op = int(response.output_tokens or 0)
        cr = int(response.cache_read_tokens or 0)
        try:
            cost_usd = compute_cost_usd(
                self._provider.provider_name,
                self._provider.model_for_role(self._role),
                ip, op, cr,
            )
        except Exception:  # noqa: BLE001 — telemetry must never break the run
            cost_usd = 0.0
        self._cost.add(
            "_loop",
            input_tokens=ip,
            output_tokens=op,
            cost_usd=cost_usd,
        )

    def _attribute_tool_cost(self, tool: str, raw: Any) -> None:
        """Pull a sub-agent fan-out cost out of the tool's return value, if any."""
        if not isinstance(raw, dict):
            return
        cost_usd = raw.get("total_cost_usd") or raw.get("cost_usd")
        if not cost_usd:
            return
        in_tok  = int(raw.get("input_tokens")  or raw.get("total_input_tokens")  or 0)
        out_tok = int(raw.get("output_tokens") or raw.get("total_output_tokens") or 0)
        try:
            self._cost.add(
                tool,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=float(cost_usd),
            )
        except (TypeError, ValueError):
            pass


# ── module helpers ──────────────────────────────────────────────────────────


def _hook_script_exists(repo_path: str, event: str) -> bool:
    """True iff the hook script for `event` exists and is executable."""
    import os
    from pathlib import Path
    p = Path(repo_path) / ".brain" / "hooks" / f"{event}.sh"
    return p.exists() and os.access(p, os.X_OK)


def _summarise_for_hook(raw: Any) -> Any:
    """Trim long tool results before handing them to a hook (stdin gets noisy)."""
    if isinstance(raw, dict):
        # Keep the headline fields only.
        return {k: raw[k] for k in raw if k in {
            "subagents", "total_cost_usd", "written", "skipped",
            "entity_count", "run_id", "manifest_path", "results_count",
        }}
    if isinstance(raw, list):
        return {"items": len(raw)}
    return {"type": type(raw).__name__}


def _to_str(value: Any) -> str:
    """Render a tool return value for inclusion in the conversation."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
