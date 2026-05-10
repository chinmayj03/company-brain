"""finalize_brain — close the run and update the brain manifest."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter
from companybrain.store.json_store import JsonFileBrainStore


@register_tool(
    name="finalize_brain",
    description=(
        "Close the extraction run and update the brain manifest. Call exactly "
        "once after the last write_to_brain. Returns "
        "{run_id, manifest_path, entity_count}."
    ),
    parameters=[
        ToolParameter("workspace_id", "string",
                      "Workspace identifier for the run. Should match context.workspace_id.",
                      required=False),
    ],
    requires=(Capability.WRITE_BRAIN,),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id") or context.get("workspace_id")
    if not workspace_id:
        return {"error": "workspace_id missing from both args and context"}

    store = context.get("brain_store")
    if not isinstance(store, JsonFileBrainStore):
        repo_path = context.get("repo_path")
        brain_root = (
            Path(repo_path) / ".brain"
            if repo_path
            else Path(f"/tmp/cb_brain_{workspace_id}")
        )
        store = JsonFileBrainStore(brain_root)
        context["brain_store"] = store

    run_id = context.get("run_id") or workspace_id
    await store.commit_run(run_id)

    entity_count = 0
    async for _ in store.list_ids():
        entity_count += 1

    return {
        "run_id":        run_id,
        "manifest_path": str(store.root / "manifest.json"),
        "entity_count":  entity_count,
    }
