"""
ExplorationAgent — ADR-0061 E1.

Fires when /query receives a low-confidence answer (confidence.level == "low").
Runs up to MAX_ROUNDS of tool-assisted exploration, then returns a context
string that query.py injects into a second LLM call.

Budget: $0.10 per invocation, enforced by MAX_ROUNDS + MAX_TOOL_CALLS_PER_ROUND.

Tools available to the agent:
  read_file        — read any source file from the repo
  search_entities  — hybrid search over .brain/ entities
  get_neighbors    — Neo4j neighborhood for a URN (falls back to .brain/ scan)
  get_callers      — callers of a method URN (Neo4j or .brain/ scan)
  get_schema       — full entity JSON from .brain/ by URN
  get_git_log      — recent git commits touching a file
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from companybrain.llm import get_provider, ChatMessage, TaskRole
from companybrain.llm.base import ToolDefinition, ToolParameter, ToolCall

log = structlog.get_logger(__name__)

# ── Cost / safety caps ─────────────────────────────────────────────────────────
MAX_ROUNDS = 3
MAX_TOOL_CALLS_PER_ROUND = 4
MAX_FILE_CHARS = 6_000


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class ExplorationResult:
    context: str               # additional context gathered; inject before re-query
    citations: list[str] = field(default_factory=list)  # URNs found
    rounds_taken: int = 0
    tool_calls_made: int = 0


# ── Tool implementations ───────────────────────────────────────────────────────

def _tool_read_file(path: str, max_chars: int = MAX_FILE_CHARS) -> str:
    try:
        text = Path(path).read_text(errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated at {max_chars} chars]"
        return text
    except OSError as exc:
        return f"(error reading {path}: {exc})"


def _tool_search_entities(
    query: str,
    brain_root: str,
    workspace_id: str,
    top_k: int = 8,
) -> str:
    try:
        from companybrain.retrieval.hybrid_search import HybridSearcher
        from companybrain.store.identity import workspace_slug_for

        slug = workspace_slug_for(workspace_id)
        searcher = HybridSearcher(brain_root=Path(brain_root), workspace_slug=slug)
        hits = searcher.search(query, top_k=top_k)
        if not hits:
            return "(no results)"
        lines = []
        for h in hits:
            name = h.payload.get("qualified_name") or h.urn.split(":")[-1]
            summary = h.payload.get("t1_summary", "")
            lines.append(f"[{h.urn}] {name}: {summary}")
        return "\n".join(lines)
    except Exception as exc:
        return f"(search_entities error: {exc})"


def _tool_get_schema(urn: str, brain_root: str) -> str:
    """Return the full entity JSON from .brain/ for a URN."""
    try:
        root = Path(brain_root)
        # Try index.json first
        index_path = root / "index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text())
            rel = index.get(urn)
            if rel:
                entity_path = root / rel
                if entity_path.exists():
                    return entity_path.read_text()

        # Fall back: scan subdirectories for a file whose content has the URN
        for json_file in root.rglob("*.json"):
            if json_file.parent.name == ".bm25":
                continue
            try:
                data = json.loads(json_file.read_text())
                if data.get("id") == urn or data.get("qualified_name") == urn.split(":")[-1]:
                    return json.dumps(data, indent=2)
            except (json.JSONDecodeError, OSError):
                continue
        return f"(entity not found: {urn})"
    except Exception as exc:
        return f"(get_schema error: {exc})"


def _tool_get_neighbors(urn: str, brain_root: str, rel_type: str = "", limit: int = 10) -> str:
    """Get neighboring entities for a URN via Neo4j or .brain/ edge scan."""
    try:
        from neo4j import GraphDatabase  # sync driver
        neo4j_url  = os.environ.get("NEO4J_URL",      "bolt://localhost:7687")
        neo4j_user = os.environ.get("NEO4J_USER",     "neo4j")
        neo4j_pass = os.environ.get("NEO4J_PASSWORD", "password")
        driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_pass))
        try:
            with driver.session() as session:
                if rel_type:
                    cypher = (
                        "MATCH (a {urn: $urn})-[r:" + rel_type + "]->(b) "
                        "RETURN b.urn AS urn, b.qualified_name AS name, type(r) AS rel "
                        "LIMIT $limit"
                    )
                else:
                    cypher = (
                        "MATCH (a {urn: $urn})-[r]->(b) "
                        "RETURN b.urn AS urn, b.qualified_name AS name, type(r) AS rel "
                        "LIMIT $limit"
                    )
                result = session.run(cypher, urn=urn, limit=limit)
                rows = result.data()
                if not rows:
                    return "(no neighbors found)"
                lines = [f"[{r['rel']}] {r['urn']} — {r.get('name', '')}" for r in rows]
                return "\n".join(lines)
        finally:
            driver.close()
    except Exception:
        # Fallback: scan .brain/ JSON files for edges stored in metadata
        return _neighbors_from_brain(urn, brain_root, rel_type, limit)


def _tool_get_callers(urn: str, brain_root: str, limit: int = 10) -> str:
    """Get methods that call the given URN via Neo4j or .brain/ scan."""
    try:
        from neo4j import GraphDatabase
        neo4j_url  = os.environ.get("NEO4J_URL",      "bolt://localhost:7687")
        neo4j_user = os.environ.get("NEO4J_USER",     "neo4j")
        neo4j_pass = os.environ.get("NEO4J_PASSWORD", "password")
        driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_pass))
        try:
            with driver.session() as session:
                cypher = (
                    "MATCH (caller)-[:CALLS]->(callee {urn: $urn}) "
                    "RETURN caller.urn AS urn, caller.qualified_name AS name "
                    "LIMIT $limit"
                )
                result = session.run(cypher, urn=urn, limit=limit)
                rows = result.data()
                if not rows:
                    return "(no callers found)"
                lines = [f"{r['urn']} — {r.get('name', '')}" for r in rows]
                return "\n".join(lines)
        finally:
            driver.close()
    except Exception:
        return _callers_from_brain(urn, brain_root, limit)


def _tool_get_git_log(file_path: str, limit: int = 10) -> str:
    """Return recent git commits that touched a file."""
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={limit}", "--oneline", "--", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return f"(git log failed: {result.stderr.strip()})"
        output = result.stdout.strip()
        return output if output else "(no commits found for this file)"
    except Exception as exc:
        return f"(get_git_log error: {exc})"


# ── Brain-file fallbacks when Neo4j is unavailable ─────────────────────────────

def _neighbors_from_brain(urn: str, brain_root: str, rel_type: str, limit: int) -> str:
    root = Path(brain_root)
    found: list[str] = []
    for json_file in root.rglob("*.json"):
        if json_file.parent.name in (".bm25",) or json_file.name in ("index.json", "manifest.json"):
            continue
        try:
            data = json.loads(json_file.read_text())
            edges = data.get("edges", []) or data.get("metadata", {}).get("edges", [])
            for edge in edges:
                if edge.get("source") == urn or edge.get("from") == urn:
                    if rel_type and edge.get("type", edge.get("rel", "")) != rel_type:
                        continue
                    target = edge.get("target") or edge.get("to", "")
                    found.append(f"[{edge.get('type', 'EDGE')}] {target}")
                    if len(found) >= limit:
                        return "\n".join(found)
        except (json.JSONDecodeError, OSError):
            continue
    return "\n".join(found) if found else "(no neighbors found — Neo4j unavailable)"


def _callers_from_brain(urn: str, brain_root: str, limit: int) -> str:
    root = Path(brain_root)
    found: list[str] = []
    for json_file in root.rglob("*.json"):
        if json_file.parent.name in (".bm25",) or json_file.name in ("index.json", "manifest.json"):
            continue
        try:
            data = json.loads(json_file.read_text())
            edges = data.get("edges", []) or data.get("metadata", {}).get("edges", [])
            for edge in edges:
                if edge.get("type", edge.get("rel", "")) == "CALLS" and edge.get("target") == urn:
                    caller_urn = edge.get("source") or edge.get("from", "")
                    found.append(caller_urn)
                    if len(found) >= limit:
                        return "\n".join(found)
        except (json.JSONDecodeError, OSError):
            continue
    return "\n".join(found) if found else "(no callers found — Neo4j unavailable)"


# ── Tool schema definitions ────────────────────────────────────────────────────

_EXPLORATION_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="read_file",
        description=(
            "Read the source code of a file. Use when you need to understand "
            "what a class, module, or function does in detail."
        ),
        parameters=[
            ToolParameter("path", "string", "Absolute path to the file to read"),
            ToolParameter("max_chars", "integer",
                          f"Max characters to return (default {MAX_FILE_CHARS})", required=False),
        ],
    ),
    ToolDefinition(
        name="search_entities",
        description=(
            "Hybrid BM25 + semantic search over .brain/ entities. "
            "Returns URNs, names, and summaries. "
            "Use to find relevant entities when you have a keyword or concept."
        ),
        parameters=[
            ToolParameter("query", "string", "Natural-language or keyword search query"),
            ToolParameter("top_k", "integer", "Maximum results (default 8)", required=False),
        ],
    ),
    ToolDefinition(
        name="get_neighbors",
        description=(
            "Get entities directly connected to a URN in the knowledge graph. "
            "Use to explore what a component depends on or exposes."
        ),
        parameters=[
            ToolParameter("urn", "string", "URN of the entity to expand"),
            ToolParameter("rel_type", "string",
                          "Optional edge type filter (e.g. CALLS, IMPLEMENTS)", required=False),
            ToolParameter("limit", "integer", "Max neighbors to return (default 10)", required=False),
        ],
    ),
    ToolDefinition(
        name="get_callers",
        description=(
            "Find all methods or components that call the given URN. "
            "Use to understand who depends on a function or API."
        ),
        parameters=[
            ToolParameter("urn", "string", "URN of the entity to find callers for"),
            ToolParameter("limit", "integer", "Max callers to return (default 10)", required=False),
        ],
    ),
    ToolDefinition(
        name="get_schema",
        description=(
            "Retrieve the full brain entity JSON for a URN from .brain/. "
            "Includes code snippet, summary, edges, and metadata."
        ),
        parameters=[
            ToolParameter("urn", "string", "URN of the entity"),
        ],
    ),
    ToolDefinition(
        name="get_git_log",
        description=(
            "Get recent git commits that touched a file. "
            "Use to understand change history and contributor intent."
        ),
        parameters=[
            ToolParameter("file_path", "string", "Absolute or repo-relative path to the file"),
            ToolParameter("limit", "integer", "Max commits (default 10)", required=False),
        ],
    ),
]

_SYSTEM_PROMPT = """\
You are an exploration agent. Your job is to gather evidence that will help \
answer a user question that received a low-confidence initial answer.

