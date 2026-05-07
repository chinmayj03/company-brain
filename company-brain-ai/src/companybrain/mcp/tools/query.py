"""brain_query — main entry point for LLM consumers."""
from __future__ import annotations
import json
import os
from pathlib import Path

from neo4j import AsyncGraphDatabase

from companybrain.assembly.smart_zone import SmartZoneAssembler
from companybrain.assembly.types import TokenBudget
from companybrain.mcp.tools import Tool, register
from companybrain.store import JsonFileBrainStore


_SCHEMA = {
    "name": "brain_query",
    "description": (
        "Assemble company-brain context for a task. "
        "Returns T0/T1/T2 tiered context within a token budget, "
        "plus blast radius and business context for the relevant entities. "
        "Use this BEFORE making any significant code change to learn what "
        "components, APIs, data models, and assumptions are involved."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task":         {"type": "string", "description": "Natural-language task description"},
            "entities":     {"type": "array", "items": {"type": "string"},
                              "description": "Optional URN seeds; default is hybrid retrieval"},
            "token_budget": {"type": "integer", "default": 6000},
            "repo":         {"type": "string", "description": "Repo path; defaults to BRAIN_REPO_ROOT env"},
        },
        "required": ["task"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")

    store = JsonFileBrainStore(repo / ".brain")
    driver = AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        assembler = SmartZoneAssembler(
            brain_root=repo / ".brain", workspace_id=workspace_id,
            store=store, neo4j_driver=driver,
        )
        budget = TokenBudget(total=int(args.get("token_budget", 6000)))
        payload = await assembler.assemble(
            task=args["task"],
            entities=args.get("entities"),
            budget=budget,
        )
        return payload.rendered
    finally:
        await driver.close()


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
