"""
Intent router — ADR-0043 WS2.

Classifies a natural-language query into one of five intents so that
downstream components (HybridSearcher index selection, per-intent prompt
templates, SmartZone policy) can be tuned to the question type.

Intents
-------
call_chain   — "who calls X?", "trace the flow from A to B"
data_flow    — "what columns does X read?", "how does Y get persisted?"
change_risk  — "what breaks if I rename Z?", "blast radius of deleting W"
concept      — "what does X do?", "explain Y", "how does the auth system work?"
other        — anything that doesn't match the above

Cost: one fast-model LLM call per unique (question_hash, workspace_id).
Cached in-process with a configurable TTL so repeated identical queries skip
the classification round-trip.
"""
from __future__ import annotations

import hashlib
import time
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

Intent = Literal["call_chain", "data_flow", "change_risk", "concept", "other"]

INTENTS: tuple[Intent, ...] = ("call_chain", "data_flow", "change_risk", "concept", "other")

ROUTER_SYSTEM_PROMPT = """\
You are a query intent classifier for a codebase knowledge graph.

Classify the user's question into EXACTLY ONE of the following intents:

  call_chain   — The question asks about execution flow, call paths, or
                 which component invokes which. Examples: "who calls X",
                 "trace the flow from A to B", "what triggers Y".

  data_flow    — The question asks about data access, persistence, column
                 reads/writes, or how a value is stored/fetched. Examples:
                 "what columns does X read", "how is Y stored", "which
                 query fetches Z".

  change_risk  — The question asks about impact, blast radius, or what
                 would break. Examples: "what breaks if I rename X",
                 "blast radius of deleting Y", "is it safe to change Z".

  concept      — The question asks for an explanation, overview, or
                 business meaning. Examples: "what does X do", "explain
                 how auth works", "describe the payment flow".

  other        — Anything that doesn't clearly fit the above.

Respond with ONLY the intent label — a single word, no punctuation, no
explanation. The valid labels are: call_chain, data_flow, change_risk,
concept, other.
"""


# ── In-process intent cache ───────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("intent", "expires_at")

    def __init__(self, intent: Intent, ttl_sec: int):
        self.intent = intent
        self.expires_at = time.monotonic() + ttl_sec


_CACHE: dict[str, _CacheEntry] = {}


def _cache_key(question: str, workspace_id: str) -> str:
    h = hashlib.sha256(f"{workspace_id}::{question}".encode()).hexdigest()[:16]
    return h


def _get_cached(key: str) -> Intent | None:
    entry = _CACHE.get(key)
    if entry and time.monotonic() < entry.expires_at:
        return entry.intent
    if key in _CACHE:
        del _CACHE[key]
    return None


def _set_cached(key: str, intent: Intent, ttl_sec: int) -> None:
    # Evict old entries when cache grows (simple LRU approximation — evict oldest 20%)
    if len(_CACHE) > 500:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1].expires_at)
        for k, _ in oldest[: len(_CACHE) // 5]:
            _CACHE.pop(k, None)
    _CACHE[key] = _CacheEntry(intent, ttl_sec)


# ── Public API ────────────────────────────────────────────────────────────────

async def classify_intent(
    question: str,
    *,
    workspace_id: str,
    ttl_sec: int = 3600,
) -> Intent:
    """Classify `question` into one of the five intents.

    Uses the fast LLM model (haiku / 8b) for cheap classification.
    Results are cached by (sha256(workspace_id::question), workspace_id)
    for `ttl_sec` seconds (default 1 hour).

    Never raises — falls back to 'concept' on LLM error so the query
    pipeline can still proceed.
    """
    key = _cache_key(question, workspace_id)
    cached = _get_cached(key)
    if cached is not None:
        log.debug("intent_router.cache_hit", intent=cached)
        return cached

    try:
        intent = await _call_llm(question)
    except Exception as exc:
        log.warning("intent_router.classify_failed (falling back to 'concept')",
                    error=str(exc))
        return "concept"
    _set_cached(key, intent, ttl_sec)
    return intent


async def _call_llm(question: str) -> Intent:
    try:
        from companybrain.llm import get_provider, TaskRole, ChatMessage
        provider = get_provider()
        response = await provider.chat(
            messages=[
                ChatMessage(role="system", content=ROUTER_SYSTEM_PROMPT),
                ChatMessage(role="user",   content=question),
            ],
            role=TaskRole.FAST,
            max_tokens=10,
        )
        raw = (response.content or "").strip().lower().split()[0] if response.content else ""
        intent: Intent = raw if raw in INTENTS else "concept"  # type: ignore[assignment]
        log.info("intent_router.classified", intent=intent, question=question[:80])
        return intent
    except Exception as exc:
        log.warning("intent_router.llm_failed (falling back to 'concept')",
                    error=str(exc))
        return "concept"