You have tools to read files, search entities, traverse the knowledge graph, \
and inspect git history. Use them strategically — each tool call costs tokens.

After gathering evidence, output a JSON object:
{
  "findings": "<concise summary of what you found — 200-400 words>",
  "key_entities": ["urn:cb:...", ...],
  "confidence_boost": "<why the new context should improve the answer>"
}

Return ONLY the JSON — no prose before or after.\
"""


# ── Main agent ─────────────────────────────────────────────────────────────────

class ExplorationAgent:
    """
    Iterative exploration sub-agent for hard queries (ADR-0061 E1).

    Call .explore(question, initial_summary) to get an ExplorationResult
    whose .context field can be prepended to a second LLM call.
    """

    def __init__(
        self,
        repo_path: str | None = None,
        workspace_id: str = "",
        brain_root: str | None = None,
    ):
        self.repo_path   = repo_path or os.environ.get("BRAIN_REPO_ROOT", "")
        self.workspace_id = workspace_id or os.environ.get("BRAIN_WORKSPACE_ID", "")
        self.brain_root  = brain_root or (
            str(Path(self.repo_path) / ".brain") if self.repo_path else ""
        )
        self._provider = get_provider()

    # ── Public interface ───────────────────────────────────────────────────────

    async def explore(self, question: str, initial_summary: str) -> ExplorationResult:
        """
        Run up to MAX_ROUNDS exploration rounds.
        Returns gathered context + cited URNs.
        """
        user_msg = (
            f"ORIGINAL QUESTION:\n{question}\n\n"
            f"INITIAL ANSWER (low confidence):\n{initial_summary}\n\n"
            "Use your tools to gather additional evidence. "
            "Focus on entities, files, or relationships that the initial answer missed."
        )

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=user_msg),
        ]

        tool_calls_made = 0
        rounds_done = 0

        for round_num in range(MAX_ROUNDS):
            rounds_done = round_num + 1

            response = await self._provider.chat_with_tools(
                messages=messages,
                tools=_EXPLORATION_TOOLS,
                role=TaskRole.BALANCED,
                max_tokens=2048,
            )

            log.debug(
                "[exploration] round",
                round=round_num + 1,
                wants_tool_call=response.wants_tool_call,
                tool_calls=[tc.name for tc in response.tool_calls],
            )

            if not response.wants_tool_call:
                # Agent produced its final synthesis
                return self._parse_result(
                    response.content,
                    rounds_taken=rounds_done,
                    tool_calls_made=tool_calls_made,
                )

            # Append assistant turn
            messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            # Execute tool calls (cap per round)
            calls_this_round = 0
            for tc in response.tool_calls:
                if calls_this_round >= MAX_TOOL_CALLS_PER_ROUND:
                    break
                result_text = await asyncio.get_event_loop().run_in_executor(
                    None, self._execute_tool, tc
                )
                log.debug(
                    "[exploration] tool result",
                    tool=tc.name,
                    result_len=len(result_text),
                    preview=result_text[:100],
                )
                messages.append(ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=tc.call_id,
                ))
                tool_calls_made += 1
                calls_this_round += 1

        # Max rounds reached — ask for synthesis
        messages.append(ChatMessage(
            role="user",
            content="Summarise your findings now. Return the JSON result.",
        ))
        final = await self._provider.chat_with_tools(
            messages=messages,
            tools=_EXPLORATION_TOOLS,
            role=TaskRole.BALANCED,
            max_tokens=1024,
        )
        return self._parse_result(
            final.content,
            rounds_taken=rounds_done,
            tool_calls_made=tool_calls_made,
        )

    # ── Tool dispatch ──────────────────────────────────────────────────────────

    def _execute_tool(self, tc: ToolCall) -> str:
        args: dict[str, Any] = tc.arguments or {}
        try:
            if tc.name == "read_file":
                return _tool_read_file(
                    path=str(args.get("path", "")),
                    max_chars=int(args.get("max_chars", MAX_FILE_CHARS)),
                )
            if tc.name == "search_entities":
                return _tool_search_entities(
                    query=str(args.get("query", "")),
                    brain_root=self.brain_root,
                    workspace_id=self.workspace_id,
                    top_k=int(args.get("top_k", 8)),
                )
            if tc.name == "get_neighbors":
                return _tool_get_neighbors(
                    urn=str(args.get("urn", "")),
                    brain_root=self.brain_root,
                    rel_type=str(args.get("rel_type", "")),
                    limit=int(args.get("limit", 10)),
                )
            if tc.name == "get_callers":
                return _tool_get_callers(
                    urn=str(args.get("urn", "")),
                    brain_root=self.brain_root,
                    limit=int(args.get("limit", 10)),
                )
            if tc.name == "get_schema":
                return _tool_get_schema(
                    urn=str(args.get("urn", "")),
                    brain_root=self.brain_root,
                )
            if tc.name == "get_git_log":
                return _tool_get_git_log(
                    file_path=str(args.get("file_path", "")),
                    limit=int(args.get("limit", 10)),
                )
            return json.dumps({"error": f"Unknown tool: {tc.name}"})
        except Exception as exc:
            log.warning("[exploration] tool error", tool=tc.name, error=str(exc))
            return json.dumps({"error": str(exc)})

    # ── Output parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_result(text: str, rounds_taken: int, tool_calls_made: int) -> ExplorationResult:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text.strip())
            findings  = data.get("findings", text)
            key_urns  = data.get("key_entities", [])
            boost     = data.get("confidence_boost", "")
            context   = f"## Exploration Findings\n\n{findings}"
            if boost:
                context += f"\n\n**Why this helps:** {boost}"
            return ExplorationResult(
                context=context,
                citations=[u for u in key_urns if isinstance(u, str)],
                rounds_taken=rounds_taken,
                tool_calls_made=tool_calls_made,
            )
        except json.JSONDecodeError:
            # Agent returned prose — still useful as context
            return ExplorationResult(
                context=f"## Exploration Findings\n\n{text}",
                rounds_taken=rounds_taken,
                tool_calls_made=tool_calls_made,
            )
