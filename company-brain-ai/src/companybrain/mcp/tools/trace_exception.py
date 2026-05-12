"""ADR-0061 E3 — trace_exception MCP tool.

Given an exception class/type name, walk the existing THROWS / CATCHES edges
in Neo4j to return a tree of:

  - thrown_by:   methods that throw the exception (THROWS edges in)
  - caught_by:   handlers that catch it          (CATCHES edges in)
  - wrapped_by:  handlers that wrap it in another exception (WRAPS_EXCEPTION)
  - unhandled_at: methods on the throw side whose call chains reach an
                  endpoint without ever being caught (best-effort heuristic)

The exception is resolved by matching the entity's qualified_name or the
``name`` property suffix — the agent can pass either a fully-qualified class
or a bare name like ``DatabaseOperationException``.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from neo4j import AsyncGraphDatabase

from companybrain.mcp.tools import Tool, register


_SCHEMA = {
    "name": "trace_exception",
    "description": (
        "Trace where an exception is thrown, caught, and re-wrapped. Walks "
        "THROWS / CATCHES / WRAPS_EXCEPTION edges in the brain graph and "
        "returns a structured tree the agent can present to the user."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exception class — qualified or bare "
                               "(e.g. 'DatabaseOperationException').",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Max sites per category.",
            },
        },
        "required": ["name"],
    },
}


def _driver():
    return AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )


async def trace_exception(name: str, limit: int = 25) -> dict[str, Any]:
    """Programmatic entry point.  Returns the tree directly (no string
    serialisation) so callers — including the acceptance test suite — can
    assert on the structure."""
    limit = max(1, min(int(limit), 200))
    driver = _driver()
    try:
        async with driver.session() as session:
            target = await _resolve_target(session, name)
            if target is None:
                return {
                    "exception": name,
                    "resolved": False,
                    "thrown_by": [], "caught_by": [],
                    "wrapped_by": [], "unhandled_at": [],
                    "message": "exception class not found in graph",
                }
            thrown = await _sites(session, target["id"], "THROWS", limit)
            caught = await _sites(session, target["id"], "CATCHES", limit)
            wrapped = await _sites(session, target["id"], "WRAPS_EXCEPTION", limit)
            unhandled = await _unhandled_paths(session, target["id"], limit)
            return {
                "exception": target["name"],
                "urn": target["id"],
                "resolved": True,
                "thrown_by":   thrown,
                "caught_by":   caught,
                "wrapped_by":  wrapped,
                "unhandled_at": unhandled,
            }
    finally:
        await driver.close()


async def _resolve_target(session, name: str) -> Optional[dict]:
    """Find the URN for an exception class. Tries exact qualified-name first,
    then a suffix match (so the agent can pass either form)."""
    q = (
        "MATCH (n) "
        "WHERE n.qualified_name = $name OR n.name = $name "
        "   OR n.qualified_name ENDS WITH $suffix "
        "RETURN n.id AS id, coalesce(n.qualified_name, n.name) AS name "
        "LIMIT 1"
    )
    bare = name.rsplit(".", 1)[-1]
    result = await session.run(q, name=name, suffix=f".{bare}")
    row = await result.single()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"]}


async def _sites(session, urn: str, edge: str, limit: int) -> list[dict]:
    """Return the entities connected to ``urn`` via ``edge`` (incoming)."""
    q = (
        f"MATCH (src)-[r:{edge}]->(target {{id: $urn}}) "
        "RETURN DISTINCT src.id AS id, "
        "       coalesce(src.qualified_name, src.name) AS name, "
        "       src.file AS file "
        f"LIMIT {limit}"
    )
    result = await session.run(q, urn=urn)
    rows = await result.data()
    return [
        {"urn": r["id"], "name": r["name"] or r["id"],
         "file": r["file"] or ""}
        for r in rows
    ]


async def _unhandled_paths(session, urn: str, limit: int) -> list[dict]:
    """Methods that THROWS the target and whose chain of callers contains
    *no* CATCHES-edge handler. The heuristic is intentionally cheap: we
    just check the immediate throwing method's direct callers for a
    CATCHES edge into the same exception type. Anything without one is
    flagged as 'propagates' — the agent can drill in if the user wants.
    """
    q = (
        "MATCH (src)-[:THROWS]->(target {id: $urn}) "
        "OPTIONAL MATCH (caller)-[:CALLS]->(src) "
        "WHERE NOT EXISTS { (caller)-[:CATCHES]->(target) } "
        "RETURN src.id AS thrower_id, "
        "       coalesce(src.qualified_name, src.name) AS thrower, "
        "       count(caller) AS uncovered_callers "
        f"LIMIT {limit}"
    )
    result = await session.run(q, urn=urn)
    rows = await result.data()
    out: list[dict] = []
    for r in rows:
        out.append({
            "urn": r["thrower_id"],
            "name": r["thrower"] or r["thrower_id"],
            "uncovered_callers": int(r.get("uncovered_callers") or 0),
        })
    return out


def _format(tree: dict[str, Any]) -> str:
    """Human-readable rendering for the JSON-RPC stdio transport."""
    lines = [f"trace_exception({tree['exception']!r})"]
    if not tree.get("resolved"):
        lines.append(f"  {tree.get('message', 'not found')}")
        return "\n".join(lines)
    lines.append(f"  urn: {tree.get('urn', '')}")
    for key in ("thrown_by", "caught_by", "wrapped_by", "unhandled_at"):
        rows = tree.get(key, [])
        lines.append(f"  {key} ({len(rows)}):")
        for row in rows:
            file_hint = f"  [{row['file']}]" if row.get("file") else ""
            extra = ""
            if "uncovered_callers" in row:
                extra = f"  uncovered_callers={row['uncovered_callers']}"
            lines.append(f"    - {row.get('name', '')}{file_hint}{extra}")
    return "\n".join(lines)


async def _handle(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "ERROR: 'name' is required."
    limit = int(args.get("limit", 25))
    try:
        tree = await trace_exception(name, limit=limit)
    except Exception as e:
        return f"ERROR: trace_exception failed: {e}"
    return _format(tree)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
