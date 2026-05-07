"""
POST /query — answer a natural language question using tiered graph context + LLM.

Architecture (ADR-0018: Smart-zone context assembler):

  1. SmartZoneAssembler.assemble() classifies the task, runs hybrid retrieval
     (ADR-0015), expands via Neo4j blast-radius, MMR-reranks, tiers into
     T0/T1/T2, compresses task-aware, and renders the final context block.
  2. Build the LLM system prompt with payload.rendered as the context.
  3. Call the LLM (configured provider: OpenAI / Anthropic / Ollama / Groq).
  4. Return answer + source citations.

Fallback: if SmartZoneAssembler is unavailable (missing .brain/, Neo4j down),
          falls back to the legacy Java-assembler or hybrid-retrieval path.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog
from fastapi import APIRouter

import httpx

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import QueryRequest, QueryResponse

router = APIRouter()
log = structlog.get_logger(__name__)

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://localhost:8080")
INTERNAL_KEY = os.environ.get("AI_INTERNAL_KEY", "dev-internal-key")

# ── Prompts ───────────────────────────────────────────────────────────────────

QUERY_SYSTEM_PROMPT = """\
You are a senior software engineer answering questions about a codebase.

You are given a KNOWLEDGE BASE built from the dependency graph of the codebase.
It is organised in tiers:
  • T2 blocks (## headers): the most relevant nodes — read carefully.
  • T1 blocks (### headers): nearby context — skim for relevant facts.
  • T0 list (## Other Related Nodes): distant nodes — use for awareness only.

Answer the user's question using ONLY information from the KNOWLEDGE BASE.
Cite node names when you reference them. Flag HIGH change-risk nodes explicitly.
If the knowledge base doesn't contain enough information to answer, say so clearly.
Do NOT guess or invent facts about the codebase.

Format:
  - 2–4 paragraphs of direct answer
  - A "Risk assessment:" line if any HIGH-risk nodes are relevant
  - A "Needs more context:" section ONLY if you genuinely can't answer
"""


# ── Main handler ──────────────────────────────────────────────────────────────

@router.post("", response_model=QueryResponse)
async def query_graph(request: QueryRequest):
    """
    POST /query

    1. SmartZoneAssembler.assemble() builds T0/T1/T2 tiered context (ADR-0018)
    2. Call LLM with payload.rendered as context
    3. Return answer + citations
    """
    provider = get_provider()

    # ── Step 1: Smart-zone assembly (ADR-0018) ────────────────────────────────
    assembled_context: str | None = None
    smart_zone_meta: dict = {}

    assembled_context, smart_zone_meta = await _smart_zone_assemble(
        task=request.question,
        workspace_id=str(request.workspace_id),
        repo_path=getattr(request, "repo_path", None),
    )

    # ── Step 1b: Legacy Java assembler fallback ───────────────────────────────
    if not assembled_context:
        focal_node_id: str | None = None
        focal_name: str | None = None
        search_symbol = request.context_symbol or _symbol_from_file(request.file_path)
        if search_symbol:
            focal_node_id, focal_name = await _resolve_node(
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
        assembled_context = await _hybrid_retrieve(request.question, request.workspace_id)
        if assembled_context:
            log.info("[query] Using hybrid retrieval context (SmartZone + Java unavailable)")

    # ── Step 2: Build LLM prompt ──────────────────────────────────────────────
    context_quality = "high" if assembled_context else "low"
    if assembled_context:
        user_content = (
            f"KNOWLEDGE BASE:\n\n{assembled_context}\n\n"
            f"---\n\n"
            f"QUESTION: {request.question}"
        )
    else:
        user_content = (
            f"QUESTION: {request.question}\n\n"
            f"Note: No brain context available. "
            f"Run the extraction pipeline on the repo first."
        )

    # ── Step 3: Call LLM ──────────────────────────────────────────────────────
    log.info("[query] Calling LLM",
             task_type=smart_zone_meta.get("task_type"),
             tokens_used=smart_zone_meta.get("tokens_used", 0),
             quality=context_quality)

    response = await provider.chat(
        messages=[
            ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=user_content),
        ],
        role=TaskRole.QUERY,
        max_tokens=1024,
    )

    # ── Step 4: Build response ────────────────────────────────────────────────
    sources = []
    t0_count = len(smart_zone_meta.get("t0", []))
    t1_count = len(smart_zone_meta.get("t1", []))
    t2_count = len(smart_zone_meta.get("t2", []))
    if t0_count or t1_count or t2_count:
        sources.append({
            "label": (
                f"SmartZone: T0={t0_count} T1={t1_count} T2={t2_count} "
                f"tokens={smart_zone_meta.get('tokens_used', 0)}"
            ),
            "node_id": "",
        })

    confidence = (
        "high"   if context_quality == "high" and (t1_count + t2_count) > 1
        else "medium" if context_quality == "high"
        else "low"
    )

    return QueryResponse(
        answer=response.content,
        sources=sources,
        affected_nodes=[],
        confidence=confidence,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _smart_zone_assemble(
    task: str, workspace_id: str, repo_path: str | None
) -> tuple[str | None, dict]:
    """
    Run SmartZoneAssembler (ADR-0018) to build T0/T1/T2 tiered context.
    Returns (rendered_str, meta_dict) or (None, {}) if unavailable.
    """
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
        store  = JsonFileBrainStore(brain_root.parent)

        assembler = SmartZoneAssembler(
            brain_root=brain_root,
            workspace_id=workspace_id,
            store=store,
            neo4j_driver=driver,
        )
        budget  = TokenBudget()
        payload = await assembler.assemble(task=task, budget=budget)
        await driver.close()

        meta = {
            "task_type":  payload.task_type,
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
    """
    Search the Java backend for a node matching the given symbol.
    Returns (node_id, node_name) or (None, None) if not found or backend unavailable.
    """
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
    """
    Call POST /v1/internal/assemble-context on the Java backend.
    Returns (contextText, traversalMeta) or (None, {}) if unavailable.
    """
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


async def _hybrid_retrieve(question: str, workspace_id: str) -> str | None:
    """Retrieve relevant entities via HybridSearcher as a fallback context source."""
    try:
        from pathlib import Path
        from companybrain.retrieval.hybrid_search import HybridSearcher
        from companybrain.store.identity import workspace_slug_for

        brain_root_env = os.environ.get("BRAIN_ROOT", "")
        if not brain_root_env:
            return None
        brain_root = Path(brain_root_env)
        workspace_slug = workspace_slug_for(workspace_id)
        searcher = HybridSearcher(brain_root=brain_root, workspace_slug=workspace_slug)
        hits = searcher.search(question, top_k=10)
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
    """
    Derive a search symbol from a file path when no explicit symbol is given.
    e.g. 'src/services/PaymentService.java' → 'PaymentService'
    """
    if not file_path:
        return None
    base = file_path.split("/")[-1]
    # Strip extension
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or None
