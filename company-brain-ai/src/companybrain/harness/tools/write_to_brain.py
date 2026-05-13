"""write_to_brain — persist extracted entities + edges to the JSON brain store."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from companybrain.harness.permissions import Capability
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
    requires=(Capability.WRITE_BRAIN,),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    import structlog as _sl
    _log = _sl.get_logger(__name__)
    entities = list(args.get("entities") or [])
    standalone_edges = list(args.get("edges") or [])
    _log.info(
        "write_to_brain.call",
        n_entities=len(entities),
        n_edges=len(standalone_edges),
        sample_qnames=[e.get("qname") for e in entities[:3]],
        sample_repo=[e.get("repo") for e in entities[:3]],
    )

    workspace_id = context.get("workspace_id")
    if not workspace_id:
        return {"written": 0, "skipped": 0, "errors": ["context missing workspace_id"]}

    store = _get_store(context)
    run_id = context.get("run_id") or workspace_id
    tenant = workspace_slug_for(workspace_id)

    # Fallback repo identifier: directory name of the repo_path in context.
    # When the model forwards extract_methods_from_class output it usually
    # remembers to include `repo`, but if it doesn't we'd previously raise
    # RepoUnknownForUrn and drop the entity silently. Now we backfill from
    # context so the write still lands.
    repo_path = context.get("repo_path") or ""
    default_repo = os.path.basename(str(repo_path).rstrip(os.sep)) if repo_path else ""

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
        if not ent.get("repo") and default_repo:
            ent = {**ent, "repo": default_repo}
        try:
            brain_entity = _to_brain_entity(ent, tenant=tenant)
        except (RepoUnknownForUrn, ValueError) as exc:
            errors.append(f"{ent.get('qname','?')}: {exc}")
            continue
        await store.write(brain_entity, run_id=run_id, workspace_id=workspace_id)
        written += 1

    skipped = max(len(entities) - written - len(errors), 0)

    # Track call count + cumulative writes to detect the harness "write loop"
    # regression where the model batches one entity per call ad infinitum and
    # never calls finalize_brain. After the 3rd call OR a zero-write batch we
    # return an explicit nudge in the result text so the model breaks out.
    counter = int(context.get("_write_to_brain_calls", 0)) + 1
    cumulative = int(context.get("_write_to_brain_total", 0)) + written
    context["_write_to_brain_calls"] = counter
    context["_write_to_brain_total"] = cumulative

    next_step: str | None = None
    if written == 0:
        next_step = ("No new entities written. Call finalize_brain next to "
                     "commit the previously-buffered writes and end the run.")
    elif counter >= 3:
        next_step = (f"You have made {counter} write_to_brain calls "
                     f"({cumulative} entities total). Call finalize_brain next "
                     "and stop unless there is an explicit gap to fill.")

    result: dict[str, Any] = {"written": written, "skipped": skipped, "errors": errors}
    if next_step:
        result["next_step"] = next_step
    return result


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
