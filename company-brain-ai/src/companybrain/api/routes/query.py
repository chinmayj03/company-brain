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

import json
import os
from pathlib import Path
from typing import Literal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

import httpx

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import QueryRequest, QueryResponse

router = APIRouter()
log = structlog.get_logger(__name__)

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://localhost:8080")
INTERNAL_KEY = os.environ.get("AI_INTERNAL_KEY", "dev-internal-key")

# ── ADR-0042 E10: Intent router ───────────────────────────────────────────────

class IntentRouterResponse(BaseModel):
    """Structured output from the intent router LLM call."""
    intent: Literal[
        "impact-analysis", "trace-flow", "explain-purpose",
        "find-callers", "find-tests", "schema-question", "general"
    ]
    anchor_entities: list[str]
    edge_types_needed: list[str]
    max_hops: int = 2
    include_test_coverage: bool = False


# Intent → SmartZone policy: which hops / edge types / test coverage to pull
_INTENT_POLICY: dict[str, dict] = {
    "impact-analysis": {
        "hops": 4,
        "edge_filter": [],   # no filter — all edges
        "include_tests": True,
    },
    "trace-flow": {
        "hops": 5,
        "edge_filter": ["CALLS", "CALLS_ENDPOINT", "READS_COLUMN", "WRITES_COLUMN"],
        "include_tests": False,
    },
    "explain-purpose": {
        "hops": 1,
        "edge_filter": ["CONTAINS", "USES", "EXTENDS"],
        "include_tests": False,
    },
    "find-callers": {
        "hops": 2,
        "edge_filter": ["CALLS"],   # reverse direction handled by query
        "include_tests": False,
    },
    "find-tests": {
        "hops": 1,
        "edge_filter": ["TESTED_BY"],
        "include_tests": True,
    },
    "schema-question": {
        "hops": 2,
        "edge_filter": ["READS_COLUMN", "WRITES_COLUMN", "CONTAINS"],
        "include_tests": False,
    },
    "general": {
        "hops": 2,
        "edge_filter": [],
        "include_tests": False,
    },
}

_INTENT_ROUTER_SYSTEM = """\
You are a query intent classifier for a code knowledge graph.

Given a natural-language question about code, classify the INTENT and identify
the anchor entities (named code artifacts: function names, class names, table
names, column names, endpoint paths) the question is about.

Output ONLY valid JSON matching exactly:
{
  "intent": "<one of the values below>",
  "anchor_entities": ["<entity name>", ...],
  "edge_types_needed": ["<EDGE_TYPE>", ...],
  "max_hops": <integer 1-5>,
  "include_test_coverage": <boolean>
}

Intent values:
  impact-analysis  — "what breaks if I change X?", "blast radius of X", "rename X"
  trace-flow       — "how does X reach Y?", "call chain from X to DB", "data flow"
  explain-purpose  — "what does X do?", "explain X", "what is X for?"
  find-callers     — "who calls X?", "what uses X?", "callers of X"
  find-tests       — "what tests cover X?", "is X tested?", "test coverage for X"
  schema-question  — "what tables does X read?", "which columns?", "DB schema for X"
  general          — anything else

EXAMPLES:
  Q: "What breaks if I rename lob column in plan_info?"
  → {"intent": "impact-analysis", "anchor_entities": ["plan_info", "lob"], "edge_types_needed": ["READS_COLUMN", "WRITES_COLUMN", "CALLS"], "max_hops": 4, "include_test_coverage": true}

  Q: "How does getCompetitors reach the database?"
  → {"intent": "trace-flow", "anchor_entities": ["getCompetitors"], "edge_types_needed": ["CALLS", "READS_COLUMN"], "max_hops": 5, "include_test_coverage": false}

  Q: "What tests cover CompetitorService?"
  → {"intent": "find-tests", "anchor_entities": ["CompetitorService"], "edge_types_needed": ["TESTED_BY"], "max_hops": 1, "include_test_coverage": true}
"""

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

━━━ CITATION RULES ━━━
When describing how data is fetched or stored:
  1. Identify the CALL CHAIN: API handler → service method → repository/DAO method.
  2. If any node has a query_text field (SQL/JPQL), QUOTE IT verbatim inside backticks.
     Example: "The service calls `findByPayerIdAndLob` which executes:
     `SELECT p FROM Payer p WHERE p.payerId = :id AND p.lob = :lob`"
  3. If the method is an InterfaceMethod (JPA derived query with no body), say so:
     "Spring Data derives the SQL from the method signature `findAllByStatus(String)`"
  4. For jOOQ queries, quote the DSL chain or the reconstructed SQL approximation.
  5. Always name the specific repository method called, not just the service method.

