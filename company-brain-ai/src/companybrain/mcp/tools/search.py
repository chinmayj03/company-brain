"""brain_search — hybrid keyword + semantic search."""
import os
from pathlib import Path

from companybrain.mcp.tools import Tool, register
from companybrain.retrieval.hybrid_search import HybridSearcher
from companybrain.store.identity import workspace_slug_for


_SCHEMA = {
    "name": "brain_search",
    "description": "Hybrid (BM25 + semantic) search across all brain entities.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query":        {"type": "string"},
            "top_k":        {"type": "integer", "default": 10},
            "entity_types": {"type": "array", "items": {"type": "string"}},
            "repo":         {"type": "string"},
        },
        "required": ["query"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "")
    searcher = HybridSearcher(
        brain_root=repo,
        workspace_slug=workspace_slug_for(workspace_id),
    )
    hits = searcher.search(
        args["query"],
        top_k=args.get("top_k", 10),
        entity_types=args.get("entity_types"),
    )
    if not hits:
        return "(no results)"
    lines = []
    for h in hits:
        lines.append(f"{h.score:6.4f}  {h.urn}")
        if h.payload.get("t1_summary"):
            lines.append(f"        → {h.payload['t1_summary']}")
    return "\n".join(lines)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
