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

import json
import os
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

import httpx

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import QueryRequest
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

# Intent → Qdrant index mapping for intent-aware retrieval (ADR-0043 WS2)
_INTENT_TO_INDEX: dict[str, str] = {
    "call_chain":  "default",   # per-entity BM25+dense RRF; needs full graph
    "data_flow":   "code",      # code/signature collection for SQL queries
    "change_risk": "default",   # full graph to trace blast radius
    "concept":     "business",  # business context collection for semantic search
    "other":       "default",
}


# ── Main handler ──────────────────────────────────────────────────────────────

@router.post("", response_model=QueryResponse)
async def query_graph(request: QueryRequest):
    """
    POST /query

    0. IntentRouter classifies the question (ADR-0043 WS2)
    1. SmartZoneAssembler.assemble() builds T0/T1/T2 tiered context (ADR-0018)
    2. Per-intent user message is rendered (ADR-0043 WS2)
    3. Call LLM with structured system prompt (ADR-0043)
    4. Parse typed QueryResponse
    5. Return structured response
    """
    from companybrain.config import settings

    t0 = time.monotonic()
    provider = get_provider()

    # ── Step 0: Intent classification (ADR-0043 WS2) ──────────────────────────
    intent = "concept"
    if not settings.skip_intent_router:
        try:
            from companybrain.api.intent_router import classify_intent
            intent = await classify_intent(
                request.question,
                workspace_id=str(request.workspace_id),
                ttl_sec=settings.brain_query_cache_ttl_sec,
            )
        except Exception as exc:
            log.warning("[query] Intent router failed (using 'concept')", error=str(exc))

    log.info("[query] Intent classified", intent=intent)
    qdrant_index = _INTENT_TO_INDEX.get(intent, "default")

    # ── Step 1: Smart-zone assembly (ADR-0018) ────────────────────────────────
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

    # ── Step 1c: Hybrid retrieval fallback (ADR-0015) ─────────────────────────
    if not assembled_context:
        assembled_context = await _hybrid_retrieve(
            request.question,
            request.workspace_id,
            getattr(request, "repo_path", None),
            index=qdrant_index,
            include_unverified=getattr(request, "include_unverified", False),
        )
        if assembled_context:
            log.info("[query] Using hybrid retrieval context (SmartZone + Java unavailable)",
                     intent=intent, index=qdrant_index)

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

    # ── Step 3: Call LLM ──────────────────────────────────────────────────────
    t1 = time.monotonic()
    log.info("[query] Calling LLM",
             intent=intent,
             task_type=smart_zone_meta.get("task_type"),
             tokens_used=smart_zone_meta.get("tokens_used", 0),
             context_available=bool(assembled_context))

    response = await provider.chat(
        messages=[
            ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=user_content),
        ],
        role=TaskRole.QUERY,
        max_tokens=4096,
    )
    llm_dur = int((time.monotonic() - t1) * 1000)

    # ── Step 4: Parse structured response ────────────────────────────────────
    query_response = _parse_llm_response(response.content, assembled_context)

    # ── Step 4b: Surface per-entity sticky notes (ADR-0052 P6) ───────────────
    await _attach_notes(query_response, str(request.workspace_id))

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


# ── LLM response parsing ──────────────────────────────────────────────────────

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
        return QueryResponse(
            summary=raw,
            confidence=Confidence(
                level="medium" if context else "low",
                rationale="LLM returned free-form text rather than structured JSON",
            ),
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
        from neo4j import AsyncGraphDatabase
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

        neo4j_url  = os.environ.get("NEO4J_URL", "bolt://localhost:7687")
        neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        neo4j_pass = os.environ.get("NEO4J_PASSWORD", "password")

        driver = AsyncGraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_pass))
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
        await driver.close()

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
