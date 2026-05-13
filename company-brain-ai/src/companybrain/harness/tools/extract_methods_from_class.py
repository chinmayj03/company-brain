"""extract_methods_from_class — batched ContextAgent extraction (ADR-0048).

This tool extracts entities from a source file AND persists them directly to
the brain store. We discovered (`.e2e-session/fixes-summary.md`) that the
"model relays entities back into write_to_brain" pattern fails in practice:
once the conversation hits ~80K input tokens the model loses track of the
entity payloads and starts calling write_to_brain with `entities: []` in a
loop, producing 0 disk writes despite ~50 successful tool calls.

By persisting from inside this tool we make the workflow context-window-safe:
the model receives a compact summary (qnames + written count), not the full
entity payload it needs to remember and re-emit.
"""
from __future__ import annotations

import hashlib
import os
import structlog
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from companybrain.agents.context_agent import ContextAgent, ContextAgentResult
from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter
from companybrain.pipeline.code_chunker import _LANGUAGE_MAP, MethodChunk
from companybrain.store.identity import RepoUnknownForUrn, to_urn, workspace_slug_for
from companybrain.store.base import BrainEntity
from companybrain.util.file_cache import FileCache

log = structlog.get_logger(__name__)

# Auto-persist defaults true; operators can disable via env for diagnostic
# runs where they want to inspect the raw extraction output without it
# landing on disk.
_AUTO_PERSIST = os.environ.get("BRAIN_EXTRACT_AUTOPERSIST", "true").strip().lower() not in (
    "0", "false", "no", "off",
)


@register_tool(
    name="extract_methods_from_class",
    description=(
        "Run the batched ContextAgent extractor on a set of methods from one source "
        "file AND persist the extracted entities to the brain store. Returns "
        "{written, skipped, errors, qnames_written} — a compact summary, NOT the "
        "full entity payload. You DO NOT need to call write_to_brain afterwards. "
        "Prefer one batched call per file over many per-method calls."
    ),
    parameters=[
        ToolParameter("file", "string", "Absolute path to the source file."),
        ToolParameter("methods", "array",
                      "List of method qnames to extract, e.g. ['UserService.findById']. "
                      "Must all live in the same file."),
        ToolParameter("language", "string",
                      "Source language (java, python, typescript, ...). "
                      "Inferred from file extension when omitted.",
                      required=False),
    ],
    requires=(Capability.READ_REPO, Capability.LLM_CALL, Capability.WRITE_BRAIN),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    file_path = args["file"]
    qnames = list(args.get("methods") or [])
    if not qnames:
        return {"written": 0, "skipped": 0, "errors": ["empty methods list"], "qnames_written": []}

    raw_language = args.get("language") or _LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "")
    language = raw_language or "unknown"

    cache = context.get("file_cache")
    if isinstance(cache, FileCache):
        body = cache.read(file_path)
    else:
        try:
            body = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return {
                "written":       0,
                "skipped":       0,
                "errors":        [f"could not read {file_path}: {exc}"],
                "qnames_written": [],
            }

    chunks = [
        MethodChunk(
            file_path=file_path,
            qname=q,
            kind="method",
            body=body,
            header_context="",
            import_context="",
            body_hash=hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest(),
            language=language,
        )
        for q in qnames
    ]

    agent = ContextAgent()
    results: list[ContextAgentResult] = await agent.extract_batch(chunks)

    repo_path_str = str(context.get("repo_path") or "")
    repo_name = Path(repo_path_str).name if repo_path_str else ""

    # Diagnostic / opt-out mode: return the raw payload like the old contract.
    # The model is then responsible for forwarding into write_to_brain.
    if not _AUTO_PERSIST:
        return {
            "auto_persist": False,
            "entities":     [_to_dict(r, repo=repo_name, file_path=file_path) for r in results],
        }

    # Auto-persist path: write directly to the brain store and return a
    # summary the model can reason about in O(1) tokens regardless of how
    # large the extraction was.
    workspace_id = context.get("workspace_id")
    if not workspace_id:
        return {
            "written":        0,
            "skipped":        0,
            "errors":         ["context missing workspace_id"],
            "qnames_written": [],
        }

    # Lazy import to avoid a cycle between this tool and write_to_brain.
    from companybrain.harness.tools.write_to_brain import _get_store

    store = _get_store(context)
    run_id = context.get("run_id") or workspace_id
    tenant = workspace_slug_for(workspace_id)

    written: list[str] = []
    errors: list[str] = []
    for r in results:
        try:
            brain_entity = _result_to_brain_entity(
                r, repo=repo_name, file_path=file_path, tenant=tenant,
            )
        except (RepoUnknownForUrn, ValueError) as exc:
            errors.append(f"{r.qname}: {exc}")
            continue
        if brain_entity is None:
            errors.append(f"{r.qname}: ContextAgent returned no entity")
            continue
        try:
            await store.write(brain_entity, run_id=run_id, workspace_id=workspace_id)
        except Exception as exc:   # noqa: BLE001 — keep the tool fault-tolerant
            errors.append(f"{r.qname}: write failed: {exc}")
            continue
        written.append(r.qname)

    log.info(
        "extract_methods_from_class.persisted",
        file=file_path,
        attempted=len(results),
        written=len(written),
        errors=len(errors),
    )
    return {
        "written":        len(written),
        "skipped":        max(len(results) - len(written) - len(errors), 0),
        "errors":         errors,
        "qnames_written": written,
    }


