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
import re
import time
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter

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

    # ── Step 5: Render markdown blob ──────────────────────────────────────────
    render_to_markdown(query_response)

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


# ── LLM response parsing ──────────────────────────────────────────────────────

def _parse_llm_response(raw: str, context: str | None) -> QueryResponse:
    """
    Parse the LLM's JSON output into a typed QueryResponse.

    Falls back to a minimal envelope when the LLM returns free-form text,
    so existing consumers always receive the same schema.
    """
    # Strip markdown fences the LLM sometimes wraps around JSON.
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        data: dict[str, Any] = json.loads(cleaned)
        return QueryResponse(**data)
    except Exception as exc:
        log.warning("[query] LLM output was not valid QueryResponse JSON — wrapping",
                    error=str(exc), preview=raw[:200])
        confidence_level = (
            "medium" if context else "low"
        )
        return QueryResponse(
            summary=_strip_uncited(raw),
            confidence=Confidence(
                level=confidence_level,
                rationale="LLM returned free-form text rather than structured JSON",
            ),
        )


_CITATION_RE = re.compile(r"\[urn:cb:[^\]]+\]")
_CODE_TOKEN_RE = re.compile(r"[A-Z][a-z]+[A-Z]|[a-z]+\.[a-zA-Z]+|[A-Z_]{3,}|\w+\.\w+")


def _strip_uncited(text: str) -> str:
    """Remove sentences that contain code-shaped tokens but no URN citation."""
    result = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _CODE_TOKEN_RE.search(sentence) and not _CITATION_RE.search(sentence):
            continue
        result.append(sentence)
    return " ".join(result) if result else text


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


async def _hybrid_retrieve(
    question: str, workspace_id: str, repo_path: str | None = None,
    index: str = "default",
) -> str | None:
    """Retrieve relevant entities via HybridSearcher as a fallback context source.

    Resolves brain_root from (in order):
      1. Caller-supplied repo_path (typically request.repo_path)
      2. BRAIN_ROOT env var

    Returns None if neither is set OR neither contains a usable .brain/ directory.
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
        lines = ["## Hybrid Retrieval Results\n"]
        for hit in hits:
            payload = hit.payload
            name = payload.get("qualified_name") or hit.urn.split(":")[-1]
            summary = payload.get("t1_summary", "")
            lines.append(f"- **{name}** ({payload.get('entity_type', '')}): {summary}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("[query] Hybrid retrieval failed (non-fatal)", error=str(exc))
        return None


def _symbol_from_file(file_path: str | None) -> str | None:
    if not file_path:
        return None
    base = file_path.split("/")[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or None
