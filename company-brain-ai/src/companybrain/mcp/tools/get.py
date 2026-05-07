"""brain_get — return one entity's full JSON."""
import json
import os
from pathlib import Path

from companybrain.mcp.tools import Tool, register
from companybrain.store import JsonFileBrainStore


_SCHEMA = {
    "name": "brain_get",
    "description": "Get a single brain entity by its URN. Returns the full JSON.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string", "description": "URN of the entity"},
            "repo":      {"type": "string"},
        },
        "required": ["entity_id"],
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    store = JsonFileBrainStore(repo / ".brain")
    entity = await store.read(args["entity_id"])
    if entity is None:
        return f"(not found: {args['entity_id']})"
    return json.dumps(entity.to_dict(), indent=2)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