def _result_to_brain_entity(
    r: ContextAgentResult,
    *,
    repo: str,
    file_path: str,
    tenant: str,
) -> BrainEntity | None:
    """Build a BrainEntity directly from the ContextAgent result.

    Mirrors write_to_brain._to_brain_entity but does not require the caller
    to assemble an entity dict first — we already have the structured
    ContextAgentResult so we can skip a serialise/deserialise round trip.
    """
    if r.entity is None:
        return None
    qname = r.qname or ""
    if not qname:
        raise ValueError("entity missing qname")
    if not repo:
        raise RepoUnknownForUrn("entity missing repo (context.repo_path unset)")

    entity_type = r.entity.entity_type or "function_node"
    urn = to_urn(
        tenant=tenant,
        domain="code",
        repo=repo,
        entity_type=entity_type,
        qualified_name=qname,
    )
    relationships: list[dict[str, Any]] = []
    for e in r.edges or []:
        target = getattr(e, "target", "")
        if not target:
            continue
        relationships.append({
            "target_id":  target,
            "edge_type":  getattr(e, "edge_type", "CALLS"),
            "confidence": float(getattr(e, "confidence", 0.8) or 0.8),
            "source":     (getattr(e, "evidence", "") or "harness/extract_methods_from_class")[:200],
        })

    metadata: dict[str, Any] = {}
    if r.business_context:
        metadata["business_context"] = r.business_context
    if r.entity.signature:
        metadata["signature"] = r.entity.signature
    if r.entity.query_text:
        metadata["query_text"] = r.entity.query_text
    if r.entity.confidence is not None:
        metadata["confidence"] = r.entity.confidence

    return BrainEntity(
        id=urn,
        entity_type=entity_type,
        repo=repo,
        file=file_path,
        qualified_name=qname,
        t1_summary=getattr(r.entity, "t1_summary", "") or "",
        t0_token=getattr(r.entity, "t0_token", "") or "",
        t1_token=getattr(r.entity, "t1_token", "") or "",
        metadata=metadata,
        relationships=relationships,
        version_hash=getattr(r.entity, "version_hash", "") or "",
        last_updated_by="harness/extract_methods_from_class",
    )


def _to_dict(r: ContextAgentResult, *, repo: str, file_path: str) -> dict[str, Any]:
    """Legacy/diagnostic shape: only used when BRAIN_EXTRACT_AUTOPERSIST=false.

    Always includes `repo` and `file` so the model can pass the result
    straight into write_to_brain.
    """
    out: dict[str, Any] = {
        "qname":            r.qname,
        "repo":             repo,
        "file":             file_path,
        "edges":            [_edge_to_dict(e) for e in r.edges],
        "business_context": r.business_context or {},
    }
    if r.entity is not None:
        out["entity_type"] = r.entity.entity_type
        out["signature"]   = r.entity.signature
        out["confidence"]  = r.entity.confidence
        out["query_text"]  = r.entity.query_text
    return out


def _edge_to_dict(e: Any) -> dict[str, Any]:
    return {
        "edge_type":  getattr(e, "edge_type", ""),
        "target":     getattr(e, "target", ""),
        "confidence": getattr(e, "confidence", 0.0),
        "evidence":   (getattr(e, "evidence", "") or "")[:200],
    }


def _iter_qnames(values: Iterable[Any]) -> list[str]:
    return [str(v) for v in values if v is not None]
