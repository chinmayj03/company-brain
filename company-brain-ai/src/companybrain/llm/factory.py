"""
LLM Provider Factory — creates the correct provider from configuration.

Single source of truth for which provider is active.
Used via dependency injection in the pipeline and API layers.

Configuration (env var LLM_PROVIDER):
  ollama      → OllamaProvider      (default — no API key needed, local models)
  anthropic   → AnthropicProvider   (Claude models)
  openai      → OpenAIProvider      (GPT models or Azure OpenAI)
  groq        → OpenAIProvider      (GPT-OSS/Llama4/Qwen3 via Groq, 500–1000 tok/s)
  openrouter  → OpenAIProvider      (30+ models via OpenRouter, many free)

The singleton is created once at startup and injected everywhere.

Fallback chain (automatic):
  If the primary provider returns a 429 (rate limit) the call is retried on
  each provider in LLM_FALLBACK_CHAIN (comma-separated, e.g. "openrouter,anthropic").
  This means the pipeline completes even when Groq's daily token quota is
  exhausted — it silently promotes to OpenRouter for the rest of the run.
"""

from __future__ import annotations

import asyncio
import structlog

from companybrain.llm.base import LLMProvider, LLMProvider, TaskRole, ChatMessage, ChatResponse
from companybrain.config import settings

log = structlog.get_logger(__name__)

_provider: LLMProvider | None = None

# ── Groq base URL and models (April 2026 lineup) ──────────────────────────────
# FAST      = openai/gpt-oss-20b              (1,000 tok/s — fastest on Groq)
# BALANCED  = meta-llama/llama-4-scout-...    (750 tok/s — vision + tool-use)
# SYNTHESIS = openai/gpt-oss-120b             (500 tok/s — best reasoning)
# REASONING = qwen/qwen3-32b                  (400 tok/s — strong reasoning)
# QUERY     = openai/gpt-oss-120b             (best user-facing responses)
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_GROQ_MODELS: dict[TaskRole, str] = {
    TaskRole.FAST:      settings.groq_model_fast,
    TaskRole.BALANCED:  settings.groq_model_balanced,
    TaskRole.SYNTHESIS: settings.groq_model_synthesis,
    TaskRole.REASONING: settings.groq_model_reasoning,
    TaskRole.QUERY:     settings.groq_model_query,
}

# ── OpenRouter base URL and default free-tier models ───────────────────────────
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODELS: dict[TaskRole, str] = {
    TaskRole.FAST:      settings.openrouter_model_fast,
    TaskRole.BALANCED:  settings.openrouter_model_balanced,
    TaskRole.SYNTHESIS: settings.openrouter_model_synthesis,
    TaskRole.REASONING: settings.openrouter_model_reasoning,
    TaskRole.QUERY:     settings.openrouter_model_query,
}


