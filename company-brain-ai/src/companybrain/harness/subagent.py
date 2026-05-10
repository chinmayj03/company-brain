"""Subagent — minimal isolated agent runner (ADR-0051 P2).

The Subagent is the Task-tool primitive from Claude Code: a small agent loop
that runs in its own context window with a restricted tool subset, returns
only its final result to the caller, and never mutates the parent agent's
conversation. Spawning N sub-agents fans extraction work out N-wide while
keeping the parent's input tokens flat.

Distinct from `HarnessLoop` in two ways:
  1. Fresh context window — does not inherit the parent's conversation.
     The parent only ever observes the sub-agent's `final_text` (and cost
     telemetry); never its tool-call trajectory.
  2. Restricted tool subset — the caller specifies which tools the
     sub-agent may use (e.g. read-only research vs. full extraction).
     Calls to tools outside the allowlist are surfaced to the model as
     errors so it can re-plan, not silently dropped.

The runner is provider-agnostic and re-uses the same TOOL_REGISTRY the
parent loop uses, so a tool registered once is automatically callable from
any sub-agent that lists it.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

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


@dataclass
class SubagentResult:
    """What `Subagent.run` returns to the caller (typically a spawn_* tool).

    `final_text` is the only field the parent harness sees by default — the
    rest is telemetry the spawn_* tool aggregates before returning.
    """
    name: str
    final_text: str = ""
    # One entry per dispatched tool: {name, arguments, ok, error}.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_time_seconds: float = 0.0
    error: str | None = None
    timed_out: bool = False

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def succeeded_tool_calls(self) -> int:
        return sum(1 for c in self.tool_calls if c.get("ok"))


class Subagent:
    """One isolated sub-agent that runs to completion or its iteration cap.

    Parameters
    ----------
    name
        Human-readable label used in logs and result records (e.g.
        ``"extractor:src/main/java/.../Foo.java"``). Not surfaced to the model.
    allowed_tools
        Names of tools (from TOOL_REGISTRY) this sub-agent may invoke.
        Calls to anything outside this set return an error to the model.
    system_prompt
        Static system message. Sub-agents typically receive a focused,
        single-purpose prompt rather than the parent's full pipeline brief.
    role
        TaskRole used to pick the model. Defaults to FAST because per-file
        extraction sub-agents run high-volume; raise to BALANCED when the
        sub-agent does multi-step reasoning (e.g. spawn_verifier).
    max_iterations
        Hard cap on assistant turns. Reaching the cap is a controlled stop
        — the last assistant text is returned as `final_text`.
    max_tokens
        Per-turn output cap forwarded to the provider.
    tool_timeout_seconds
        Per-tool-call wall-clock cap. Exceeding it surfaces a timeout error
        to the model rather than aborting the run.
    provider
        Optional LLMProvider override; injected by tests so the loop can
        run without API credentials.
    """

    def __init__(
        self,
        *,
        name: str,
        allowed_tools: list[str],
        system_prompt: str,
        role: TaskRole = TaskRole.FAST,
        max_iterations: int = 15,
        max_tokens: int = 4_000,
        tool_timeout_seconds: float = 60.0,
        provider: LLMProvider | None = None,
    ):
        self._name = name
        self._allowed = set(allowed_tools)
        self._system = system_prompt
        self._role = role
        self._max_iter = max_iterations
        self._max_tokens = max_tokens
        self._tool_timeout = tool_timeout_seconds
        # Defer provider lookup until run-time so tests can inject a mock
        # without needing real API credentials configured.
        self._provider = provider if provider is not None else get_provider()

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, *, context: dict[str, Any]) -> SubagentResult:
        """Run the sub-agent on one user prompt; return final_text + telemetry.

        `context` is the same opaque dict the parent harness threads through
        to tool handlers. Sub-agents share it by design — caches (file_cache),
        store handles (brain_store), and identifiers (workspace_id, repo_path)
        all live there. What is NOT shared is the parent's conversation.
        """
        result = SubagentResult(name=self._name)
        # Build the sub-agent's own message list from scratch — parent history
        # never leaks in. This is the entire point of the Task primitive.
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self._system),
            ChatMessage(role="user", content=prompt),
        ]
        # Only expose the allowlisted tools to the model. Tools outside the
        # set are completely invisible to it — the schemas are not even sent.
        tool_defs = [
            TOOL_REGISTRY[n].definition
            for n in self._allowed
            if n in TOOL_REGISTRY
        ]
        started = time.monotonic()

        for i in range(self._max_iter):
            result.iterations = i + 1

            try:
                response: ChatResponse = await self._provider.chat_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    role=self._role,
                    max_tokens=self._max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 — surface to caller, don't crash the parent
                log.exception("subagent.provider_error", name=self._name)
                result.error = f"{type(exc).__name__}: {exc}"
                break

            self._accrue_usage(response, result)

            log.debug(
                "subagent.turn",
                name=self._name,
                iteration=result.iterations,
                wants_tools=response.wants_tool_call,
                tools_requested=[tc.name for tc in response.tool_calls],
            )

            if not response.tool_calls:
                # Text-only turn → that's the final answer.
                result.final_text = response.content or ""
                break

            # Append the assistant turn (with tool_calls) so providers that
            # need the full history get it back on the next call.
            messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            tool_results = await asyncio.gather(
                *[self._dispatch(tc, context) for tc in response.tool_calls],
                return_exceptions=False,
            )

            for tc, (content_str, ok, err) in zip(
                response.tool_calls, tool_results, strict=False,
            ):
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
        else:
            # max_iterations reached — salvage the last assistant text we have
            # so the parent always gets something usable.
            log.warning(
                "subagent.max_iterations",
                name=self._name,
                iterations=self._max_iter,
                tool_calls=result.tool_call_count,
            )
            for m in reversed(messages):
                if m.role == "assistant" and m.content:
                    result.final_text = m.content
                    break

        result.wall_time_seconds = round(time.monotonic() - started, 3)
        return result

    # ── internals ───────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        tc: ToolCall,
        context: dict[str, Any],
    ) -> tuple[str, bool, str | None]:
        """Resolve one tool_call → (content_string, ok, error).

        Two extra checks vs. HarnessLoop._dispatch:
          1. Allowlist gate — calls to tools outside `allowed_tools` return an
             error so the model can re-plan.
          2. Wrapped TimeoutError — same cap as the parent but logged under
             the sub-agent's name for clearer traces.
        """
        if tc.name not in self._allowed:
            err = (
                f"Tool {tc.name!r} not in subagent allowlist "
                f"{sorted(self._allowed)}. Pick another tool or stop."
            )
            log.warning("subagent.tool_blocked", name=self._name, tool=tc.name)
            return _to_str({"error": err}), False, err

        tool = TOOL_REGISTRY.get(tc.name)
        if tool is None:
            err = f"Unknown tool: {tc.name!r}. Available: {sorted(self._allowed)}"
            log.warning("subagent.unknown_tool", name=self._name, tool=tc.name)
            return _to_str({"error": err}), False, err

        try:
            raw = await asyncio.wait_for(
                tool.invoke(tc.arguments, context=context),
                timeout=self._tool_timeout,
            )
            return _to_str(raw), True, None
        except TimeoutError:
            err = f"Tool {tc.name} timed out after {self._tool_timeout}s"
            log.warning(
                "subagent.tool_timeout",
                name=self._name, tool=tc.name, timeout=self._tool_timeout,
            )
            return _to_str({"error": err}), False, err
        except Exception as exc:  # noqa: BLE001 — surface any failure to the model
            log.exception("subagent.tool_error", name=self._name, tool=tc.name)
            return _to_str({"error": f"{type(exc).__name__}: {exc}"}), False, str(exc)

    def _accrue_usage(self, response: ChatResponse, result: SubagentResult) -> None:
        """Roll one ChatResponse's usage counters into the running totals.

        Cost is computed locally (not read off the response) because providers
        return raw token counts, not USD — the price table lives in llm.base.
        """
        ip = int(response.input_tokens or 0)
        op = int(response.output_tokens or 0)
        cr = int(response.cache_read_tokens or 0)
        result.input_tokens += ip
        result.output_tokens += op
        try:
            result.cost_usd += compute_cost_usd(
                self._provider.provider_name,
                self._provider.model_for_role(self._role),
                ip, op, cr,
            )
        except Exception:  # noqa: BLE001 — telemetry must never break the run
            pass


async def run_with_timeout(
    agent: Subagent,
    prompt: str,
    *,
    context: dict[str, Any],
    timeout_seconds: float,
) -> SubagentResult:
    """Run a Subagent under a wall-clock cap; convert timeouts to a result, not a raise.

    spawn_* tools call this so one runaway sub-agent can't stall the parent's
    fan-out. The semaphore in the parent tool bounds concurrency; this bounds
    duration.
    """
    try:
        return await asyncio.wait_for(
            agent.run(prompt, context=context),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        log.warning(
            "subagent.run_timeout",
            name=agent.name,
            timeout=timeout_seconds,
        )
        return SubagentResult(
            name=agent.name,
            final_text="",
            timed_out=True,
            error=f"Subagent {agent.name!r} exceeded {timeout_seconds}s wall-time cap.",
        )


def _to_str(value: Any) -> str:
    """Render a tool return value for inclusion in the conversation."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
