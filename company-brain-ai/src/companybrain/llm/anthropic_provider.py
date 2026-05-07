"""
AnthropicProvider — Claude models via the Anthropic API.

Model assignment per task role:
  FAST        → claude-haiku-4-5-20251001
  BALANCED    → claude-sonnet-4-6
  SYNTHESIS   → claude-opus-4-6
  REASONING   → claude-sonnet-4-6
  QUERY       → claude-opus-4-6

Override per role: ANTHROPIC_MODEL_<ROLE>=<model>
Requires: ANTHROPIC_API_KEY env var
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import structlog
from anthropic import AsyncAnthropic

from companybrain.llm.base import (
    LLMProvider, TaskRole, ChatMessage, ChatResponse,
    LLMCallRecord, compute_cost_usd, log_llm_call,
)

log = structlog.get_logger(__name__)

_DEFAULT_MODELS: dict[TaskRole, str] = {
    TaskRole.FAST:      "claude-haiku-4-5-20251001",
    TaskRole.BALANCED:  "claude-sonnet-4-6",
    TaskRole.SYNTHESIS: "claude-opus-4-6",
    TaskRole.REASONING: "claude-sonnet-4-6",
    TaskRole.QUERY:     "claude-opus-4-6",
}

_ENV_OVERRIDES: dict[TaskRole, str] = {
    role: os.environ[f"ANTHROPIC_MODEL_{role.value.upper()}"]
    for role in TaskRole
    if f"ANTHROPIC_MODEL_{role.value.upper()}" in os.environ
}


class AnthropicProvider(LLMProvider):

    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)
        log.info("AnthropicProvider initialised", model_overrides=_ENV_OVERRIDES)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def model_for_role(self, role: TaskRole) -> str:
        return _ENV_OVERRIDES.get(role, _DEFAULT_MODELS[role])

    async def chat(
        self,
        messages: list[ChatMessage],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> ChatResponse:
        model = self.model_for_role(role)

        # Anthropic separates system prompt from the message list
        system_msgs = [m for m in messages if m.role == "system"]
        user_msgs   = [m for m in messages if m.role != "system"]

        system_text = "\n\n".join(m.content for m in system_msgs) if system_msgs else None

        # Build system param: use prompt caching when a system prompt is present.
        system_param = (
            [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
            if system_text is not None
            else []
        )

        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_param,
            messages=[{"role": m.role, "content": m.content} for m in user_msgs],
        )

        content = response.content[0].text if response.content else ""

        cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read     = getattr(response.usage, "cache_read_input_tokens",     0) or 0
        log.debug(
            "anthropic chat complete",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read=cache_read,
            cache_creation=cache_creation,
        )

        chat_response = ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

        cost = compute_cost_usd(
            self.provider_name,
            model,
            response.usage.input_tokens,
            response.usage.output_tokens,
            cache_read,
        )
        log_llm_call(LLMCallRecord(
            provider=self.provider_name,
            model=model,
            role=role.value,
            task="",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cost_usd=cost,
            ts=datetime.now(timezone.utc).isoformat(),
        ))

        return chat_response