class FallbackProvider(LLMProvider):
    """
    Wraps a list of providers and automatically falls over to the next one
    whenever the current provider returns a rate-limit (429) error.

    The fallback is sticky within a process lifetime: once provider[0] fails,
    provider[1] handles ALL subsequent calls — no per-call switching overhead.
    """

    # When all providers are exhausted (all 429d), back off and retry from
    # the start of the chain.  Caps at 60 s to avoid hanging indefinitely.
    _BACKOFF_SECONDS = [5, 15, 30, 60]

    def __init__(self, providers: list[LLMProvider]) -> None:
        assert providers, "FallbackProvider needs at least one provider"
        self._providers = providers
        self._active_idx = 0
        self._backoff_attempt = 0

    @property
    def _active(self) -> LLMProvider:
        return self._providers[self._active_idx]

    def _is_rate_limit(self, exc: BaseException) -> bool:
        msg = str(exc)
        return "429" in msg or "rate_limit_exceeded" in msg or "rate limit" in msg.lower()

    def _is_daily_quota(self, exc: BaseException) -> bool:
        """True when the provider's *daily* token quota is exhausted (not just RPM)."""
        msg = str(exc).lower()
        return (
            "tokens per day" in msg
            or "daily_tokens_limit" in msg
            or "daily limit" in msg
            or "exceeded your daily" in msg
            or "quota exceeded" in msg
        )

    def _blacklist_active(self) -> None:
        """
        Permanently skip the active provider for this run (daily quota gone).
        Removes it from the chain so _promote never cycles back to it.
        """
        if len(self._providers) > 1:
            removed = self._providers.pop(self._active_idx)
            log.warning(
                "Daily quota exhausted — removing provider from chain for this run",
                provider=removed.provider_name,
                remaining=[p.provider_name for p in self._providers],
            )
            # Clamp index in case we removed the last element
            self._active_idx = min(self._active_idx, len(self._providers) - 1)

    def _promote(self) -> bool:
        """
        Move to the next fallback provider. Returns True if one is available.
        When all providers are exhausted, waits with exponential backoff and
        resets to the start of the chain so the pipeline can keep going.
        """
        if self._active_idx + 1 < len(self._providers):
            old = self._providers[self._active_idx].provider_name
            self._active_idx += 1
            new = self._providers[self._active_idx].provider_name
            log.warning(
                "Rate limit hit — switching provider",
                from_provider=old,
                to_provider=new,
            )
            return True

        # All providers exhausted — signal caller to backoff+retry
        return False

    @property
    def provider_name(self) -> str:
        return self._active.provider_name

    def model_for_role(self, role: TaskRole) -> str:
        return self._active.model_for_role(role)

    async def _backoff_and_reset(self) -> None:
        """Wait with exponential backoff then reset to the first surviving provider."""
        wait = self._BACKOFF_SECONDS[
            min(self._backoff_attempt, len(self._BACKOFF_SECONDS) - 1)
        ]
        self._backoff_attempt += 1
        log.warning(
            "All providers rate-limited — backing off before retry",
            wait_seconds=wait,
            attempt=self._backoff_attempt,
            providers=[p.provider_name for p in self._providers],
        )
        await asyncio.sleep(wait)
        self._active_idx = 0   # reset to first surviving provider

    def _handle_rate_limit(self, exc: BaseException) -> bool:
        """
        Decide what to do on a 429.
        - Daily quota: blacklist the provider, return True (caller should retry)
        - RPM limit: promote to next provider, return True (caller should retry)
        - No more providers: return False (caller should backoff+retry)
        Returns False only when all providers are gone and caller must await backoff.
        """
        if self._is_daily_quota(exc):
            # Daily quota gone — remove this provider permanently and keep going
            self._blacklist_active()
            if self._providers:
                return True    # retry on whichever provider is now active
            return False       # no providers left at all — shouldn't happen
        # RPM/general rate-limit: try next provider in chain
        return self._promote()

    async def chat(self, messages, role=TaskRole.BALANCED, max_tokens=4096, temperature=0.1) -> ChatResponse:
        while True:
            try:
                result = await self._active.chat(messages, role=role, max_tokens=max_tokens, temperature=temperature)
                self._backoff_attempt = 0   # successful call — reset backoff counter
                return result
            except Exception as exc:
                if self._is_rate_limit(exc):
                    if self._handle_rate_limit(exc):
                        continue   # retry immediately on surviving provider
                    await self._backoff_and_reset()
                    continue
                raise

    async def chat_json(self, messages, role=TaskRole.BALANCED, max_tokens=4096) -> str:
        while True:
            try:
                result = await self._active.chat_json(messages, role=role, max_tokens=max_tokens)
                self._backoff_attempt = 0
                return result
            except Exception as exc:
                if self._is_rate_limit(exc):
                    if self._handle_rate_limit(exc):
                        continue
                    await self._backoff_and_reset()
                    continue
                raise

    async def chat_with_tools(self, messages, tools, role=TaskRole.BALANCED, max_tokens=2048) -> ChatResponse:
        while True:
            try:
                result = await self._active.chat_with_tools(messages, tools=tools, role=role, max_tokens=max_tokens)
                self._backoff_attempt = 0
                return result
            except Exception as exc:
                if self._is_rate_limit(exc):
                    if self._handle_rate_limit(exc):
                        continue
                    await self._backoff_and_reset()
                    continue
                raise


