"""
POST /query — answer a natural language question using tiered graph context + LLM.

Architecture (ADR-0018: Smart-zone context assembler + ADR-0043: intent router,
              response layer):

  0. IntentRouter.classify_intent() → one of {call_chain, data_flow,
     change_risk, concept, other} (fast model, cached).
  1. SmartZoneAssembler.assemble() classifies the task, runs hybrid retrieval
     (ADR-0015) on the intent-appropriate index, expands via Neo4j blast-radius,
     MMR-reranks, tiers into T0/T1/T2, compresses task-aware, renders context.
  2. Build the per-intent user message from prompts/user_message.py.
  3. Call the LLM, parse the JSON response into a typed QueryResponse.
  4. Populate raw_markdown via the markdown renderer and return.

Fallback: if SmartZoneAssembler is unavailable (missing .brain/, Neo4j down),
          falls back to the legacy Java-assembler or hybrid-retrieval path and
          wraps the free-form answer in a minimal QueryResponse envelope.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

import httpx

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import QueryRequest
from companybrain.agents.exploration_agent import ExplorationAgent
from companybrain.models.query_response import (
    Confidence,
    QueryResponse,
    Citation,
)
from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT
from companybrain.api.responses.markdown_renderer import render_to_markdown

router = APIRouter()
log = structlog.get_logger(__name__)

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://localhost:8080")
INTERNAL_KEY = os.environ.get("AI_INTERNAL_KEY", "dev-internal-key")

# ── A1.3: LRU query result cache ──────────────────────────────────────────────
# Module-level singleton; lazily initialised on first request so config.py
# values (including env-var overrides) are available at that point.
# Import is deferred to avoid a circular-import at module load time.
_query_result_cache = None


def _get_query_cache():
    """Return the process-level QueryResultCache singleton (lazy init)."""
    global _query_result_cache
    if _query_result_cache is None:
        from companybrain.cache.query_cache import get_query_cache
        _query_result_cache = get_query_cache()
    return _query_result_cache

# Intent → Qdrant index mapping for intent-aware retrieval (ADR-0043 WS2)
_INTENT_TO_INDEX: dict[str, str] = {
    "call_chain":  "default",   # per-entity BM25+dense RRF; needs full graph
    "data_flow":   "code",      # code/signature collection for SQL queries
    "change_risk": "default",   # full graph to trace blast radius
    "concept":     "business",  # business context collection for semantic search
    "other":       "default",
}

# ── Neo4j connection singleton (B6 latency fix) ────────────────────────────────
# Creating a new AsyncGraphDatabase.driver per query adds 5-10s overhead
# (TCP + TLS handshake each time). A module-level singleton reuses the
# connection across requests; driver.close() is called only on app shutdown.
_neo4j_driver: Any | None = None


def _get_neo4j_driver() -> Any:
    """Return a module-level Neo4j async driver, creating it on first call."""
    global _neo4j_driver
    if _neo4j_driver is None:
        try:
            from neo4j import AsyncGraphDatabase
            neo4j_url  = os.environ.get("NEO4J_URL",      "bolt://localhost:7687")
            neo4j_user = os.environ.get("NEO4J_USER",     "neo4j")
            neo4j_pass = os.environ.get("NEO4J_PASSWORD", "password")
            _neo4j_driver = AsyncGraphDatabase.driver(
                neo4j_url, auth=(neo4j_user, neo4j_pass)
            )
        except Exception:  # noqa: BLE001 — neo4j may not be installed
            pass
    return _neo4j_driver


# ── Main handler ──────────────────────────────────────────────────────────────

@router.post("", response_model=QueryResponse)
async def query_graph(
    request: QueryRequest,
    persona: str | None = Query(
        default=None,
        description=(
            "ADR-0079 P1 — Persona hint for shaped answer formatting. "
            "One of: dev | pm | vp_eng | cs | cfo | ceo. "
            "When omitted the router infers from query text or workspace config."
        ),
    ),
):
    """
    POST /query

    0. IntentRouter classifies the question (ADR-0043 WS2)
    0b. ADR-0061 E5 ambiguity check — bail out with clarification chips if the
        question is ambiguous and no ``interpret`` hint was supplied.
    1. SmartZoneAssembler.assemble() builds T0/T1/T2 tiered context (ADR-0018)
    2. Per-intent user message is rendered (ADR-0043 WS2)
    3. Call LLM with structured system prompt (ADR-0043)
    4. Parse typed QueryResponse
    4d. ADR-0061 E1 — fire ExplorationAgent on low-confidence answers.
    4e. ADR-0061 E2 — re-read source for shaky citations.
    4f. ADR-0061 E6 — surface cross-repo similarity insights.
    5. ADR-0079 P1 — Persona routing + shaped answer formatting.
    6. Return structured response
    """
    from companybrain.config import settings

    # ── Step 0b: ADR-0061 E5 ambiguity check ──────────────────────────────────
    # When the request carries ``interpret`` we trust the client and skip; when
    # the agent is firing nested we also skip (avoids interactive loops).
    _is_nested = bool(getattr(request, "_no_iterative", False))
    if not request.interpret and not _is_nested:
        try:
            from companybrain.api.routes.clarification import detect_ambiguity
            clarification = detect_ambiguity(
                request.question,
                repo_path=getattr(request, "repo_path", None),
                workspace_id=str(request.workspace_id),
            )
            if clarification.ambiguous:
                log.info("[query] Returning clarification request",
                         term=clarification.term,
                         n_options=len(clarification.interpretations or []))
                resp = QueryResponse(
                    summary=(
                        f"The term '{clarification.term}' is ambiguous — please pick "
                        "an interpretation."
                    ),
                    confidence=Confidence(level="low",
                                          rationale="ambiguous query — awaiting clarification"),
                )
                resp.ambiguity = True
                resp.interpretations = clarification.interpretations or []
                resp.suggested_followup = clarification.suggested_followup
                resp.telemetry = {"clarification_returned": True}
                return resp
        except Exception as exc:
            log.debug("[query] Ambiguity detector failed (non-fatal)",
                      error=str(exc))

    # ── A1.3: LRU query result cache lookup ───────────────────────────────────
    # Check before t0 so cache hits don't appear in latency telemetry.
    if settings.query_cache_enabled:
        _cached = _get_query_cache().get(request.question, str(request.workspace_id))
        if _cached is not None:
            log.info("[query] Cache hit — returning cached result",
                     workspace_id=str(request.workspace_id))
            return _cached

    t0 = time.monotonic()
    provider = get_provider()

    # ── Steps 0 + 1: Intent classification AND SmartZone run in parallel ──────
    # Intent classification is a fast cached LLM call (~200ms); SmartZone is a
    # Neo4j + Qdrant retrieval (~2-4s). Running them concurrently saves the
    # full intent-classification time on the critical path (B6 latency fix).
    # SmartZone needs the intent-derived Qdrant index, so we run it with the
    # default "default" index first, then re-run only if intent changes the
    # index meaningfully — in practice, 80% of queries use "default".

    async def _classify() -> str:
        if settings.skip_intent_router:
            return "concept"
        try:
            from companybrain.api.intent_router import classify_intent
            return await classify_intent(
                request.question,
                workspace_id=str(request.workspace_id),
                ttl_sec=settings.brain_query_cache_ttl_sec,
            )
        except Exception as exc:
            log.warning("[query] Intent router failed (using 'concept')", error=str(exc))
            return "concept"

    # Fire both concurrently with the default index; accept a possible
    # index mismatch on the first try (correct in the retry below if needed).
    intent, (assembled_context, smart_zone_meta) = await asyncio.gather(
        _classify(),
        _smart_zone_assemble(
            task=request.question,
            workspace_id=str(request.workspace_id),
            repo_path=getattr(request, "repo_path", None),
            qdrant_index="default",
        ),
    )

    log.info("[query] Intent classified", intent=intent)
    qdrant_index = _INTENT_TO_INDEX.get(intent, "default")

    # If intent routing wants a non-default index and SmartZone returned nothing
    # (common on first call before data is indexed), try once more with the
    # correct index.  This costs one extra query only on the miss path.
    if qdrant_index != "default" and not assembled_context:
        assembled_context, smart_zone_meta = await _smart_zone_assemble(
            task=request.question,
            workspace_id=str(request.workspace_id),
            repo_path=getattr(request, "repo_path", None),
            qdrant_index=qdrant_index,
        )

    # ── Step 1b: Legacy Java assembler fallback ───────────────────────────────
    if not assembled_context:
        focal_node_id: str | None = None
        search_symbol = request.context_symbol or _symbol_from_file(request.file_path)
        if search_symbol:
            focal_node_id, _ = await _resolve_node(
                request.workspace_id, search_symbol
            )
        if focal_node_id:
            assembled_context, _ = await _assemble_context(
                workspace_id=str(request.workspace_id),
                focal_node_id=focal_node_id,
                question=request.question,
                max_hops=request.max_hops,
            )

    # ── Step 1c: Hybrid retrieval fallback (ADR-0015 / A1.2 pipeline) ──────────
    retrieval_score: float = 0.0
    if not assembled_context:
        # ADR-0015 A1.2: try the full BM25+dense+RRF+BGE pipeline first.
        assembled_context, retrieval_score = await _hybrid_retrieve_v2(
            request.question,
            str(request.workspace_id),
            getattr(request, "repo_path", None),
            index=qdrant_index,
            include_unverified=getattr(request, "include_unverified", False),
        )
        if not assembled_context:
            # Fall back to legacy path when v2 pipeline is unavailable.
            assembled_context = await _hybrid_retrieve(
                request.question,
                request.workspace_id,
                getattr(request, "repo_path", None),
                index=qdrant_index,
                include_unverified=getattr(request, "include_unverified", False),
            )
        if assembled_context:
            log.info("[query] Using hybrid retrieval context (SmartZone + Java unavailable)",
                     intent=intent, index=qdrant_index, retrieval_score=round(retrieval_score, 4))

    # ── Step 2: Build per-intent user message (ADR-0043 WS2) ─────────────────
    try:
        from companybrain.api.prompts.user_message import build_user_message
        user_content = build_user_message(
            request.question,
            intent=intent,
            context=assembled_context,
        )
    except Exception as exc:
        log.warning("[query] Per-intent template failed (using plain fallback)",
                    error=str(exc))
        user_content = _plain_user_message(request.question, assembled_context)

    # ADR-0061 E5: when the client carries an interpretation choice, prepend a
    # one-line hint so the LLM scopes its answer accordingly.
    if request.interpret:
        try:
            from companybrain.api.routes.clarification import interpretation_hint
            hint = interpretation_hint(request.interpret, request.question)
            if hint:
                user_content = f"{hint}\n\n{user_content}"
        except Exception:
            pass

    # ── Step 3: Call LLM (single-pass or iterative) ──────────────────────────
    t1 = time.monotonic()
    log.info("[query] Calling LLM",
             intent=intent,
             iterative=settings.iterative_exploration_enabled,
             task_type=smart_zone_meta.get("task_type"),
             tokens_used=smart_zone_meta.get("tokens_used", 0),
             context_available=bool(assembled_context))

    if settings.iterative_exploration_enabled:
        # ADR-0079 P1: infer persona early so the iterative loop can use it.
        _inferred_persona = persona or settings.persona_default
        if settings.persona_templates_enabled:
            try:
                from companybrain.personas.router import infer_persona
                _inferred_persona, _ = infer_persona(request.question, persona)
            except Exception:
                pass
        query_response, _iter_telemetry = await _iterative_answer(
            question=request.question,
            context=assembled_context,
            workspace_id=str(request.workspace_id),
            repo_path=getattr(request, "repo_path", None),
            qdrant_index=qdrant_index,
            persona=_inferred_persona,
        )
    else:
        response = await provider.chat(
            messages=[
                ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
                ChatMessage(role="user",   content=user_content),
            ],
            role=TaskRole.QUERY,
            max_tokens=4096,
        )
        query_response = _parse_llm_response(response.content, assembled_context)
        _iter_telemetry = {}
        # ── A1.4: attach deterministic multi-signal confidence (non-iterative path)
        try:
            from companybrain.confidence.helpers import build_confidence_from_query_result
            query_response = query_response.model_copy(update={
                "confidence": build_confidence_from_query_result(
                    query_response,
                    retrieval_score=0.0,  # not available on non-iterative path
                    source_paths=None,
                    verifier_score=None,  # verifier not run on single-pass
                )
            })
        except Exception as _conf_exc:
            log.debug("[query] confidence aggregation skipped (non-fatal)", error=str(_conf_exc))

    llm_dur = int((time.monotonic() - t1) * 1000)

    # ── Step 3b: propagate retrieval_score into telemetry (ADR-0015 A1.2) ────
    # Downstream systems (dashboards, evaluation harness) can read this from
    # the telemetry dict without needing to re-run retrieval.
    if retrieval_score > 0.0:
        query_response.telemetry = dict(query_response.telemetry or {})
        query_response.telemetry["retrieval_score"] = round(retrieval_score, 4)

    # ── Step 4: (response already typed) ─────────────────────────────────────

    # ── Step 4a: ADR-0061 E1 — Exploration Agent on low-confidence answers ───
    # When the initial answer has low confidence, spawn ExplorationAgent to
    # gather additional context, then re-run the LLM with the enriched prompt.
    # This is a transparent post-processor: the caller always gets one response.
    if query_response.confidence.level == "low":
        query_response = await _run_exploration_agent(
            request=request,
            initial_response=query_response,
            assembled_context=assembled_context,
            user_content=user_content,
            intent=intent,
            provider=provider,
        )

    # ── Step 4b: Surface per-entity sticky notes (ADR-0052 P6) ───────────────
    await _attach_notes(query_response, str(request.workspace_id))

    # ── Step 4d: ADR-0061 E1 — fire ExplorationAgent on low-confidence ──────
    if not _is_nested:
        query_response = await _maybe_explore(
            request=request,
            response=query_response,
            zone_tokens_used=smart_zone_meta.get("tokens_used", 0),
        )

    # ── Step 4e: ADR-0061 E2 — re-read source for shaky citations ───────────
    if not _is_nested:
        try:
            from companybrain.api.routes.query_reread import maybe_reread
            query_response = await maybe_reread(
                question=request.question,
                response=query_response,
                workspace_id=str(request.workspace_id),
                repo_path=getattr(request, "repo_path", None),
            )
        except Exception as exc:
            log.debug("[query] Re-read step failed (non-fatal)", error=str(exc))

    # ── Step 4f: ADR-0061 E6 — cross-repo similarity insights ───────────────
    if not _is_nested:
        _attach_cross_repo_insights(query_response, str(request.workspace_id))

    # ── Step 4c: Surface ADR-0059 risk alerts + domains + onboarding paths ──
    # Read derived entities from .brain/ and attach to the response so the
    # frontend / SDK can render the Risk Dashboard (Product 2) and the
    # onboarding curriculum without a second round-trip.
    _attach_adr_0059_derivatives(query_response, getattr(request, "repo_path", None))

    # ── Step 5: Render markdown blob ──────────────────────────────────────────
    render_to_markdown(query_response)
    # ADR-0049 O5a-5: expose raw markdown without double-encoding.
    if query_response.summary_md is None:
        query_response.summary_md = query_response.raw_markdown or query_response.summary

    # ── Step 5b: ADR-0079 P1 — Persona routing + shaped answer formatting ───────
    if settings.persona_templates_enabled:
        query_response = _apply_persona_formatting(
            question=request.question,
            persona_param=persona,
            query_response=query_response,
        )

    # ── A1.3: Store result in LRU cache ───────────────────────────────────────
    if settings.query_cache_enabled:
        _conf_level = getattr(query_response.confidence, "level", "low")
        if _conf_level in ("high", "medium"):
            _get_query_cache().put(
                request.question, str(request.workspace_id), query_response
            )

    # ── Step 6: Persist conversation (ADR-0072 A1/A2/A5) ─────────────────────
    await _persist_conversation(
        workspace_id=str(request.workspace_id),
        question=request.question,
        query_response=query_response,
        actor_id=getattr(request, "actor_id", None),
        actor_kind=getattr(request, "actor_kind", "user"),
    )

    dur = int((time.monotonic() - t0) * 1000)
    log.info(
        "[query] OK",
        intent=intent,
        confidence=query_response.confidence.level,
        affected_count=len(query_response.affected_entities),
        call_chain_len=len(query_response.call_chain),
        llm_ms=llm_dur,
        total_ms=dur,
    )
    return query_response


# ── SSE streaming endpoint (ADR-0049 O5) ─────────────────────────────────────

@router.post("/stream")
async def query_graph_stream(request: QueryRequest):
    """
    POST /query/stream — identical to POST /query but streams the LLM response
    as Server-Sent Events so the UI can show the first tokens in ~600ms rather
    than waiting 4-8 seconds for the full response.

    SSE format:
        data: {"delta": "<text chunk>"}\n\n
        ...
        data: [DONE]\n\n

    The client-side change to consume the stream is out of scope for ADR-0049.
    """
    from companybrain.config import settings as _s

    provider = get_provider()

    intent = "concept"
    if not _s.skip_intent_router:
        try:
            from companybrain.api.intent_router import classify_intent
            intent = await classify_intent(
                request.question,
                workspace_id=str(request.workspace_id),
                ttl_sec=_s.brain_query_cache_ttl_sec,
            )
        except Exception:
            pass

    qdrant_index = _INTENT_TO_INDEX.get(intent, "default")
    assembled_context, _ = await _smart_zone_assemble(
        task=request.question,
        workspace_id=str(request.workspace_id),
        repo_path=getattr(request, "repo_path", None),
        qdrant_index=qdrant_index,
    )

    try:
        from companybrain.api.prompts.user_message import build_user_message
        user_content = build_user_message(request.question, intent=intent, context=assembled_context)
    except Exception:
        user_content = _plain_user_message(request.question, assembled_context)

    async def _event_stream():
        try:
            async with provider._client.messages.stream(
                model=provider.model_for_role(TaskRole.QUERY),
                system=[{
                    "type": "text",
                    "text": QUERY_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_content}],
                max_tokens=4096,
            ) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps({'delta': text})}\n\n"
        except Exception as exc:
            log.warning("[query/stream] Stream error", error=str(exc))
        yield "data: [DONE]\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


# ── ADR-0061 E1: Exploration Agent post-processor ─────────────────────────────

async def _run_exploration_agent(
    request,
    initial_response: QueryResponse,
    assembled_context: str | None,
    user_content: str,
    intent: str,
    provider=None,
) -> QueryResponse:
    """
    Fire ExplorationAgent when the initial answer has low confidence.

    1. Run ExplorationAgent to gather additional context (max 3 rounds / $0.10).
    2. Re-run the LLM with original context + exploration findings prepended.
    3. Return the enriched QueryResponse with telemetry fields set.

    Falls back to the initial_response on any error so /query stays available.
    """
    try:
        repo_path    = getattr(request, "repo_path", None)
        workspace_id = str(request.workspace_id)

        agent = ExplorationAgent(
            repo_path=repo_path,
            workspace_id=workspace_id,
        )

        log.info(
            "[query/exploration] Low confidence — starting exploration",
            workspace_id=workspace_id,
            question=request.question[:100],
        )

        result = await agent.explore(
            question=request.question,
            initial_summary=initial_response.summary,
        )

        log.info(
            "[query/exploration] Exploration complete",
            rounds=result.rounds_taken,
            tool_calls=result.tool_calls_made,
            context_len=len(result.context),
        )

        if not result.context.strip():
            # Nothing found — return original with telemetry flag
            initial_response.telemetry = {
                "exploration_agent_invoked": True,
                "exploration_rounds": result.rounds_taken,
                "exploration_tool_calls": result.tool_calls_made,
                "exploration_context_empty": True,
            }
            return initial_response

        # Re-run LLM with enriched context
        enriched_content = (
            f"{result.context}\n\n"
            f"---\n\n"
            f"{user_content}"
        )

        _provider = provider or get_provider()
        enriched_response = await _provider.chat(
            messages=[
                ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
                ChatMessage(role="user",   content=enriched_content),
            ],
            role=TaskRole.QUERY,
            max_tokens=4096,
        )

        new_response = _parse_llm_response(enriched_response.content, assembled_context)
        new_response.telemetry = {
            "exploration_agent_invoked": True,
            "exploration_rounds": result.rounds_taken,
            "exploration_tool_calls": result.tool_calls_made,
            "exploration_citations": result.citations,
        }
        return new_response

    except Exception as exc:
        log.warning("[query/exploration] Exploration failed (returning initial answer)",
                    error=str(exc))
        initial_response.telemetry = {
            "exploration_agent_invoked": False,
            "exploration_error": str(exc),
        }
        return initial_response


# ── LLM response parsing ──────────────────────────────────────────────────────

_URN_RE = re.compile(r'urn:cb:[a-zA-Z0-9:._\-]{5,120}')


def _citations_from_context(context: str | None, raw: str) -> list[Citation]:
    """Extract Citation objects from the assembled context and the LLM's raw text.

    When _parse_llm_response falls back to a free-form envelope it calls this to
    recover entity references so cited_entity_urns is never silently empty.
    """
    if not context:
        return []
    seen: set[str] = set()
    out: list[Citation] = []
    # Mine URNs from assembled context (authoritative — these were retrieved)
    for m in _URN_RE.finditer(context):
        urn = m.group(0).rstrip(".,;)\"'")
        if urn not in seen:
            seen.add(urn)
            name = urn.rsplit(":", 1)[-1]
            out.append(Citation(urn=urn, name=name, why_relevant="in retrieved context", confidence=0.7))
    # Also mine URNs the LLM cited inline in its prose response
    for m in _URN_RE.finditer(raw):
        urn = m.group(0).rstrip(".,;)\"'")
        if urn not in seen:
            seen.add(urn)
            name = urn.rsplit(":", 1)[-1]
            out.append(Citation(urn=urn, name=name, why_relevant="cited inline", confidence=0.9))
    return out[:20]


def _parse_llm_response(raw: str, context: str | None) -> QueryResponse:
    """
    Parse the LLM's JSON output into a typed QueryResponse.

    Falls back to a minimal envelope when the LLM returns free-form text,
    so existing consumers always receive the same schema.
    """
    # Strip markdown fences the LLM sometimes wraps around JSON.
    lines = raw.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    cleaned = "\n".join(lines).strip()

    try:
        data: dict[str, Any] = json.loads(cleaned)
        return QueryResponse(**data)
    except Exception as exc:
        log.warning("[query] LLM output was not valid QueryResponse JSON — wrapping",
                    error=str(exc), preview=raw[:200])
        citations = _citations_from_context(context, raw)
        return QueryResponse(
            summary=raw,
            confidence=Confidence(
                level="medium" if context else "low",
                rationale="LLM returned free-form text rather than structured JSON",
            ),
            affected_entities=citations,
        )


def _strip_uncited(text: str) -> str:
    """Drop code-shaped identifiers (CamelCase, camelCase) from sentences that
    lack a `[urn:...]` citation. Sentences containing a URN citation are kept verbatim.
    Suppresses LLM hallucinations of code names that weren't in retrieved context.
    """
    out_sentences: list[str] = []
    for sentence in text.split(". "):
        if "[urn:" in sentence:
            out_sentences.append(sentence)
            continue
        kept: list[str] = []
        for raw_token in sentence.split():
            stripped = raw_token.strip(",.;:!?()[]{}'\"")
            if not _looks_like_code_identifier(stripped):
                kept.append(raw_token)
        out_sentences.append(" ".join(kept))
    return ". ".join(out_sentences)


def _looks_like_code_identifier(token: str) -> bool:
    """True if token has internal capitalization (PaymentService, getFoo) — i.e.,
    a code identifier rather than a sentence-initial capitalized word."""
    if len(token) < 2 or not any(c.isalpha() for c in token):
        return False
    has_lower = any(c.islower() for c in token)
    has_upper_after_first = any(c.isupper() for c in token[1:])
    return has_lower and has_upper_after_first


def _plain_user_message(question: str, context: str | None) -> str:
    if context:
        return (
            f"KNOWLEDGE BASE:\n\n{context}\n\n"
            f"---\n\n"
            f"QUESTION: {question}"
        )
    return (
        f"QUESTION: {question}\n\n"
        f"Note: No brain context available. "
        f"Run the extraction pipeline on the repo first."
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _smart_zone_assemble(
    task: str, workspace_id: str, repo_path: str | None,
    qdrant_index: str = "default",
) -> tuple[str | None, dict]:
    """Run SmartZoneAssembler; return (rendered_str, meta_dict) or (None, {})."""
    try:
        from companybrain.assembly.smart_zone import SmartZoneAssembler
        from companybrain.assembly.types import TokenBudget
        from companybrain.store.json_store import JsonFileBrainStore

        brain_root_env = os.environ.get("BRAIN_ROOT", "")
        effective_root = repo_path or brain_root_env
        if not effective_root:
            return None, {}

        brain_root = Path(effective_root) / ".brain"
        if not brain_root.exists():
            log.debug("[query] .brain/ not found — skipping SmartZone", path=str(brain_root))
            return None, {}

        # Reuse the module-level driver instead of creating a new one per query.
        # A new driver adds ~5-10s (TCP+TLS handshake) to every request (B6 fix).
        driver = _get_neo4j_driver()
        if driver is None:
            return None, {}

        store  = JsonFileBrainStore(brain_root)

        assembler = SmartZoneAssembler(
            brain_root=brain_root,
            workspace_id=workspace_id,
            store=store,
            neo4j_driver=driver,
        )
        budget  = TokenBudget()
        # Pass intent-derived index hint to the assembler's searcher.
        payload = await assembler.assemble(
            task=task, budget=budget, qdrant_index=qdrant_index
        )
        # Do NOT close the shared driver here — it lives for the process lifetime.

        meta = {
            "task_type":   payload.task_type,
            "tokens_used": payload.tokens_used,
            "t0": payload.t0,
            "t1": payload.t1,
            "t2": payload.t2,
        }
        return payload.rendered if payload.rendered else None, meta

    except Exception as exc:
        log.warning("[query] SmartZone assembly failed (non-fatal)", error=str(exc))
        return None, {}


async def _resolve_node(workspace_id: str, symbol: str) -> tuple[str | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{BACKEND_URL}/v1/search",
                params={"q": symbol, "limit": 1},
                headers={"X-Internal-Key": INTERNAL_KEY},
            )
            if resp.status_code == 200:
                data = resp.json()
                nodes = data.get("nodes", [])
                if nodes:
                    node = nodes[0]
                    return str(node["id"]), node["name"]
    except Exception as exc:
        log.debug("[query] Node resolution failed (non-fatal)", error=str(exc))
    return None, None


async def _assemble_context(
    workspace_id: str,
    focal_node_id: str,
    question: str,
    max_hops: int,
) -> tuple[str | None, dict]:
    payload = {
        "workspaceId": workspace_id,
        "focalNodeId": focal_node_id,
        "question":    question,
        "maxHops":     max_hops,
        "tokenBudget": 4096,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/v1/internal/assemble-context",
                json=payload,
                headers={"X-Internal-Key": INTERNAL_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("contextText"), data
    except Exception as exc:
        log.warning("[query] Context assembly failed — falling back to no context",
                    error=str(exc), focal_node_id=focal_node_id)
        return None, {}


# ADR-0056: which ``verified`` statuses are surfaced to /query by default.
_VERIFIED_PUBLIC = {"confirmed", "fuzzy", "skipped"}


async def _hybrid_retrieve(
    question: str, workspace_id: str, repo_path: str | None = None,
    index: str = "default",
    include_unverified: bool = False,
) -> str | None:
    """Retrieve relevant entities via HybridSearcher as a fallback context source.

    Resolves brain_root from (in order):
      1. Caller-supplied repo_path (typically request.repo_path)
      2. BRAIN_ROOT env var

    Returns None if neither is set OR neither contains a usable .brain/ directory.

    ADR-0056: hits with ``payload.verified`` in {"hallucinated", "conflicting"}
    are dropped unless ``include_unverified=True``. Entities indexed before the
    verifier rolled out have no ``verified`` key and default to ``skipped``,
    which is allowed through.
    """
    try:
        from companybrain.retrieval.hybrid_search import HybridSearcher
        from companybrain.store.identity import workspace_slug_for

        effective_root = repo_path or os.environ.get("BRAIN_ROOT", "")
        if not effective_root:
            return None
        brain_root = Path(effective_root)
        workspace_slug = workspace_slug_for(workspace_id)
        searcher = HybridSearcher(brain_root=brain_root, workspace_slug=workspace_slug)
        hits = searcher.search(question, top_k=10, index=index)
        if not hits:
            return None
        filtered_hits = _filter_verified(hits, include_unverified=include_unverified)
        if not filtered_hits:
            return None
        lines = ["## Hybrid Retrieval Results\n"]
        for hit in filtered_hits:
            payload = hit.payload
            name = payload.get("qualified_name") or hit.urn.split(":")[-1]
            summary = payload.get("t1_summary", "")
            lines.append(f"- **{name}** ({payload.get('entity_type', '')}): {summary}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("[query] Hybrid retrieval failed (non-fatal)", error=str(exc))
        return None


def _filter_verified(hits, include_unverified: bool):
    """ADR-0056: drop hallucinated/conflicting hits from /query unless the
    caller explicitly opts in. Public so tests can exercise the filter with
    fabricated hit lists."""
    if include_unverified:
        return list(hits)
    kept = []
    for hit in hits:
        payload = getattr(hit, "payload", {}) or {}
        verified = payload.get("verified", "skipped")
        if verified in _VERIFIED_PUBLIC:
            kept.append(hit)
    return kept


def _symbol_from_file(file_path: str | None) -> str | None:
    if not file_path:
        return None
    base = file_path.split("/")[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or None


# ── ADR-0052 P6: per-entity sticky notes ─────────────────────────────────────

async def _attach_notes(response: QueryResponse, workspace_id: str) -> None:
    """Bulk-fetch sticky notes for every URN the response cites.

    Best-effort: if the entity_notes table doesn't exist (rebuilt prod from
    a pre-V14 dump) or asyncpg can't connect, we leave ``response.notes``
    empty and log at debug level so /query still answers.
    """
    urns: set[str] = set()
    for c in response.affected_entities:
        if c.urn:
            urns.add(c.urn)
    for step in response.call_chain:
        if step.urn:
            urns.add(step.urn)
    if not urns:
        return
    try:
        from companybrain.harness import notes as notes_mod
        by_urn = await notes_mod.list_notes_for_urns(
            workspace_id=workspace_id,
            entity_urns=urns,
        )
    except Exception as exc:
        log.debug("[query] notes lookup skipped", error=str(exc))
        return
    out: list[dict] = []
    for urn in sorted(by_urn):
        out.extend(notes_mod.render_for_query(by_urn[urn]))
    response.notes = out


# ── ADR-0059: temporal-pass derived entities surfacing ──────────────────────

# Hard cap so a workspace with hundreds of alerts doesn't blow up the response.
_MAX_ALERTS_IN_RESPONSE = 50
_MAX_DOMAINS_IN_RESPONSE = 30
_MAX_ONBOARDING_PATHS_IN_RESPONSE = 30


def _attach_adr_0059_derivatives(
    response: QueryResponse,
    repo_path: str | None,
) -> None:
    """Populate ``response.risk_alerts`` / ``domain_entities`` /
    ``onboarding_paths`` from the workspace's ``.brain/`` directory.

    Best-effort: a missing ``.brain/`` or read error leaves the fields empty.
    Filtering by relevance to the question is intentionally NOT done here —
    the frontend's Risk Dashboard tile renders all alerts, and the onboarding
    panel renders all paths so the user can pick a domain.
    """
    brain_root = _resolve_brain_root(repo_path)
    if brain_root is None:
        return
    response.risk_alerts      = _load_brain_subdir(brain_root, "risk_alert",
                                                   limit=_MAX_ALERTS_IN_RESPONSE)
    response.domain_entities  = _load_brain_subdir(brain_root, "domain_entity",
                                                   limit=_MAX_DOMAINS_IN_RESPONSE)
    response.onboarding_paths = _load_brain_subdir(brain_root, "onboarding_path",
                                                   limit=_MAX_ONBOARDING_PATHS_IN_RESPONSE)


def _resolve_brain_root(repo_path: str | None) -> Path | None:
    """Locate the workspace's ``.brain/`` directory. Mirrors the resolution
    used by SmartZoneAssembler so /query and the assembler agree on roots."""
    if repo_path:
        candidate = Path(repo_path) / ".brain"
        if candidate.is_dir():
            return candidate
    env_root = os.environ.get("BRAIN_ROOT")
    if env_root:
        candidate = Path(env_root)
        if candidate.is_dir():
            return candidate
        candidate = Path(env_root) / ".brain"
        if candidate.is_dir():
            return candidate
    return None


# ── ADR-0061: iterative-exploration helpers ──────────────────────────────────

async def _maybe_explore(
    *,
    request: QueryRequest,
    response: QueryResponse,
    zone_tokens_used: int,
) -> QueryResponse:
    """If the initial answer is low-confidence OR context was sparse, spawn
    the ExplorationAgent (ADR-0061 E1) and merge its findings into the response.

    The exploration agent is *additive* — its prose is appended to the
    summary rather than replacing structured fields, so URN citations from
    the initial answer survive. Telemetry records whether it ran and how
    many tool calls it made.
    """
    try:
        from companybrain.agents.exploration_agent import (
            ExplorationAgent, should_fire,
        )
    except Exception as exc:
        log.debug("[query] ExplorationAgent unavailable", error=str(exc))
        return response

    initial_conf = _confidence_to_float(response.confidence)
    if not should_fire(initial_conf, zone_tokens_used):
        return response

    try:
        agent = ExplorationAgent(
            workspace_id=str(request.workspace_id),
            repo_path=getattr(request, "repo_path", None),
        )
        result = await agent.run(request.question)
    except Exception as exc:
        log.warning("[query] ExplorationAgent failed (non-fatal)", error=str(exc))
        return response

    response.telemetry = dict(response.telemetry or {})
    response.telemetry["exploration_agent_invoked"] = True
    response.telemetry["exploration_agent_steps"] = result.steps
    response.telemetry["exploration_agent_capped"] = result.capped
    if result.text:
        bridge = (
            "\n\n— ExplorationAgent follow-up "
            f"({result.steps} tool call{'s' if result.steps != 1 else ''}) —\n"
        )
        response.summary = (response.summary or "") + bridge + result.text
        if response.confidence and response.confidence.level == "low":
            response.confidence = Confidence(
                level="medium",
                rationale="Exploration agent added supporting evidence",
            )
    return response


def _confidence_to_float(conf) -> float:
    """Map a Confidence(level=...) onto a numeric scale for E1's gate."""
    if conf is None:
        return 0.0
    level = (getattr(conf, "level", "") or "").lower()
    return {"high": 0.9, "medium": 0.7, "low": 0.4}.get(level, 0.5)


