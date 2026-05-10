"""write_to_brain — persist extracted entities + edges to the JSON brain store."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter
from companybrain.store.base import BrainEntity
from companybrain.store.identity import RepoUnknownForUrn, to_urn, workspace_slug_for
from companybrain.store.json_store import JsonFileBrainStore

_DEFAULT_ENTITY_TYPE = "function_node"


@register_tool(
    name="write_to_brain",
    description=(
        "Persist a batch of extracted entities + edges to the JSON brain store. "
        "Idempotent on (workspace_id, entity_id) — safe to call multiple times. "
        "Returns {written: N, skipped: M, errors: [...]}. Always call finalize_brain "
        "exactly once after the last write_to_brain call."
    ),
    parameters=[
        ToolParameter("entities", "array",
                      "List of {qname, entity_type, repo, file, signature?, code_snippet?, "
                      "metadata?, edges?:[{target, edge_type, confidence?, evidence?}]} dicts."),
        ToolParameter("edges", "array",
                      "Optional list of {source_qname, target, edge_type, confidence?, evidence?} "
                      "dicts. Edges may also be inlined under each entity.",
                      required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    entities = list(args.get("entities") or [])
    standalone_edges = list(args.get("edges") or [])

    workspace_id = context.get("workspace_id")
    if not workspace_id:
        return {"written": 0, "skipped": 0, "errors": ["context missing workspace_id"]}

    store = _get_store(context)
    run_id = context.get("run_id") or workspace_id
    tenant = workspace_slug_for(workspace_id)

    by_qname = {e.get("qname"): e for e in entities if e.get("qname")}
    for edge in standalone_edges:
        src = edge.get("source_qname")
        if not src:
            continue
        ent = by_qname.get(src)
        if ent is None:
            continue
        ent.setdefault("edges", []).append(edge)

    written = 0
    errors: list[str] = []

    for ent in entities:
        try:
            brain_entity = _to_brain_entity(ent, tenant=tenant)
        except (RepoUnknownForUrn, ValueError) as exc:
            errors.append(f"{ent.get('qname','?')}: {exc}")
            continue
        await store.write(brain_entity, run_id=run_id, workspace_id=workspace_id)
        written += 1

    skipped = max(len(entities) - written - len(errors), 0)
    return {"written": written, "skipped": skipped, "errors": errors}


def _get_store(context: dict[str, Any]) -> JsonFileBrainStore:
    """Reuse a brain store from context, or build one lazily under .brain/."""
    store = context.get("brain_store")
    if isinstance(store, JsonFileBrainStore):
        return store

    workspace_id = context["workspace_id"]
    repo_path = context.get("repo_path")
    brain_root = (
        Path(repo_path) / ".brain"
        if repo_path
        else Path(f"/tmp/cb_brain_{workspace_id}")
    )
    store = JsonFileBrainStore(brain_root)
    # Cache for subsequent write/finalize calls in the same run.
    context["brain_store"] = store
    return store


def _to_brain_entity(ent: dict[str, Any], *, tenant: str) -> BrainEntity:
    qname = ent.get("qname") or ""
    if not qname:
        raise ValueError("entity missing qname")
    entity_type = ent.get("entity_type") or _DEFAULT_ENTITY_TYPE
    repo = ent.get("repo") or ""
    if not repo:
        raise RepoUnknownForUrn("entity missing repo")
    file_path = ent.get("file") or ""

    urn = to_urn(
        tenant=tenant,
        domain="code",
        repo=repo,
        entity_type=entity_type,
        qualified_name=qname,
    )

    relationships: list[dict[str, Any]] = []
    for e in ent.get("edges") or []:
        if not isinstance(e, dict):
            continue
        target = e.get("target")
        if not target:
            continue
        relationships.append({
            "target_id":  target,
            "edge_type":  e.get("edge_type", "CALLS"),
            "confidence": float(e.get("confidence") or 0.8),
            "source":     e.get("evidence") or "harness/extract_methods_from_class",
        })

    metadata = dict(ent.get("metadata") or {})
    for key in ("signature", "code_snippet", "query_text", "business_context"):
        if key in ent and ent[key] is not None:
            metadata.setdefault(key, ent[key])

    return BrainEntity(
        id=urn,
        entity_type=entity_type,
        repo=repo,
        file=file_path,
        qualified_name=qname,
        t1_summary=ent.get("t1_summary", ""),
        t0_token=ent.get("t0_token", ""),
        t1_token=ent.get("t1_token", ""),
        metadata=metadata,
        relationships=relationships,
        version_hash=ent.get("version_hash", ""),
        last_updated_by="harness/write_to_brain",
    )


# Re-export for tests that patch the asyncio.gather entry point.
gather = asyncio.gather  # noqa: F401
