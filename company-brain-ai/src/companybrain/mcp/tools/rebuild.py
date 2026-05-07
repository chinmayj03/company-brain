"""brain_rebuild — force-rebuild Postgres + Neo4j + Qdrant from .brain/ JSONs."""
import os
from pathlib import Path

from companybrain.cli_helpers.brain_rebuild import rebuild_from_json
from companybrain.mcp.tools import Tool, register


_SCHEMA = {
    "name": "brain_rebuild",
    "description": "Replay every .brain/ JSON into Postgres + Neo4j + Qdrant. "
                    "Use after wiping any of the projection stores.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "repo": {"type": "string"},
        },
    },
}


async def _handle(args: dict) -> str:
    repo = Path(args.get("repo") or os.getenv("BRAIN_REPO_ROOT", "."))
    workspace_id = os.getenv("BRAIN_WORKSPACE_ID", "")
    await rebuild_from_json(repo, workspace_id)
    return f"rebuilt brain from {repo}/.brain/"


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