def get_provider() -> LLMProvider:
    """
    Returns the active LLM provider singleton (with fallback chain if configured).
    Creates it on first call (lazy init).
    """
    global _provider
    if _provider is None:
        _provider = _create_provider()
    return _provider


def _build_single_provider(provider_name: str) -> LLMProvider:
    """Build one concrete provider by name. Raises if key missing."""
    provider_name = provider_name.strip().lower()

    if provider_name == "ollama":
        from companybrain.llm.ollama_provider import OllamaProvider
        return OllamaProvider(host=settings.ollama_host)

    elif provider_name == "anthropic":
        if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-..."):
            raise ValueError("ANTHROPIC_API_KEY not configured")
        from companybrain.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=settings.anthropic_api_key)

    elif provider_name == "openai":
        if not settings.openai_api_key or settings.openai_api_key.startswith("sk-..."):
            raise ValueError("OPENAI_API_KEY not configured")
        from companybrain.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            name="openai",
        )

    elif provider_name == "groq":
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY not configured")
        from companybrain.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.groq_api_key,
            base_url=_GROQ_BASE_URL,
            name="groq",
            model_map=_GROQ_MODELS,
        )

    elif provider_name == "openrouter":
        if not settings.openrouter_api_key or settings.openrouter_api_key.startswith("sk-or-..."):
            raise ValueError("OPENROUTER_API_KEY not configured")
        from companybrain.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openrouter_api_key,
            base_url=_OPENROUTER_BASE_URL,
            name="openrouter",
            model_map=_OPENROUTER_MODELS,
        )

    else:
        raise ValueError(f"Unknown provider '{provider_name}'")


def _create_provider() -> LLMProvider:
    """
    Build the active provider, optionally wrapped in a FallbackProvider chain.

    Primary provider:  LLM_PROVIDER env var (e.g. groq)
    Fallback chain:    LLM_FALLBACK_CHAIN env var — comma-separated list of
                       additional providers to try when the primary 429s.
                       Example: LLM_FALLBACK_CHAIN=openrouter,anthropic

    Providers that are missing their API key are silently skipped.
    """
    import os
    primary_name = settings.llm_provider.lower()
    log.info("Initialising LLM provider", provider=primary_name)

    # Build primary — error if it can't be created (user must fix config)
    primary = _build_single_provider(primary_name)

    # Build fallback chain — skip providers with missing keys (don't error)
    fallback_names_raw = os.environ.get("LLM_FALLBACK_CHAIN", "").strip()
    fallback_providers: list[LLMProvider] = []
    if fallback_names_raw:
        for fb_name in fallback_names_raw.split(","):
            fb_name = fb_name.strip()
            if not fb_name or fb_name == primary_name:
                continue
            try:
                fb = _build_single_provider(fb_name)
                fallback_providers.append(fb)
                log.info("Fallback provider registered", provider=fb_name)
            except ValueError as e:
                log.debug("Skipping fallback provider (key missing)", provider=fb_name, reason=str(e))

    if not fallback_providers:
        return primary

    log.info(
        "OpenAIProvider initialised",
        provider=primary_name,
        base_url=getattr(primary, "_client", None) and getattr(primary._client, "base_url", "?"),
        models={r.value: primary.model_for_role(r) for r in TaskRole},
    )
    return FallbackProvider([primary] + fallback_providers)


def reset_provider() -> None:
    """Force re-creation of the provider (useful in tests after changing settings)."""
    global _provider
    _provider = None
