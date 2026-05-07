"""
AgentLoop — generic ReAct (Reason + Act) loop for tool-calling agents.

Each agent subclass provides:
  - system_prompt()       → what the agent is trying to accomplish
  - tools()               → which tools it can use (subset of registry)
  - parse_output(str)     → convert final LLM text to typed result

The loop:
  1. LLM sees system prompt + user message + (optionally) tool results so far
  2. If LLM calls a tool → execute it, append result, loop
  3. If LLM produces text (no tool calls) → that's the final answer
  4. If max_turns exceeded → return whatever we have

Design decisions:
  - Tool calls run synchronously in the event loop (all tools are fast I/O)
  - Max turns = 10 by default (prevents infinite loops)
  - Temperature = 0.0 for agents (deterministic routing decisions)
  - Each agent gets its own ToolRegistry subset (principle of least privilege)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

import structlog

from companybrain.llm.base import (
    ChatMessage, TaskRole, ToolCall, ToolResult,
)
from companybrain.llm import get_provider
from companybrain.agents.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

T = TypeVar("T")


class AgentLoop(ABC, Generic[T]):
    """
    Base class for all agents that use tool-calling.

    Subclasses override:
      - SYSTEM_PROMPT   : class-level string
      - TOOL_NAMES      : which tools from the registry this agent can use
      - ROLE            : TaskRole for model selection
      - parse_output()  : convert LLM final text → typed result T
    """

    SYSTEM_PROMPT: str = ""
    TOOL_NAMES: list[str] | None = None   # None = all tools
    ROLE: TaskRole = TaskRole.BALANCED
    MAX_TURNS: int = 10

    def __init__(self):
        self._provider = get_provider()
        self._registry = ToolRegistry(names=self.TOOL_NAMES)
        log.info(
            f"{self.__class__.__name__} ready",
            provider=self._provider.provider_name,
            model=self._provider.model_for_role(self.ROLE),
            tools=[d.name for d in self._registry.definitions],
        )

    async def run(self, user_message: str, **context_vars: Any) -> T:
        """
        Run the agent loop on a user message.
        context_vars are interpolated into SYSTEM_PROMPT via .format().
        """
        system = self.SYSTEM_PROMPT.format(**context_vars) if context_vars else self.SYSTEM_PROMPT

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user",   content=user_message),
        ]

        tool_defs = self._registry.definitions
        turn = 0

        log.debug(
            f"{self.__class__.__name__}: starting",
            turn_limit=self.MAX_TURNS,
            user_msg_len=len(user_message),
        )

        while turn < self.MAX_TURNS:
            turn += 1

            response = await self._provider.chat_with_tools(
                messages=messages,
                tools=tool_defs,
                role=self.ROLE,
                max_tokens=2048,
            )

            log.debug(
                f"{self.__class__.__name__}: turn {turn}",
                wants_tool_call=response.wants_tool_call,
                tool_calls=[tc.name for tc in response.tool_calls],
                content_len=len(response.content),
            )

            if not response.wants_tool_call:
                # Agent produced its final answer
                log.info(
                    f"{self.__class__.__name__}: done",
                    turns=turn,
                    output_len=len(response.content),
                )
                return self.parse_output(response.content)

            # Execute tool calls and feed results back
            # Append the assistant message that requested the tools
            messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                result_text = await self._registry.execute(tc)
                log.debug(
                    f"{self.__class__.__name__}: tool result",
                    tool=tc.name,
                    result_len=len(result_text),
                    preview=result_text[:120],
                )
                messages.append(ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=tc.call_id,
                ))

        # Max turns exceeded — try to parse whatever we have
        log.warning(f"{self.__class__.__name__}: max turns exceeded", turns=turn)
        last_content = next(
            (m.content for m in reversed(messages) if m.role == "assistant" and m.content),
            "",
        )
        return self.parse_output(last_content)

    @abstractmethod
    def parse_output(self, text: str) -> T:
        """Convert the agent's final text response to a typed result."""
        ...

    # ── Shared JSON parsing helper ─────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> dict | list | None:
        """Strip fences and parse JSON, returning None on failure."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            # Try to extract JSON object/array from mixed text
            import re
            m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        return None
