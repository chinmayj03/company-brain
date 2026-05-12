"""
Universal Extraction Stage 0.5b — ADR-0057.

Walks the repo with ``FileWalker.walk_universal()`` and dispatches each
non-code file to the matching extractor in ``companybrain.extractors``.

Phase 1 scope: count entities per kind and surface them in telemetry.
Persistence of the new entity types (ContainerImage, ConfigKey, Dependency,
WorkflowJob, …) into Neo4j is owned by a follow-up PR — see the deferred
work list in docs/adrs/ADR-0057-universal-file-extraction.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

from companybrain.extractors.dispatch import get_extractor
from companybrain.models.entities import ExtractedBatch, PipelineStartRequest
from companybrain.pipeline.file_walker import FileWalker

log = structlog.get_logger(__name__)

ProgressFn = Callable[..., Awaitable[None]]


async def run_universal_extraction(
    *,
    request: PipelineStartRequest,
    progress: ProgressFn,
) -> dict[str, Any]:
    """
    Run the universal extractors over every repo in ``request.repos`` and
    return a telemetry dict that the orchestrator appends to
    ``stages_summary``.

    Returns
    -------
    dict
        ``{"files": int, "by_kind": {kind: count}, "entities": int}``.
        ``files`` is the count of non-code files claimed by some extractor;
        ``by_kind`` is the per-kind file count; ``entities`` is the total
        number of new ADR-0057 entities produced across all batches.
    """
    await progress("0.5b", "🧰", "Universal extraction — docs / config / infra / CI / manifests")

    total_files = 0
    total_entities = 0
    by_kind: dict[str, int] = {}

    for repo_cfg in request.repos:
        root_str = repo_cfg.local_path or repo_cfg.url
        if not root_str:
            continue
        root = Path(root_str)
        if not root.exists() or not root.is_dir():
            log.debug("Universal extraction: repo path missing", repo=str(root))
            continue

        walker = FileWalker(repo_root=root)
        for info in walker.walk_universal():
            if info.extractor_kind == "code":
                continue  # legacy chunker owns these
            extractor = get_extractor(info.path)
            if extractor is None:
                continue
            try:
                content = info.path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            try:
                batch: ExtractedBatch = extractor.extract(
                    info.path, content, repo=root.name
                )
            except Exception as e:
                log.debug("Universal extractor failed", path=info.relative_path, error=str(e))
                continue

            total_files += 1
            by_kind[info.extractor_kind] = by_kind.get(info.extractor_kind, 0) + 1
            total_entities += batch.entity_count

    await progress(
        "0.5b", "✅",
        f"{total_files} files, {total_entities} entities ({_kind_summary(by_kind)})",
        files=total_files, entities=total_entities, by_kind=by_kind,
    )
    return {"files": total_files, "by_kind": by_kind, "entities": total_entities}


def _kind_summary(by_kind: dict[str, int]) -> str:
    if not by_kind:
        return "no files claimed"
    return ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