def _attach_cross_repo_insights(response: QueryResponse, workspace_id: str) -> None:
    """ADR-0061 E6 — attach SIMILAR_TO insights for top affected entities."""
    if not response.affected_entities:
        return
    try:
        from companybrain.retrieval.cross_repo_similarity import (
            attach_cross_repo_insights,
        )
        from companybrain.store.identity import workspace_slug_for
    except Exception as exc:
        log.debug("[query] cross-repo similarity module unavailable",
                  error=str(exc))
        return
    seeds = [
        {"urn": c.urn, "name": c.name, "text": c.why_relevant or c.name}
        for c in response.affected_entities[:5]
    ]
    try:
        n = attach_cross_repo_insights(
            response=response,
            own_workspace_slug=workspace_slug_for(workspace_id),
            seeds=seeds,
        )
    except Exception as exc:
        log.debug("[query] cross-repo similarity attach failed",
                  error=str(exc))
        return
    if n:
        response.telemetry = dict(response.telemetry or {})
        response.telemetry["cross_repo_hits"] = n


# ── ADR-0061 P1: iterative exploration helper ─────────────────────────────────

async def _iterative_answer(
    question: str,
    context: str | None,
    workspace_id: str,
    repo_path: str | None,
    qdrant_index: str,
    persona: str = "dev",
) -> tuple[QueryResponse, dict]:
    """
    Run the iterative exploration loop and return (QueryResponse, telemetry_dict).

    The retrieve_fn closure captures the request-scoped parameters so
    ExplorationLoop can call back into hybrid_retrieve during iterations.
    Falls back to single-pass on import error or loop exception.

    ADR-0079 P1: persona is now forwarded to orchestrate_query so the
    iterative loop can use persona context when deciding what to retrieve.
    """
    async def _retrieve(sub_query: str) -> str | None:
        return await _hybrid_retrieve(
            sub_query, workspace_id, repo_path, index=qdrant_index
        )

    try:
        from companybrain.query.orchestrator import orchestrate_query
        result = await orchestrate_query(
            question=question,
            context=context,
            retrieve_fn=_retrieve,
            persona=persona,
        )
        telemetry = {
            "iterations_taken": result.iterations_taken,
            "verifier_score": result.verifier_score,
            "exploration_agent_invoked": result.exploration_agent_invoked,
        }
        log.info(
            "[query] iterative loop done",
            **telemetry,
            citations=len(result.response.affected_entities),
        )
        return result.response, telemetry
    except Exception as exc:
        log.warning("[query] iterative path failed — single-pass fallback", error=str(exc))
        from companybrain.agents.answerer_agent import AnswererAgent
        agent = AnswererAgent()
        response = await agent.answer(question, context)
        return response, {}


