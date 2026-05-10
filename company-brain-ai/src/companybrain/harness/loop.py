"""HarnessLoop — prompt-controlled tool dispatch (ADR-0051 P1).

The loop owns the conversation history. Each iteration:
  1. Send {system + user + tool-result history} to the LLM.
  2. If the response has no tool_calls → return the text as the final answer.
  3. If it has tool_calls → look each up in TOOL_REGISTRY, dispatch in parallel,
     append one tool-result message per call, then loop.

This is the single replacement for the linear stage machine in
`pipeline/orchestrator.py`. New capabilities are added by registering a new
tool, not by editing this loop.

The loop is provider-agnostic: it talks to whatever LLMProvider the factory
returns. For providers whose `chat_with_tools` falls back to text (because the
provider doesn't expose a native tool API), the loop still terminates correctly
— just with the model emitting a text answer instead of a tool_use block.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from companybrain.harness.system_prompt import build_system_prompt
from companybrain.harness.tools import TOOL_REGISTRY
from companybrain.llm import get_provider
from companybrain.llm.base import ChatMessage, ChatResponse, LLMProvider, TaskRole, ToolCall

log = structlog.get_logger(__name__)


@dataclass
class HarnessResult:
    """What HarnessLoop.run returns to its caller."""
    final_text: str = ""
    iterations: int = 0
    # One entry per dispatched tool: {name, arguments, ok, error}.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)  # full transcript for debugging

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
    """

    def __init__(
        self,
        *,
        max_iterations: int = 50,
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4_000,
        tool_timeout_seconds: float = 60.0,
        provider: LLMProvider | None = None,
    ):
        # Defer provider lookup until run-time so tests can inject a mock
        # without needing real API credentials configured.
        self._provider = provider if provider is not None else get_provider()
        self._max_iter = max_iterations
        self._role = role
        self._max_tokens = max_tokens
        self._tool_timeout = tool_timeout_seconds

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

        for i in range(self._max_iter):
            result.iterations = i + 1

            response: ChatResponse = await self._provider.chat_with_tools(
                messages=messages,
                tools=tool_defs,
                role=self._role,
                max_tokens=self._max_tokens,
            )

            log.debug(
                "harness.turn",
                iteration=result.iterations,
                wants_tools=response.wants_tool_call,
                tools_requested=[tc.name for tc in response.tool_calls],
                content_len=len(response.content or ""),
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

        result.telemetry = {
            "iterations": result.iterations,
            "tool_calls_total": result.tool_call_count,
            "tool_calls_ok": result.succeeded_tool_calls,
            "wall_time_seconds": round(time.monotonic() - started, 3),
            "provider": self._provider.provider_name,
            "model": self._provider.model_for_role(self._role),
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

        try:
            raw = await asyncio.wait_for(
                tool.invoke(tc.arguments, context=context),
                timeout=self._tool_timeout,
            )
            return _to_str(raw), True, None
        except TimeoutError:
            err = f"Tool {tc.name} timed out after {self._tool_timeout}s"
            log.warning("harness.tool_timeout", tool=tc.name, timeout=self._tool_timeout)
            return _to_str({"error": err}), False, err
        except Exception as exc:  # noqa: BLE001 — surface any failure to the model
            log.exception("harness.tool_error", tool=tc.name)
            return _to_str({"error": f"{type(exc).__name__}: {exc}"}), False, str(exc)


def _to_str(value: Any) -> str:
    """Render a tool return value for inclusion in the conversation."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
