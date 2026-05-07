"""
Provider-aware concurrency configuration for pipeline stages.

Concurrency rules:
  - Ollama      → 1  (single-threaded local GPU)
  - Groq free   → 1  (RPM/TPM limits hit immediately at concurrency > 1 with
                       method-level chunking; sequential is faster overall)
  - OpenAI      → settings.max_entity_extraction_concurrency  (default 10)
  - Anthropic   → settings.max_entity_extraction_concurrency  (default 10)
  - OpenRouter  → 2  (shared rate limits; be conservative)

Why Groq = 1:
  Groq free tier: ~14k TPM for llama-3.1-8b-instant.
  A single 5-unit pipeline with method chunking generates 20–50 sub-requests.
  At concurrency=3, the first minute fills the token budget and every subsequent
  call rate-limits, causing RetryError chains.  Sequential (concurrency=1) paces
  naturally and completes faster than recovering from cascading 429s.

Usage:
    sem = get_extraction_semaphore()
    async with sem:
        entities = await extractor._extract_from_code_unit(...)

The semaphore is not a singleton — create one per pipeline run so that
concurrent pipeline jobs don't share limits.
"""
from __future__ import annotations

import asyncio

from companybrain.config import settings

# Providers that must run sequentially due to tight rate limits
_SEQUENTIAL_PROVIDERS = frozenset({"ollama", "groq"})
# Providers that are rate-limited but can handle light parallelism
_LOW_CONCURRENCY_PROVIDERS = frozenset({"openrouter"})
_LOW_CONCURRENCY = 2


def get_extraction_concurrency() -> int:
    """
    Return the max concurrent LLM extraction calls for the active provider.
    """
    provider = settings.llm_provider.lower()
    if provider in _SEQUENTIAL_PROVIDERS:
        return 1
    if provider in _LOW_CONCURRENCY_PROVIDERS:
        return _LOW_CONCURRENCY
    return settings.max_entity_extraction_concurrency


def get_extraction_semaphore() -> asyncio.Semaphore:
    """Return a fresh asyncio.Semaphore sized for the current provider."""
    return asyncio.Semaphore(get_extraction_concurrency())


def get_synthesis_concurrency() -> int:
    """Same logic, but for context synthesis (Stage 3)."""
    provider = settings.llm_provider.lower()
    if provider in _SEQUENTIAL_PROVIDERS:
        return 1
    if provider in _LOW_CONCURRENCY_PROVIDERS:
        return 1
    return settings.max_context_synthesis_concurrency


def is_parallel_safe() -> bool:
    """Returns True when the provider supports concurrent LLM calls safely."""
    return settings.llm_provider.lower() not in _SEQUENTIAL_PROVIDERS