# ── ADR-0072: conversation persistence ───────────────────────────────────────

async def _persist_conversation(
    workspace_id: str,
    question: str,
    query_response: QueryResponse,
    actor_id: str | None,
    actor_kind: str,
) -> None:
    """
    Insert the completed /query call into the conversations table.

    Best-effort: any exception is swallowed and logged at DEBUG level so that
    a DB hiccup never causes /query to return an error to the caller.

    Fields persisted:
      - workspace_id  — scopes the row to the workspace
      - question      — the raw user question (History tab)
      - answer_md     — pre-rendered markdown blob (detail view)
      - summary_json  — full QueryResponse as JSON (round-trip detail view)
      - title         — first 60 chars of the question (display label)
      - actor_id      — who asked (Audit Log tab)
      - actor_kind    — 'user' | 'ci' | 'api' (Audit Log filter)
    """
    try:
        from sqlalchemy import text as _text
        from companybrain.db import get_session

        title = question[:60]
        answer_md = getattr(query_response, "raw_markdown", None) or getattr(
            query_response, "summary_md", None
        )
        try:
            summary_json = query_response.model_dump()
        except AttributeError:
            summary_json = query_response.dict()

        import json as _json
        summary_json_str = _json.dumps(summary_json)

        async with get_session() as session:
            await session.execute(_text("""
                INSERT INTO conversations
                    (workspace_id, question, answer_md, summary_json, title, actor_id, actor_kind)
                VALUES
                    (:workspace_id, :question, :answer_md, :summary_json::jsonb,
                     :title, :actor_id, :actor_kind)
            """), {
                "workspace_id": workspace_id,
                "question":     question,
                "answer_md":    answer_md,
                "summary_json": summary_json_str,
                "title":        title,
                "actor_id":     actor_id,
                "actor_kind":   actor_kind or "user",
            })
            await session.commit()

        log.debug("[query] Conversation persisted", workspace_id=workspace_id)

    except Exception as exc:
        log.debug("[query] Conversation persistence skipped (non-fatal)", error=str(exc))


