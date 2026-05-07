"""
OpenAIProvider — GPT models via the OpenAI API.

Also works as a generic OpenAI-compatible provider, so you can point it at:
  - Groq       (LLM_PROVIDER=groq)
  - OpenRouter (LLM_PROVIDER=openrouter)
  - Azure OpenAI
  - Any other OpenAI-compatible endpoint

Model assignment per task role:
  FAST        → gpt-4o-mini
  BALANCED    → gpt-4o
  SYNTHESIS   → gpt-4o
  REASONING   → gpt-4o
  QUERY       → gpt-4o

Override per role: OPENAI_MODEL_<ROLE>=<model>
Requires: OPENAI_API_KEY env var (or GROQ_API_KEY / OPENROUTER_API_KEY — handled by factory)
Optional: OPENAI_BASE_URL (for Azure or custom endpoints)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import structlog
from openai import AsyncOpenAI

from companybrain.llm.base import (
    LLMProvider, TaskRole, ChatMessage, ChatResponse,
    LLMCallRecord, compute_cost_usd, log_llm_call,
)

log = structlog.get_logger(__name__)

_DEFAULT_MODELS: dict[TaskRole, str] = {
    TaskRole.FAST:      "gpt-4o-mini",
    TaskRole.BALANCED:  "gpt-4o",
    TaskRole.SYNTHESIS: "gpt-4o",
    TaskRole.REASONING: "gpt-4o",
    TaskRole.QUERY:     "gpt-4o",
}

_ENV_OVERRIDES: dict[TaskRole, str] = {
    role: os.environ[f"OPENAI_MODEL_{role.value.upper()}"]
    for role in TaskRole
    if f"OPENAI_MODEL_{role.value.upper()}" in os.environ
}


class OpenAIProvider(LLMProvider):
    """
    OpenAI-compatible provider.

    When instantiated by the factory for Groq or OpenRouter, `_name` is set
    to "groq" or "openrouter" so logs and ChatResponse.provider are accurate.
    Model defaults come from the caller (factory passes per-provider defaults).
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        name: str = "openai",
        model_map: Optional[dict[TaskRole, str]] = None,
    ):
        self._name = name
        self._model_map: dict[TaskRole, str] = model_map or _DEFAULT_MODELS
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,  # None = default OpenAI endpoint
        )
        log.info(
            "OpenAIProvider initialised",
            provider=name,
            base_url=base_url or "https://api.openai.com",
            models={r.value: m for r, m in self._model_map.items()},
        )

    @property
    def provider_name(self) -> str:
        return self._name

    def model_for_role(self, role: TaskRole) -> str:
        # OPENAI_MODEL_* env overrides only apply when the provider is actually OpenAI.
        # When running as groq/openrouter the model_map passed by the factory takes precedence,
        # so that GROQ_MODEL_* / OPENROUTER_MODEL_* in .env are respected correctly.
        if self._name == "openai":
            return _ENV_OVERRIDES.get(role, self._model_map.get(role, _DEFAULT_MODELS[role]))
        return self._model_map.get(role, _DEFAULT_MODELS[role])

    async def chat(
        self,
        messages: list[ChatMessage],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> ChatResponse:
        model = self.model_for_role(role)

        response = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )

        msg = response.choices[0].message
        content = msg.content or ""

        # Reasoning models (GPT-OSS, Qwen3, DeepSeek-R1) put their visible answer
        # in message.content and their chain-of-thought in reasoning_content.
        # If content is empty but reasoning_content exists, the model ran out of
        # max_tokens before finishing — surface reasoning_content as a fallback
        # so callers get something parseable rather than an empty string.
        if not content:
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                content = reasoning
                log.warning(
                    "Reasoning model returned empty content — using reasoning_content fallback. "
                    "Increase max_tokens for this role to get a proper response.",
                    model=model,
                    role=role.value,
                )

        input_tokens  = response.usage.prompt_tokens     if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        chat_response = ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
        )

        cost = compute_cost_usd(self.provider_name, model, input_tokens, output_tokens, 0)
        log_llm_call(LLMCallRecord(
            provider=self.provider_name,
            model=model,
            role=role.value,
            task="",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=cost,
            ts=datetime.now(timezone.utc).isoformat(),
        ))

        return chat_response
