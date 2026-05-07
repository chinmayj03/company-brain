"""
POST /query — answer a natural language question using tiered graph context + LLM.

Architecture (ADR-004: Tiered Memory & Context Assembly):

  1. Resolve the focal node from context_symbol or file_path (search Java backend).
  2. Call POST /v1/internal/assemble-context on the Java backend.
     Java runs BFS traversal and packs tiered context:
       T2 (~600 tok): focal node + immediate neighbors (full business context)
       T1 (~100 tok): mid-range nodes (purpose + change risk)
       T0 (~15  tok): far nodes (name + type one-liners)
     All within the configured token budget.
  3. Build the LLM system prompt with the assembled context as a "KNOWLEDGE BASE" block.
  4. Call the LLM (configured provider: OpenAI / Anthropic / Ollama).
  5. Return answer + source citations from the traversal metadata.

Fallback: if Java is unavailable (dev mode, cold start), falls back to a direct
          DB query path that mimics the old approach with basic context.
"""
from __future__ import annotations

import os
import structlog
from fastapi import APIRouter, HTTPException

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

    1. Resolve focal node via Java search API
    2. Assemble tiered context from Java ContextAssemblerService
    3. Call LLM with assembled context
    4. Return answer + citations
    """
    provider = get_provider()

    # ── Step 1: Resolve focal node ────────────────────────────────────────────
    focal_node_id: str | None   = None
    focal_external_id: str | None = None
    focal_name: str | None      = None

    search_symbol = request.context_symbol or _symbol_from_file(request.file_path)
    if search_symbol:
        focal_node_id, focal_name = await _resolve_node(
            request.workspace_id, search_symbol
        )

    if not focal_node_id:
        log.info("[query] No focal node resolved — answering from question only",
                 symbol=search_symbol)

    # ── Step 2: Assemble tiered context from Java ─────────────────────────────
    assembled_context: str | None = None
    traversal_meta: dict = {}

    if focal_node_id:
        assembled_context, traversal_meta = await _assemble_context(
            workspace_id=str(request.workspace_id),
            focal_node_id=focal_node_id,
            question=request.question,
            max_hops=request.max_hops,
        )

    # ── Step 3: Build LLM prompt ──────────────────────────────────────────────
    if assembled_context:
        user_content = (
            f"KNOWLEDGE BASE:\n\n{assembled_context}\n\n"
            f"---\n\n"
            f"QUESTION: {request.question}"
        )
        context_quality = "high"
    elif focal_name:
        # Fell back: Java assembler unavailable, we have the node name at least
        user_content = (
            f"Limited context available for node `{focal_name}`.\n\n"
            f"QUESTION: {request.question}\n\n"
            f"Note: The full dependency graph is available but context assembly failed. "
            f"Answer what you can from the question alone, and suggest running the pipeline."
        )
        context_quality = "low"
    else:
        user_content = (
            f"QUESTION: {request.question}\n\n"
            f"Note: No node was found matching the requested symbol. "
            f"Run the extraction pipeline on the relevant endpoint first."
        )
        context_quality = "low"

    # ── Step 4: Call LLM ──────────────────────────────────────────────────────
    log.info("[query] Calling LLM",
             focal=focal_name,
             context_tokens=traversal_meta.get("estimatedTokens", 0),
             nodes_included=traversal_meta.get("nodesIncluded", 0),
             quality=context_quality)

    response = await provider.chat(
        messages=[
            ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=user_content),
        ],
        role=TaskRole.QUERY,
        max_tokens=1024,
    )

    # ── Step 5: Build response ────────────────────────────────────────────────
    # Reconstruct affected_nodes from traversal metadata for backward compatibility
    affected_nodes: list[dict] = []
    if focal_node_id and focal_name:
        affected_nodes.append({
            "id":    focal_node_id,
            "name":  focal_name,
            "type":  traversal_meta.get("focalNodeType", ""),
            "depth": 0,
        })

    tier_summary = traversal_meta.get("tierSummary", {})
    sources = []
    if focal_name:
        sources.append({
            "label":   f"{focal_name} (focal)",
            "node_id": focal_node_id or "",
        })
    if traversal_meta.get("nodesTraversed", 0) > 1:
        sources.append({
            "label": (
                f"{traversal_meta.get('nodesIncluded', 0)} nodes assembled "
                f"(T2:{tier_summary.get('t2Count',0)} "
                f"T1:{tier_summary.get('t1Count',0)} "
                f"T0:{tier_summary.get('t0Count',0)})"
            ),
            "node_id": "",
        })

    confidence = (
        "high"   if context_quality == "high" and traversal_meta.get("nodesIncluded", 0) > 1
        else "medium" if context_quality == "high"
        else "low"
    )

    return QueryResponse(
        answer=response.content,
        sources=sources,
        affected_nodes=affected_nodes,
        confidence=confidence,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

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