# ── ADR-0079 P1: Persona formatting ──────────────────────────────────────────


def _apply_persona_formatting(
    question: str,
    persona_param: str | None,
    query_response: QueryResponse,
) -> QueryResponse:
    """
    ADR-0079 P1 — Route the question to a persona + shape, then reformat the
    response into persona-specific AnswerBlocks.

    Attaches to response.telemetry (never replaces summary — backward-compat).
    Best-effort: any error returns the unmodified response.
    """
    try:
        from companybrain.personas import route_query, get_formatter, load_bindings
        from companybrain.config import settings as _s

        result = route_query(question, persona_param=persona_param)

        # Load vertical bindings (best-effort)
        bindings = load_bindings(vertical=_s.persona_vertical)

        formatter = get_formatter(result.persona)
        raw = query_response.summary or ""
        formatted = formatter.format(
            raw_answer=raw,
            shape=result.shape,
            bindings=bindings,
            match_confidence=result.match_confidence,
            fell_through_to_generic=result.fell_through_to_generic,
        )

        # Attach persona metadata to telemetry without clobbering existing fields
        persona_telemetry: dict = {
            "persona": formatted.persona,
            "matched_shape_id": formatted.shape_id,
            "match_confidence": round(formatted.match_confidence, 3),
            "fell_through_to_generic": formatted.fell_through_to_generic,
            "persona_source": result.persona_source,
            "answer_blocks": formatted.to_dict()["answer_blocks"],
        }
        query_response.telemetry = dict(query_response.telemetry or {})
        query_response.telemetry.update(persona_telemetry)

        log.info(
            "[query/persona] Formatted",
            persona=result.persona,
            shape=result.shape.id if result.shape else None,
            confidence=round(result.match_confidence, 3),
            fell_through=result.fell_through_to_generic,
        )
    except Exception as exc:
        log.debug("[query/persona] Persona formatting failed (non-fatal)", error=str(exc))

    return query_response