━━━ FORMAT ━━━
  - 2–4 paragraphs of direct answer
  - Inline code citations for SQL/queries (use backticks)
  - A "Call chain:" line showing the flow from endpoint to DB when relevant
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

    # ── Step 0 (ADR-0042 E10): Intent router — classify question before assembly ──
    intent_result: IntentRouterResponse | None = None
    from companybrain.config import settings as _qs
    if _qs.enable_intent_router:
        intent_result = await _classify_intent(request.question, provider)
        if intent_result:
            log.info("[query] Intent classified",
                     intent=intent_result.intent,
                     anchors=intent_result.anchor_entities,
                     hops=intent_result.max_hops)

    # ── Step 1: Smart-zone assembly (ADR-0018) ────────────────────────────────
    assembled_context: str | None = None
    smart_zone_meta: dict = {}

    assembled_context, smart_zone_meta = await _smart_zone_assemble(
        task=request.question,
        workspace_id=str(request.workspace_id),
        repo_path=getattr(request, "repo_path", None),
        intent=intent_result,
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
        assembled_context = await _hybrid_retrieve(
            request.question,
            request.workspace_id,
            getattr(request, "repo_path", None),
        )
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
        max_tokens=2048,
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

async def _classify_intent(
    question: str,
    provider,
) -> "IntentRouterResponse | None":
    """
    ADR-0042 E10: One cheap LLM call (~$0.001) that classifies the question intent
    and identifies anchor entities. Drives SmartZonePolicy for subgraph selection.

    Returns None on failure (non-fatal — caller falls back to generic assembly).
    """
    try:
        from companybrain.config import settings as _cs
        raw = await provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_INTENT_ROUTER_SYSTEM),
                ChatMessage(role="user",   content=f"Question: {question}"),
            ],
            role=TaskRole.FAST,
            max_tokens=_cs.max_tokens_intent_router,
        )
        data = json.loads(raw)
        return IntentRouterResponse(**data)
    except Exception as exc:
        log.debug("[query] Intent router failed (non-fatal)", error=str(exc))
        return None


async def _smart_zone_assemble(
    task: str,
    workspace_id: str,
    repo_path: str | None,
    intent: "IntentRouterResponse | None" = None,
) -> tuple[str | None, dict]:
    """
    Run SmartZoneAssembler (ADR-0018) to build T0/T1/T2 tiered context.

    ADR-0042 E10: When intent is provided, applies the corresponding
    SmartZonePolicy (hops, edge_filter, include_tests) from _INTENT_POLICY.

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

        # Apply intent-derived policy when available
        assemble_kwargs: dict = {"task": task, "budget": budget}
        if intent:
            policy = _INTENT_POLICY.get(intent.intent, _INTENT_POLICY["general"])
            # Pass policy overrides as kwargs — SmartZoneAssembler reads them
            # if it supports them (graceful no-op if not yet upgraded).
            assemble_kwargs["intent_policy"] = {
                "hops":           policy["hops"],
                "edge_filter":    policy["edge_filter"],
                "include_tests":  policy["include_tests"],
                "anchor_entities": intent.anchor_entities,
            }
            if intent.anchor_entities:
                assemble_kwargs["entities"] = intent.anchor_entities

        payload = await assembler.assemble(**assemble_kwargs)
        await driver.close()

        meta = {
            "task_type":  payload.task_type,
            "tokens_used": payload.tokens_used,
            "t0": payload.t0,
            "t1": payload.t1,
            "t2": payload.t2,
            **({"intent": intent.intent} if intent else {}),
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


async def _hybrid_retrieve(
    question: str, workspace_id: str, repo_path: str | None = None
) -> str | None:
    """Retrieve relevant entities via HybridSearcher as a fallback context source.

    Resolves brain_root from (in order):
      1. Caller-supplied repo_path (typically request.repo_path)
      2. BRAIN_ROOT env var

    Returns None if neither is set OR neither contains a usable .brain/ directory —
    callers treat None as "no fallback context available."
    """
    try:
        from pathlib import Path
        from companybrain.retrieval.hybrid_search import HybridSearcher
        from companybrain.store.identity import workspace_slug_for

        effective_root = repo_path or os.environ.get("BRAIN_ROOT", "")
        if not effective_root:
            return None
        brain_root = Path(effective_root)
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
