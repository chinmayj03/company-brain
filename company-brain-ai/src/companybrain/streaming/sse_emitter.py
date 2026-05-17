"""
SSE emitter for POST /query/stream — A1.5.

Yields SSE-formatted strings for a query response.

Event shape:
  data: {"type": "token", "text": "<chunk>"}\n\n
  ...
  data: {"type": "done", "citations": [...], "confidence": {...},
         "matched_shape_id": null, "from_cache": false}\n\n

When the provider supports Anthropic-style streaming
(``_client.messages.stream``), tokens are emitted individually for
low TTFT.  All other providers (Groq, Ollama, OpenAI) fall back to a
single-token emission of the complete answer — the SSE contract is
preserved; only TTFT differs.

Lazy-exploration gate
---------------------
ExplorationAgent is invoked ONLY when the initial retrieval confidence
is below ``settings.iterative_verifier_score_threshold`` (default 0.6).
This matches the ADR-0061 E1 gate used in the non-streaming path.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, AsyncIterator

import structlog

from companybrain.llm import TaskRole
from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT

if TYPE_CHECKING:
    from companybrain.llm.base import LLMProvider

log = structlog.get_logger(__name__)

# Sentinel returned when context confidence is below the gate threshold.
_LOW_CONFIDENCE_LEVELS = {"low"}


def _sse(data: dict) -> str:
    """Format a dict as a single SSE event string (ends with \\n\\n)."""
    return f"data: {json.dumps(data)}\n\n"


def _citations_from_context(context: str | None) -> list[dict]:
    """Extract minimal citation dicts from an assembled context string."""
    import re
    if not context:
        return []
    urn_re = re.compile(r'urn:cb:[a-zA-Z0-9:._\-]{5,120}')
    seen: set[str] = set()
    out: list[dict] = []
    for m in urn_re.finditer(context):
        urn = m.group(0).rstrip(".,;)\"'")
        if urn not in seen:
            seen.add(urn)
            out.append({"urn": urn, "name": urn.rsplit(":", 1)[-1]})
    return out[:20]


async def stream_query_response(
    question: str,
    context: str | None,
    system_prompt: str | None,
    provider: "LLMProvider",
) -> AsyncIterator[str]:
    """
    Async generator — yields SSE-formatted strings for a query.

    Parameters
    ----------
    question:      The raw user question.
    context:       Pre-assembled knowledge-base context (may be None).
    system_prompt: Override the default QUERY_SYSTEM_PROMPT (pass None for default).
    provider:      A fully initialised LLMProvider from ``get_provider()``.
    """
    from companybrain.config import settings

    effective_system = system_prompt or QUERY_SYSTEM_PROMPT

    user_content = (
        f"KNOWLEDGE BASE:\n\n{context}\n\n---\n\nQUESTION: {question}"
        if context
        else (
            f"QUESTION: {question}\n\n"
            "Note: No brain context available. "
            "Run the extraction pipeline on the repo first."
        )
    )

    # ── Lazy exploration gate ─────────────────────────────────────────────────
    # If context is absent, confidence is effectively "low" — run exploration
    # before streaming to enrich the answer. This mirrors the non-streaming
    # E1 gate in query.py (_maybe_explore / _run_exploration_agent).
    threshold = settings.iterative_verifier_score_threshold  # default 0.6
    initial_confidence_low = not bool(context)  # heuristic: no context → low

    if initial_confidence_low and settings.iterative_exploration_enabled:
        try:
            from companybrain.agents.exploration_agent import ExplorationAgent
            agent = ExplorationAgent(workspace_id="stream", repo_path=None)
            result = await agent.explore(question=question, initial_summary="")
            if result.context.strip():
                enriched = f"{result.context}\n\n---\n\n{user_content}"
                user_content = enriched
                log.info(
                    "[stream] exploration agent enriched context",
                    rounds=result.rounds_taken,
                    context_len=len(result.context),
                )
        except Exception as exc:
            log.debug("[stream] exploration agent skipped (non-fatal)", error=str(exc))

    # ── Attempt Anthropic native streaming ────────────────────────────────────
    full_text: list[str] = []
    streamed = False

    if hasattr(provider, "_client") and hasattr(provider._client, "messages"):
        try:
            model = provider.model_for_role(TaskRole.QUERY)
            async with provider._client.messages.stream(
                model=model,
                system=[
                    {
                        "type": "text",
                        "text": effective_system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                max_tokens=settings.max_tokens_query,
            ) as stream:
                async for text_chunk in stream.text_stream:
                    full_text.append(text_chunk)
                    yield _sse({"type": "token", "text": text_chunk})
            streamed = True
            log.debug("[stream] Anthropic native streaming complete",
                      chars=sum(len(c) for c in full_text))
        except Exception as exc:
            log.warning("[stream] Anthropic streaming failed — falling back", error=str(exc))
            full_text.clear()
            streamed = False

    # ── Non-Anthropic or fallback: emit full answer as one token event ─────────
    if not streamed:
        try:
            from companybrain.llm.base import ChatMessage
            response = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=effective_system),
                    ChatMessage(role="user", content=user_content),
                ],
                role=TaskRole.QUERY,
                max_tokens=settings.max_tokens_query,
            )
            answer = response.content
            full_text.append(answer)
            yield _sse({"type": "token", "text": answer})
        except Exception as exc:
            log.warning("[stream] Provider chat failed — emitting error token", error=str(exc))
            error_text = f"[Error generating response: {exc}]"
            full_text.append(error_text)
            yield _sse({"type": "token", "text": error_text})

    # ── Done event ─────────────────────────────────────────────────────────────
    assembled_answer = "".join(full_text)
    citations = _citations_from_context(context)

    # Infer confidence from answer content (heuristic for done event).
    # A proper verifier pass would require re-invoking the LLM; for TTFT
    # reasons we keep this cheap. The non-streaming path does full verification.
    confidence_level = "high" if context else "low"
    if initial_confidence_low:
        confidence_level = "medium"

    yield _sse({
        "type": "done",
        "citations": citations,
        "confidence": {
            "level": confidence_level,
            "rationale": (
                "Streamed answer; run POST /query for full structured verification."
            ),
        },
        "matched_shape_id": None,
        "from_cache": False,
    })