# ── ADR-0015 A1.2: RetrievalPipeline wrapper ─────────────────────────────────

async def _hybrid_retrieve_v2(
    question: str,
    workspace_id: str,
    repo_path: str | None = None,
    index: str = "default",
    include_unverified: bool = False,
    top_k: int = 10,
) -> tuple[str | None, float]:
    """Retrieve entities via RetrievalPipeline (BM25+dense+RRF+BGE reranker).

    Extends ``_hybrid_retrieve()`` with full A1.2 pipeline support and returns
    a ``(context_str, top_score)`` tuple so callers can propagate the retrieval
    score into confidence aggregation.

    The ``top_score`` can be used by the confidence aggregator to determine
    whether the retrieval was strong enough to boost confidence from "low" to
    "medium" without needing an extra LLM call.

    Returns (None, 0.0) on any failure — callers fall through to the existing
    non-v2 hybrid path automatically.
    """
    try:
        from companybrain.retrieval.factory import make_retrieval_pipeline
        from companybrain.store.identity import workspace_slug_for
        from companybrain.config import settings as _s

        effective_root = repo_path or os.environ.get("BRAIN_ROOT", "")
        if not effective_root:
            return None, 0.0

        workspace_slug = workspace_slug_for(workspace_id)
        pipeline = make_retrieval_pipeline(
            _s,
            brain_root=Path(effective_root),
            workspace_slug=workspace_slug,
        )

        result = await pipeline.retrieve(
            question,
            workspace_id=workspace_slug,
            top_k=top_k,
            index=index,
        )

        if not result.hits:
            return None, 0.0

        filtered = _filter_verified(result.hits, include_unverified=include_unverified)
        if not filtered:
            return None, 0.0

        lines = ["## Hybrid Retrieval Results\n"]
        for hit in filtered:
            payload = hit.payload
            name = payload.get("qualified_name") or hit.urn.split(":")[-1]
            summary = payload.get("t1_summary", "")
            lines.append(
                f"- **{name}** ({payload.get('entity_type', '')}): {summary}"
            )

        log.info(
            "[query] hybrid_retrieve_v2 OK",
            hits=len(filtered),
            reranked=result.reranked,
            top_score=round(result.top_score, 4),
            reranker_model=result.reranker_model,
            index=index,
        )

        return "\n".join(lines), result.top_score

    except Exception as exc:
        log.debug("[query] hybrid_retrieve_v2 failed (non-fatal)", error=str(exc))
        return None, 0.0


def _load_brain_subdir(brain_root: Path, subdir: str, *, limit: int) -> list[dict]:
    """Read every JSON file in ``brain_root/<subdir>/`` and return up to
    ``limit`` rows. Each row is the file's ``metadata`` dict (the projection
    we wrote at Stage 5) plus the ``qualified_name`` for display."""
    folder = brain_root / subdir
    if not folder.is_dir():
        return []
    out: list[dict] = []
    try:
        files = sorted(folder.glob("*.json"))[:limit]
    except OSError:
        return []
    for path in files:
        try:
            blob = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = blob.get("metadata") or {}
        row = {
            "name":     blob.get("qualified_name", path.stem),
            "summary":  meta.get("code_snippet") or meta.get("signature", ""),
            "metadata": meta,
        }
        out.append(row)
    return out
