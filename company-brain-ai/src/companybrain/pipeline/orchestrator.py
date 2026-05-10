"""
Pipeline Orchestrator — runs the multi-pass LLM extraction for one API endpoint.

Stages:
  0a. Code tracing     — find handler → services → repos → models (live source)
  0b. Git collection   — commit history for context synthesis (non-fatal)
  1.  Entity extraction from live code (one LLM call per class)
  2.  Relationship extraction
  3.  Business context synthesis (uses git history as "why" signal)
  4.  Gap detection
  5.  Graph population

Progress logging:
  run_pipeline() accepts an optional `on_progress` async callback.
  It is called after every significant step with a structured dict so the
  pipeline route can push live updates to Redis and the frontend can poll them.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path as _Path
from typing import Awaitable, Callable, Optional

import httpx
import structlog

# ── Integration bridge (ADR-0008) ─────────────────────────────────────────────
# After LLM pipeline completes, trigger the TypeScript structural extractor via
# the cb-api service so that Neo4j structural nodes are always in sync with the
# Postgres semantic graph.
CB_API_URL = os.getenv("CB_API_URL", "http://cb-api:8090")

from companybrain.collectors.code_tracer import CodeTracer
from companybrain.collectors.git_collector import GitCollector, CollectorConfig
from companybrain.pipeline.entity_extractor import EntityExtractor
from companybrain.pipeline.intent_synthesizer import IntentSynthesizer, function_context_to_dict
from companybrain.pipeline.import_graph import ImportGraphAnalyzer
from companybrain.pipeline.memory_tokenizer import MemoryTokenizer, memory_tokens_to_metadata
from companybrain.pipeline.relationship_extractor import RelationshipExtractor
from companybrain.pipeline.context_synthesizer import ContextSynthesizer
from companybrain.pipeline.gap_detector import GapDetector
from companybrain.pipeline.entity_filter import filter_entities
from companybrain.pipeline.context_hierarchy import L2SharedContext
from companybrain.pipeline.context_manager_agent import ContextManagerAgent
from companybrain.pipeline.shared_context_accumulator import SharedContextAccumulator
from companybrain.pipeline.assumption_miner import mine_assumptions  # ADR-0017
from companybrain.pipeline.derived_query_extractor import DerivedQueryExtractor  # Tier 1.C
from companybrain.pipeline.extraction_loop import ExtractionLoop  # ADR-0041 Phase 2
from companybrain.graph.java_client import JavaGraphClient, ArtifactFreshnessResult
from companybrain.models.entities import PipelineStartRequest, ExtractedEntity
from companybrain.store.base import BrainEntity as _BrainEntity  # ADR-0017
from companybrain.store.identity import to_urn as _to_urn, workspace_slug_for as _ws_slug_for  # ADR-0017
from companybrain.pipeline.concurrency import (
    get_extraction_concurrency,
    get_extraction_semaphore,
    is_parallel_safe,
)

log = structlog.get_logger(__name__)

# Callback type: async fn(stage, emoji, message, extra_data)
ProgressFn = Callable[[str, str, str, dict], Awaitable[None]]


@dataclass
class PipelineResult:
    job_id: str
    workspace_id: str
    endpoint_path: str
    entity_count: int
    edge_count: int
    gap_count: int
    status: str = "completed"
    error: str | None = None
    # Rich detail for the UI
    code_units_found: int = 0
    git_commits_found: int = 0
    files_traced: list[str] = field(default_factory=list)
    stages_summary: list[dict] = field(default_factory=list)
    # ADR-0049: per-run cost/cache telemetry surfaced by /pipeline/jobs/{id}
    telemetry: dict = field(default_factory=dict)


async def run_pipeline(
    request: PipelineStartRequest,
    annotations: list[dict] | None = None,
    on_progress: Optional[ProgressFn] = None,
    java_callback_url: Optional[str] = None,
    java_callback_key: Optional[str] = None,
) -> PipelineResult:
    # Use the job_id provided by Java (via AiRunRequest) so the same ID appears
    # in every log line AND in the final callback to /v1/internal/pipeline-result.
    # Fall back to a fresh UUID only for direct/test invocations that don't
    # come from the Java job scheduler.
    job_id = getattr(request, "job_id", None) or str(uuid.uuid4())
    annotations = annotations or []
    stages_summary: list[dict] = []

    async def progress(stage: str, emoji: str, message: str, data: dict | None = None, **kwargs):
        """Emit a structured progress event — logs it AND calls the callback."""
        merged = {**(data or {}), **kwargs}
        log.info(f"[{stage}] {message}", **merged)
        if on_progress:
            await on_progress(stage, emoji, message, merged)

    # Reset the per-run usage tracker so each run starts clean
    from companybrain.llm.base import get_usage_tracker
    import time as _pipeline_time
    _pipeline_start = _pipeline_time.perf_counter()
    _run_tracker = get_usage_tracker()
    _run_tracker.reset()

    log.info(
        "━━━ Pipeline started ━━━",
        job_id=job_id,
        endpoint=request.endpoint_path,
        method=request.http_method,
        repos=[r.local_path or r.url for r in request.repos],
        workspace=request.workspace_id,
    )

    try:
        repos = [_repo_dict(r) for r in request.repos]

        # ── Stages 0a + 0b: run in parallel ───────────────────────────────────
        # Code tracing (CPU/regex + 1 LLM call) and git collection (I/O) are
        # fully independent.  Running them together saves the full git-collect time.
        await progress("0a", "🔍", "Code tracing + git history — scanning in parallel")

        # ADR-005: hold a reference to the collector so we can call collect_as_artifacts()
        _git_collector: list = []   # mutable cell trick — holds [GitCollector] after init

        async def _collect_git():
            git_config = CollectorConfig(
                endpoint_path=request.endpoint_path,
                http_method=request.http_method,
                branch=request.branch,
            )
            collector = GitCollector(git_config, repos)
            _git_collector.append(collector)
            return await collector.collect()

        tracer = CodeTracer()
        focal_context, git_result = await asyncio.gather(
            tracer.trace(
                endpoint=request.endpoint_path,
                method=request.http_method,
                repos=repos,
            ),
            _collect_git(),
            return_exceptions=True,
        )

        # Surface NoMatchingEndpointError as a job-level error rather than
        # silently producing 18 nodes from random files when the user passes
        # a bogus endpoint string. The exception message lists discovered
        # routes so the user can re-run with the correct one.
        from companybrain.collectors.code_tracer import NoMatchingEndpointError as _NoMatch
        if isinstance(focal_context, _NoMatch):
            await progress(
                "0a", "❌",
                f"Endpoint {request.http_method} {request.endpoint_path} matches no controller route",
                error=str(focal_context),
            )
            raise focal_context
        if isinstance(focal_context, Exception):
            # Any other exception from the tracer — surface and re-raise so
            # the orchestrator's outer error handler can mark the job failed.
            await progress("0a", "❌", f"Code tracing failed: {focal_context}",
                           error=str(focal_context))
            raise focal_context

        # Unpack git result (may be an exception if collection failed)
        git_clusters: list = []
        git_commits = 0
        if isinstance(git_result, Exception):
            log.warning("Stage 0b — git collection failed (non-fatal)", error=str(git_result))
            stages_summary.append({"stage": "0b", "label": "Git History",
                                    "skipped": True, "reason": str(git_result)})
            await progress("0b", "⚠️", f"Git history unavailable — continuing without it",
                           error=str(git_result))
        else:
            git_clusters = git_result
            git_commits  = sum(len(c.commits) for c in git_clusters)
            stages_summary.append({"stage": "0b", "label": "Git History",
                                    "clusters": len(git_clusters), "commits": git_commits})
            await progress("0b", "✅",
                           f"Collected {git_commits} commits across {len(git_clusters)} clusters",
                           clusters=len(git_clusters), commits=git_commits)

        files_traced = [u.file_path for u in focal_context.code_units]
        stages_summary.append({"stage": "0a", "label": "Code Tracing",
                                "code_units": len(focal_context.code_units), "files": files_traced})

        # ── ADR-005: Collect artifacts from git collector ─────────────────────
        # Re-use the already-run GitCollector to project clusters into Artifacts.
        # This is cheap — collect_as_artifacts() reads from the clusters that were
        # already computed; it does NOT re-run git or the GitHub API.
        pipeline_artifacts = []
        if _git_collector:
            try:
                pipeline_artifacts = await _git_collector[0].collect_as_artifacts(
                    workspace_id=request.workspace_id,
                )
                await progress(
                    "0b", "📦",
                    f"Collected {len(pipeline_artifacts)} artifacts "
                    f"({sum(1 for a in pipeline_artifacts if a.kind=='source_file')} source files, "
                    f"{sum(1 for a in pipeline_artifacts if a.kind=='commit')} commits, "
                    f"{sum(1 for a in pipeline_artifacts if a.kind=='pr')} PRs)",
                    artifact_count=len(pipeline_artifacts),
                )
            except Exception as e:
                log.warning("Artifact collection failed (non-fatal)", error=str(e))

        # Also add source_file artifacts from code tracer units not already covered
        traced_file_ext_ids = {a.external_id for a in pipeline_artifacts if a.kind == "source_file"}
        from companybrain.models.entities import Artifact as _Artifact
        for unit in focal_context.code_units:
            ext_id = f"{unit.repo_name}/{unit.file_path}"
            if ext_id not in traced_file_ext_ids:
                pipeline_artifacts.append(_Artifact(
                    kind="source_file",
                    external_id=ext_id,
                    content=unit.content[:16000] if unit.content else "",
                    metadata={"repo": unit.repo_name, "file_path": unit.file_path, "role": unit.role},
                ))

        if focal_context.is_empty():
            await progress(
                "0a", "⚠️",
                "No handler found — will fall back to git diff extraction",
                endpoint=request.endpoint_path,
            )
        else:
            await progress(
                "0a", "✅",
                f"Found {len(focal_context.code_units)} code units",
                units=[f"{u.role}: {u.class_name or u.file_path}" for u in focal_context.code_units],
                files=files_traced,
            )

        # ── Stage 0.5: Structural pre-pass (ADR-0011) ────────────────────────────
        # Run the Bun extractor-worker registry via cb-api BEFORE any LLM call.
        # This populates Neo4j with structural nodes and returns per-file hashes
        # that let us skip Stage 1 LLM calls entirely for structurally-unchanged
        # files.  If cb-api is unreachable the pre-pass degrades gracefully —
        # all units fall through to the dirty path and the LLM runs as before.
        from companybrain.pipeline.structural_prepass import run_structural_prepass

        _repo_path_for_prepass = (
            request.repos[0].local_path or request.repos[0].url
            if request.repos else ""
        )
        _commit_for_prepass = _resolve_commit_sha(_repo_path_for_prepass)

        await progress("0.5", "🪚", "Structural pre-pass — cb-api → Neo4j → fingerprints")
        _prepass = await run_structural_prepass(
            repo_path=_repo_path_for_prepass,
            commit_sha=_commit_for_prepass,
            workspace_id=request.workspace_id,
            focal_context=focal_context,
        )

        stages_summary.append({
            "stage": "0.5",
            "label": "Structural Pre-pass",
            "fresh": len(_prepass.fresh_units),
            "dirty": len(_prepass.dirty_units),
            "cb_api": _prepass.cb_api_status,
        })
        await progress(
            "0.5", "✅",
            f"{len(_prepass.fresh_units)} structural-fresh, {len(_prepass.dirty_units)} need LLM",
            fresh=len(_prepass.fresh_units),
            dirty=len(_prepass.dirty_units),
        )

        # ── Pre-flight: Freshness check — skip LLM for unchanged files ────────
        # Build the graph client early so we can call check_freshness before Stage 1.
        # This is the single biggest speed win: on average 80-90% of files are unchanged
        # between pipeline runs so we skip LLM inference for those entirely.
        # `job_id` is already the resolved canonical ID at this point:
        # either Java-provided via request.job_id, a fresh UUID for standalone
        # runs, or restored from checkpoint above.  Use it directly so logs
        # and the final pipeline-result callback all reference the same value.
        _graph_client_for_freshness = JavaGraphClient(
            workspace_id=request.workspace_id,
            job_id=job_id,
            callback_url=java_callback_url,
            callback_key=java_callback_key,
        )

        freshness_map: dict[str, ArtifactFreshnessResult] = {}
        fresh_entities: list[ExtractedEntity] = []
        # Start with the pre-pass split; Stage 0c may further reduce the dirty set.
        dirty_units = list(_prepass.dirty_units) if _prepass.dirty_units else list(focal_context.code_units)

        if not focal_context.is_empty():
            await progress("0c", "⚡", "Freshness pre-flight — checking which files changed since last run")
            # Only run freshness check on units the structural pre-pass marked dirty;
            # structurally-fresh units are already excluded from LLM work.
            _units_for_freshness = dirty_units or list(focal_context.code_units)
            freshness_map = await _graph_client_for_freshness.check_freshness(_units_for_freshness)

            fresh_units = list(_prepass.fresh_units)  # carry over structural-fresh units
            dirty_units = []
            for unit in _units_for_freshness:
                ext_id = f"{unit.repo_name}/{unit.file_path}"
                result = freshness_map.get(ext_id)
                if result and result.fresh and result.existing_entities:
                    fresh_units.append(unit)
                else:
                    dirty_units.append(unit)

            # Reconstruct ExtractedEntity objects from fresh node projections
            # so relationship extraction / synthesis can reference them.
            for unit in fresh_units:
                ext_id = f"{unit.repo_name}/{unit.file_path}"
                for ee in freshness_map[ext_id].existing_entities:
                    meta = ee.metadata or {}
                    fresh_entities.append(ExtractedEntity(
                        entity_type=ee.node_type,
                        name=ee.name,
                        file=meta.get("file", unit.file_path),
                        repo=meta.get("repo", unit.repo_name),
                        signature=meta.get("signature", ""),
                        last_modified_commit=meta.get("lastModifiedCommit", ""),
                        confidence=float(meta.get("confidence", 0.9)),
                        first_appeared_commit=meta.get("firstAppearedCommit"),
                        code_snippet=meta.get("codeSnippet"),
                        query_text=meta.get("queryText"),
                    ))

            stages_summary.append({
                "stage": "0c", "label": "Freshness Check",
                "total": len(focal_context.code_units),
                "fresh": len(fresh_units),
                "dirty": len(dirty_units),
                "reused_entities": len(fresh_entities),
            })
            await progress(
                "0c", "✅",
                f"Freshness: {len(fresh_units)} fresh (reused {len(fresh_entities)} entities), "
                f"{len(dirty_units)} dirty (LLM required)",
                fresh=len(fresh_units),
                dirty=len(dirty_units),
                reused=len(fresh_entities),
            )
        else:
            # No code units found — dirty_units stays empty, git diff fallback runs in Stage 1
            pass

        # ── Stage 1: Entity extraction with hierarchical context ──────────────
        # ── Resume from checkpoint if a previous run left one ─────────────────
        # stage_reached tracks the highest stage completed:
        #   "1"   → entity extraction done, intent synthesis + import graph still needed
        #   "1.5" → entity extraction + intent synthesis done, import graph still needed
        #   "1.6" → full Stage 1 done, skip everything through Stage 1
        #
        # code_units are also stored so code tracing is skipped on resume.
        _ckpt = _checkpoint_load(request)
        entities: list = []
        structural_rels: list = []
        _stage1_from_checkpoint = False      # full Stage 1 skip (stage_reached == "1.6")
        _skip_extraction        = False      # partial skip: extraction done but 1.5/1.6 still needed
        _skip_intent_synthesis  = False      # 1.5 already done

        if _ckpt:
            entities        = _ckpt["entities"]
            structural_rels = _ckpt["structural_rels"]
            stage_reached   = _ckpt["stage_reached"]

            # Restore code_units into focal_context so code tracing is skipped
            ckpt_units = _ckpt.get("code_units", [])
            if ckpt_units and focal_context.is_empty():
                focal_context.code_units = ckpt_units
                log.info("Restored code_units from checkpoint", count=len(ckpt_units))

            # Restore job_id so the same Java job ID flows through every log line
            # and into the final POST to /v1/internal/pipeline-result.
            # Only override if the current request doesn't already carry a job_id
            # (the Java-initiated path always provides one; standalone calls don't).
            ckpt_job_id = _ckpt.get("job_id", "")
            if ckpt_job_id and not getattr(request, "job_id", ""):
                try:
                    request.job_id = ckpt_job_id
                    job_id = ckpt_job_id
                    log.info("Restored job_id from checkpoint", job_id=job_id)
                except Exception:
                    pass  # request object may be frozen; non-fatal

            if stage_reached >= "1.6":
                _stage1_from_checkpoint = True
                _skip_extraction        = True
                _skip_intent_synthesis  = True
            elif stage_reached >= "1.5":
                _skip_extraction       = True
                _skip_intent_synthesis = True
            elif stage_reached >= "1":
                _skip_extraction = True

            log.info(
                "Resuming from checkpoint",
                stage_reached=stage_reached,
                entities=len(entities),
                code_units=len(ckpt_units),
                skip_extraction=_skip_extraction,
                skip_intent_synthesis=_skip_intent_synthesis,
                full_stage1_skip=_stage1_from_checkpoint,
            )
            await progress(
                "1", "⚡",
                f"Resuming from checkpoint (stage {stage_reached}) — "
                f"{len(entities)} entities, {len(ckpt_units)} code units already extracted",
            )

        # L2 repo/branch needed both for ADR-0049 O2 (pre-enqueue skip) and
        # ADR-0014 L2 warm-up below.  Define once here so both paths share it.
        repo_path_for_l2 = (request.repos[0].local_path or "") if request.repos else ""
        branch_for_l2    = request.branch or "main"

        # ── ADR-0044: chunked extraction path ─────────────────────────────────
        # When BRAIN_USE_CHUNK_QUEUE=true (and not overridden by BRAIN_LEGACY_EXTRACT),
        # the pipeline routes through the per-method extraction queue instead of the
        # legacy per-file LLM call.  Both paths converge at Stage 2.
        from companybrain.config import settings as _adr44_settings
        _use_chunk_queue = (
            _adr44_settings.use_chunk_queue
            and not _adr44_settings.use_legacy_extract
            and not _skip_extraction
            and focal_context.code_units
        )

        # ADR-0047: EntityExtractor must be instantiated before the chunk-queue
        # block so that extractor._deduplicate() is available inside it.
        extractor   = EntityExtractor()
        cm_agent    = ContextManagerAgent()
        accumulator = SharedContextAccumulator()
        # ADR-0049 O3: set True by the chunked path when it produced chunk_results.
        _skip_gap_detection_auto = False

        if _use_chunk_queue:
            await progress("1", "📦", "ADR-0047 chunked extraction — splitting repo into per-method chunks")
            try:
                from companybrain.pipeline.code_chunker import CodeChunker
                from companybrain.pipeline.chunk_relevance_filter import ChunkRelevanceFilter
                from companybrain.pipeline.chunk_batcher import ChunkBatcher
                from companybrain.pipeline.queue import enqueue, ChunkInput, retry_failed
                from companybrain.pipeline.worker import drain_queue, collect_entities_and_edges
                from companybrain.pipeline.merger import merge_chunk_entities, resolve_edges

                _chunker = CodeChunker()
                _method_chunks = _chunker.chunk_repo(focal_context.code_units)

                await progress(
                    "1", "✂️",
                    f"Chunker produced {len(_method_chunks)} method chunks from "
                    f"{len(focal_context.code_units)} code units",
                    chunks=len(_method_chunks),
                )

                # ADR-0047 U4: apply relevance filter (tier1 trivial + tier2 reachability)
                _filter = ChunkRelevanceFilter()
                _filter_results = _filter.filter(_method_chunks)
                _keep_chunks = [r.chunk for r in _filter_results if r.keep]
                _drop_chunks = [r for r in _filter_results if not r.keep]

                await progress(
                    "1", "🔍",
                    f"Relevance filter: {len(_keep_chunks)} kept, "
                    f"{len(_drop_chunks)} filtered out",
                    kept=len(_keep_chunks),
                    filtered=len(_drop_chunks),
                )

                _repo_name = getattr(focal_context.code_units[0], "repo_name", "")

                # Enqueue filtered chunks immediately as status='filtered' for telemetry
                _filtered_inputs = [
                    ChunkInput(
                        workspace_id=request.workspace_id,
                        job_id=job_id,
                        repo=_repo_name,
                        file_path=r.chunk.file_path,
                        qname=r.chunk.qname,
                        body_hash=r.chunk.body_hash,
                        chunk_kind=r.chunk.kind,
                        header_context=r.chunk.header_context,
                        import_context=r.chunk.import_context,
                        body=r.chunk.body,
                        language=r.chunk.language,
                        filter_reason=r.filter_reason,
                    )
                    for r in _drop_chunks
                ]

                # ADR-0047 U2: group small siblings before enqueueing live chunks
                _batcher = ChunkBatcher()
                _batches = _batcher.batch(_keep_chunks)

                # Flatten batches back to chunks for the queue (queue is per-chunk)
                _live_inputs = [
                    ChunkInput(
                        workspace_id=request.workspace_id,
                        job_id=job_id,
                        repo=_repo_name,
                        file_path=mc.file_path,
                        qname=mc.qname,
                        body_hash=mc.body_hash,
                        chunk_kind=mc.kind,
                        header_context=mc.header_context,
                        import_context=mc.import_context,
                        body=mc.body,
                        language=mc.language,
                    )
                    for mc in _keep_chunks
                ]

                # ADR-0049 O2: skip enqueueing chunks whose file hash already
                # has a 'done' entry in the L2 cache from a prior run.
                _l2_hit_files: set[str] = _load_l2_cache_hits(
                    repo_path_for_l2, branch_for_l2
                )
                if _l2_hit_files:
                    _live_before = len(_live_inputs)
                    _live_inputs = [
                        ci for ci in _live_inputs
                        if ci.file_path not in _l2_hit_files
                    ]
                    log.info(
                        "ADR-0049 O2: L2-cache short-circuit",
                        skipped=_live_before - len(_live_inputs),
                        remaining=len(_live_inputs),
                    )

                _all_inputs = _filtered_inputs + _live_inputs
                _inserted = await enqueue(_all_inputs)
                await progress(
                    "1", "📥",
                    f"Enqueued {_inserted} rows "
                    f"({len(_live_inputs)} live, {len(_filtered_inputs)} pre-filtered, "
                    f"{len(_all_inputs) - _inserted} duplicate skips)",
                    batches=len(_batches),
                )

                # ADR-0048: route drain through ContextAgent unless legacy flag set
                if not _adr44_settings.use_legacy_navigator:
                    from companybrain.pipeline.worker import drain_queue_batched
                    _chunk_results = await drain_queue_batched(
                        job_id=job_id,
                        workspace_id=request.workspace_id,
                        batch_size=_adr44_settings.context_agent_batch_size,
                    )
                else:
                    _chunk_results = await drain_queue(
                        job_id=job_id,
                        workspace_id=request.workspace_id,
                        max_workers=_adr44_settings.chunk_queue_max_workers,
                    )

                # Collect raw entities + edges from all chunk results
                _raw_chunk_entities, _raw_chunk_edges = collect_entities_and_edges(_chunk_results)

                # Merge duplicate entities (same qname, different body versions)
                _merged = merge_chunk_entities(_raw_chunk_entities)

                # Convert ExtractedEdge (chunk format: implicit-from, target-only)
                # → ExtractedRelationship (downstream format: explicit from+to).
                # Every chunk edge's from is the chunk's owning entity.
                # Without this, every edge the LLM extracted PER CHUNK was
                # silently dropped — only Stage 2's separate relationship pass
                # produced edges, which was the whole point of chunking.
                from companybrain.models.entities import ExtractedRelationship
                _chunk_relationships: list = []
                for cr in _chunk_results:
                    if cr.entity is None or not cr.edges:
                        continue
                    from_name = cr.entity.name
                    from_type = cr.entity.entity_type
                    for e in cr.edges:
                        _chunk_relationships.append(ExtractedRelationship(
                            from_entity=from_name,
                            from_type=from_type,
                            edge_type=getattr(e, "edge_type", "") or "",
                            to_entity=getattr(e, "target", "") or "",
                            to_type="",
                            confidence=float(getattr(e, "confidence", 0.8) or 0.8),
                            evidence=getattr(e, "evidence", "") or "",
                        ))
                log.info("ADR-0044 chunked extraction: collected chunk edges",
                         chunks=len(_chunk_results),
                         relationships_from_chunks=len(_chunk_relationships))

                # Convert chunk entities → ExtractedEntity for the downstream pipeline
                from companybrain.models.entities import ExtractedEntity
                entities = [
                    ExtractedEntity(
                        entity_type=ce.entity_type,
                        name=ce.name,
                        file=ce.file_path,
                        repo=_repo_name,
                        signature=ce.signature,
                        last_modified_commit="",
                        confidence=ce.confidence,
                        code_snippet=ce.code_snippet,
                        query_text=ce.query_text,
                    )
                    for ce in _merged
                ]
                entities = extractor._deduplicate(fresh_entities + entities)

                # ADR-0048: emit structural Class entities for DTO fast-path
                if not _adr44_settings.use_legacy_navigator:
                    _tracer = getattr(focal_context, "_tracer", None)
                    _skip_dtos: list[str] = []
                    # The tracer stores skip_dto on self; retrieve via code_units if available
                    for _unit in focal_context.code_units:
                        _tracer_obj = getattr(_unit, "_tracer_ref", None)
                        if _tracer_obj and hasattr(_tracer_obj, "_specialist_skip_dto"):
                            _skip_dtos = _tracer_obj._specialist_skip_dto
                            break
                    # Also check request for repo_path to locate DTO files
                    _repo_path_for_dto = ""
                    if request.repos:
                        _repo_path_for_dto = request.repos[0].get("path", "")
                    if _skip_dtos and _repo_path_for_dto:
                        from companybrain.pipeline.entity_extractor import _entities_from_dto_plan
                        _dto_entities = _entities_from_dto_plan(
                            _skip_dtos, _repo_path_for_dto, _repo_name,
                        )
                        if _dto_entities:
                            entities = extractor._deduplicate(entities + _dto_entities)
                            log.info("ADR-0048 DTO fast-path", dto_count=len(_dto_entities))

                await progress(
                    "1", "✅",
                    f"Chunked extraction complete — {len(_merged)} entities from "
                    f"{len(_chunk_results)} chunks ({len(_batches)} batches)",
                    entities=len(_merged),
                    chunks_processed=len(_chunk_results),
                    batches=len(_batches),
                )

                _skip_extraction = True

                # ADR-0049 O3: the chunked path emits business_context per
                # method — Stage 1.5 (intent synthesis) and Stage 4 (gap
                # detection) would re-derive the same signals. Skip them when
                # we have a successful chunked run to save ~2 LLM calls.
                if _chunk_results:
                    _skip_intent_synthesis = True
                    _skip_gap_detection_auto = True
                    log.info(
                        "ADR-0049 O3: skip intent-synthesis + gap-detection "
                        "(chunked path provides equivalent data)",
                        chunks=len(_chunk_results),
                    )

            except Exception as _chunk_err:
                # Include the full traceback so the legacy-path fallback isn't
                # silent. Without this, you only see "Failed to parse entity
                # JSON" warnings from the legacy extractor and have no way to
                # tell that the chunked path silently bailed before then.
                import traceback as _tb
                log.error(
                    "ADR-0047 chunked extraction failed — falling back to legacy",
                    error=str(_chunk_err),
                    error_type=type(_chunk_err).__name__,
                    traceback=_tb.format_exc(),
                )
                await progress("1", "⚠️",
                               f"Chunked extraction failed: {type(_chunk_err).__name__}: "
                               f"{_chunk_err} — using legacy path")
                # _skip_extraction stays False → falls through to legacy path below

        if not _skip_extraction:
            await progress("1", "🧠", "Entity extraction — L1/L2 context hierarchy + CM Agent (one class at a time)")

        # ADR-0014: warm L2 from the previous run's persisted cache (if available)
        from companybrain.pipeline.shared_context_accumulator import L2Persistence
        # repo_path_for_l2 / branch_for_l2 defined above (before chunk-queue block).
        l2 = L2Persistence.load(repo_path_for_l2, branch_for_l2) if repo_path_for_l2 else L2SharedContext()
        if not l2.is_empty():
            await progress(
                "0.6", "🧠",
                f"L2 warmed from cache: {l2.compact_summary()}",
                summary=l2.compact_summary(),
            )

        if not _skip_extraction and not focal_context.is_empty():
            concurrency = get_extraction_concurrency()
            parallel    = is_parallel_safe() and len(dirty_units) > 1

            await progress(
                "1", "📄",
                f"Extracting {len(dirty_units)} dirty units — "
                f"{'parallel (concurrency=' + str(concurrency) + ')' if parallel else 'sequential (Ollama)'} "
                f"— skipping {len(fresh_entities)} reused from fresh cache",
                mode="code-based + L2 shared context",
                units=len(dirty_units),
                skipped=len(fresh_entities),
                concurrency=concurrency,
                parallel=parallel,
            )

            context_assemblies: dict = {}

            if parallel:
                # ── Parallel extraction (cloud APIs: OpenAI / Anthropic) ──────
                #
                # Phase A (sequential): CM Agent pre-assembles context for every unit
                #   using the *initial* L2 state.  This is a cheap in-memory pass —
                #   no LLM call — so staying sequential preserves context ordering.
                #
                # Phase B (concurrent): All LLM extraction calls run under a semaphore.
                #   asyncio.gather fans them out; the semaphore caps active calls.
                #
                # Phase C (sequential): L2 is updated from all results.
                #   L2 won't have per-unit incremental enrichment during batch extraction
                #   (acceptable tradeoff — on next run those units will be fresh).

                # Phase A — pre-assemble CM contexts
                assemblies_in_order = []
                for unit in dirty_units:
                    assembly = await cm_agent.assemble(
                        unit=unit,
                        l2=l2,
                        endpoint=request.endpoint_path,
                        method=request.http_method,
                    )
                    context_assemblies[unit.file_path] = assembly
                    assemblies_in_order.append((unit, assembly))

                # Phase B — concurrent LLM extraction
                sem = get_extraction_semaphore()

                async def _extract_unit(unit, assembly):
                    async with sem:
                        try:
                            unit_entities = await extractor._extract_from_code_unit(
                                unit, focal_context, assembly
                            )
                            return unit, unit_entities
                        except Exception as exc:
                            log.error("Unit extraction failed",
                                      unit=unit.file_path, error=str(exc))
                            return unit, []

                results = await asyncio.gather(
                    *[_extract_unit(unit, asm) for unit, asm in assemblies_in_order]
                )

                # Phase C — collect and update L2 sequentially
                for unit, unit_entities in results:
                    entities.extend(unit_entities)
                    accumulator.update(l2, unit_entities, unit)
                    if unit_entities:
                        await progress(
                            "1", "📄",
                            f"{unit.class_name or unit.file_path}: {len(unit_entities)} entities",
                            role=unit.role,
                            entities=[f"{e.entity_type}:{e.name}" for e in unit_entities[:5]],
                        )

            else:
                # ── Sequential extraction (Ollama — single-threaded local GPU) ─
                # CM Agent assembles context → LLM extracts → L2 updates (incremental).
                # This is the original high-quality path: each unit sees L2 enriched
                # by all previously processed units.
                for unit in dirty_units:
                    assembly = await cm_agent.assemble(
                        unit=unit,
                        l2=l2,
                        endpoint=request.endpoint_path,
                        method=request.http_method,
                    )
                    context_assemblies[unit.file_path] = assembly

                    if assembly.system_prompt_patch:
                        await progress(
                            "1", "🎯",
                            f"CM Agent patched prompt for {unit.class_name or unit.file_path}",
                            patch=assembly.system_prompt_patch[:120],
                            confidence_prior=assembly.confidence_prior,
                        )

                    try:
                        unit_entities = await extractor._extract_from_code_unit(
                            unit, focal_context, assembly
                        )
                        entities.extend(unit_entities)
                        await progress(
                            "1", "📄",
                            f"{unit.class_name or unit.file_path}: {len(unit_entities)} entities",
                            role=unit.role,
                            entities=[f"{e.entity_type}:{e.name}" for e in unit_entities[:5]],
                        )
                    except Exception as exc:
                        log.error("Unit extraction failed", unit=unit.file_path, error=str(exc))
                        await progress("1", "⚠️", f"Unit {unit.file_path} failed: {exc}")
                        # Save partial progress so retry resumes here
                        _checkpoint_save(request, entities, [], focal_context, stage_reached="1")
                        continue

                    accumulator.update(l2, unit_entities, unit)
                    # Save after every unit so any crash/rate-limit is resumable
                    _checkpoint_save(request, entities, [], focal_context, stage_reached="1")

            # Merge fresh (reused) entities + newly extracted dirty entities, then deduplicate.
            # Fresh entities come first so their existing external_ids win dedup ties.
            entities = extractor._deduplicate(fresh_entities + entities)

            await progress(
                "1", "📊",
                f"L2 context after extraction: {l2.compact_summary()}",
            )

        else:
            await progress(
                "1", "📄",
                f"Fallback: sending {len(git_clusters)} git diff chunks to LLM",
                mode="diff-based (fallback)",
            )
            api_snapshot = {"path": request.endpoint_path, "method": request.http_method, "handler_code": ""}
            entities = await extractor.extract_from_clusters(git_clusters, api_snapshot)

        # ── Stage 1 post-processing: call-graph following (ADR-0041 Phase 2) ──
        # Follow CALLS edges and code_snippet call-sites to extract entities from
        # files that CodeTracer didn't include in the initial FocalContext.
        # Controlled by max_hops=2 and max_files_per_hop=3 to stay within cost budget.
        if focal_context.code_units and not _skip_extraction:
            try:
                _repo_paths = [getattr(rc, "path", None) for rc in
                               getattr(request, "repo_configs", []) if getattr(rc, "path", None)]
                _loop_repo_root = _repo_paths[0] if _repo_paths else "."
                _loop = ExtractionLoop(repo_root=_loop_repo_root, max_hops=2)
                _loop_result = await _loop.run(
                    initial_entities=entities,
                    initial_relationships=[],
                    initial_units=focal_context.code_units,
                    extractor=extractor,
                    focal_context=focal_context,
                    l2=l2,
                )
                if _loop_result.files_followed:
                    existing_names = {e.name for e in entities}
                    _new_loop = [e for e in _loop_result.entities if e.name not in existing_names]
                    entities.extend(_new_loop)
                    log.info(
                        "ExtractionLoop followed call chain",
                        hops=_loop_result.hops_taken,
                        files=_loop_result.files_followed,
                        new_entities=len(_new_loop),
                    )
            except Exception as _loop_err:
                log.warning("ExtractionLoop failed (non-fatal)", error=str(_loop_err))

        # ── Stage 1 post-processing: derived query extraction (Tier 1.C) ────────
        # Zero-cost structural scan for interface/repository methods that the LLM
        # misses (no body = no extraction). Runs before the filter so that
        # InterfaceMethod entities survive (weight=9 in filter).
        if focal_context.code_units:
            _dqe = DerivedQueryExtractor()
            _first_unit = focal_context.code_units[0]
            _dq_entities = _dqe.extract(
                focal_context.code_units,
                repo_name=getattr(_first_unit, "repo_name", ""),
                commit_sha=getattr(_first_unit, "commit_sha", ""),
            )
            if _dq_entities:
                existing_names = {e.name for e in entities}
                _new = [e for e in _dq_entities if e.name not in existing_names]
                entities.extend(_new)
                log.info(
                    "Derived query extractor added entities",
                    new=len(_new),
                    total=len(entities),
                )

        # ── Stage 1 post-processing: relevance filter ─────────────────────────
        # Strips diff-artifact constants, test classes, and noise-suffix classes
        # (e.g. JsonKeyMapping, *Constants) before they enter any LLM stage.
        # Only endpoint-relevant code units (controllers, services, repos, DB queries)
        # are forwarded, keeping context tight and reducing token spend.
        raw_entity_count = len(entities)
        entities = filter_entities(entities, endpoint=request.endpoint_path)

        entity_names = [f"{e.entity_type}:{e.name}" for e in entities[:10]]
        stage_1 = {
            "stage": "1",
            "label": "Entity Extraction",
            "entities": len(entities),
            "raw_entities": raw_entity_count,
            "filtered_out": raw_entity_count - len(entities),
            "sample": entity_names,
            "l2_snapshot": l2.snapshot() if not l2.is_empty() else {},
        }
        stages_summary.append(stage_1)

        await progress(
            "1", "✅",
            f"Extracted {raw_entity_count} entities → {len(entities)} after relevance filter "
            f"(L2: {len(l2.domain_glossary)} glossary terms, {len(l2.service_registry)} services)",
            entities=entity_names,
            total=len(entities),
            filtered_out=raw_entity_count - len(entities),
        )

        # ── Stage 1 assumption mining (ADR-0017) ─────────────────────────────────
        # Deterministic static extraction — zero LLM cost.
        # Runs after the relevance filter so we only mine assumptions from
        # entities that are actually relevant to this endpoint.
        # Results are BrainEntity objects written directly in Stage 5 via store.write().
        _assumption_brain_entities: list[_BrainEntity] = []
        if not _skip_extraction and focal_context.code_units:
            _assumption_brain_entities = _collect_assumption_entities(
                code_units=focal_context.code_units,
                filtered_entities=entities,
                workspace_id=request.workspace_id,
            )
            if _assumption_brain_entities:
                log.info(
                    "ADR-0017 assumption mining complete",
                    count=len(_assumption_brain_entities),
                )

        # ── Stage 1.4: LLM-guided dependency expansion ───────────────────────────
        # Skipped when resuming from checkpoint (expansion already happened in prior run).
        # The navigator is given MAX_TURNS to trace the call chain. When a
        # RepositoryImpl or Service delegates to further dependencies (other repos,
        # clients, caches) the navigator may have run out of turns before exploring
        # them. This stage asks a focused LLM call: "given what we extracted so far,
        # which class names were mentioned but never actually visited?" and then
        # runs a targeted second navigator pass for each one — capped at 3 to
        # avoid runaway expansion.
        #
        # The LLM decides whether expansion is worth it based on the entity signal:
        # - "CompetitivenessPlanRepository: only interface methods seen, no queries" → expand
        # - "NiqAPIRequest: DTO with field names only" → skip
        # - "VIEW_BY: enum type" → skip
        already_traced = {str(u.file_path) for u in focal_context.code_units}
        expansion_candidates = [] if _skip_extraction else _llm_suggest_expansions(entities, focal_context.code_units)
        if expansion_candidates:
            await progress(
                "1.4", "🔍",
                f"Dependency expansion — {len(expansion_candidates)} unresolved collaborators: "
                f"{expansion_candidates}",
            )
            for candidate_class in expansion_candidates[:5]:   # cap at 5
                # Find the file for this class across all repos
                candidate_file = _resolve_class_to_file(candidate_class, request.repos)
                if not candidate_file or str(candidate_file) in already_traced:
                    continue
                try:
                    # Determine repo root for this file
                    repo_root = str(candidate_file.parent)
                    repo_name = candidate_class
                    for repo_cfg in request.repos:
                        if repo_cfg.local_path and str(candidate_file).startswith(repo_cfg.local_path):
                            repo_root = repo_cfg.local_path
                            repo_name = repo_cfg.name or candidate_class
                            break

                    # Direct read + extract — no navigator needed when we already know
                    # the file path.  The navigator's job is *finding* unknown files;
                    # here we already found the file.
                    content = candidate_file.read_text(errors="replace")
                    from companybrain.collectors.code_tracer import CodeUnit as _CU
                    role = (
                        "repository" if any(
                            candidate_class.endswith(s)
                            for s in ("Repository", "DAO", "Store", "Persistence", "RepositoryImpl")
                        ) else
                        "service" if any(
                            candidate_class.endswith(s)
                            for s in ("Service", "ServiceImpl", "Engine", "Processor", "Handler")
                        ) else
                        "model"
                    )
                    nu = _CU(
                        file_path=str(candidate_file),
                        repo_name=repo_name,
                        role=role,
                        class_name=candidate_class,
                        content=content,
                        language="java" if str(candidate_file).endswith(".java") else
                                 "kotlin" if str(candidate_file).endswith(".kt") else
                                 "python" if str(candidate_file).endswith(".py") else
                                 "typescript",
                    )
                    focal_context.code_units.append(nu)
                    already_traced.add(str(candidate_file))
                    sec_entities = await extractor._extract_from_code_unit(nu, focal_context)
                    sec_entities = extractor._deduplicate(sec_entities)
                    entities = extractor._deduplicate(entities + sec_entities)
                    accumulator.update(l2, sec_entities, nu)
                    await progress(
                        "1.4", "✅",
                        f"Expanded {candidate_class}: {len(sec_entities)} entities added",
                    )
                except Exception as exc:
                    log.warning("Dependency expansion failed", candidate=candidate_class, error=str(exc))

        # ── Stage 1.5: Intent synthesis — FunctionContext for each function ─────
        # Transforms structural entities into business-meaningful descriptions.
        # Runs AFTER entity dedup so we don't synthesise the same function twice.
        # Runs BEFORE relationship extraction so RelationshipExtractor can use
        # the richer entity metadata when scoring edge candidates.
        #
        # Cost-cut: when settings.skip_intent_synthesis is True (or env var
        # BRAIN_SKIP_INTENT_SYNTHESIS=true), Stage 1.5 is skipped entirely.
        # The 21-field BusinessContext from Stage 3 covers most of what this
        # stage produced (purpose / side_effects / change_risk / gaps), so
        # skipping cuts ~50 LLM calls per typical run with minimal signal loss.
        from companybrain.config import settings as _stage_settings

        intent_contexts: dict = {}
        if (entities
                and not _skip_intent_synthesis
                and not _stage_settings.skip_intent_synthesis):
            await progress("1.5", "💡", "Intent synthesis — extracting business meaning from code functions")
            intent_contexts = await IntentSynthesizer().synthesise_all(entities, focal_context)
        elif _stage_settings.skip_intent_synthesis:
            await progress("1.5", "⏭️ ",
                           "Intent synthesis SKIPPED (settings.skip_intent_synthesis=true) — "
                           "BusinessContext from Stage 3 will provide the equivalent fields")

            # Attach FunctionContext to each entity's metadata so ContextAssemblerService
            # can include it in T2 blocks and the pipeline result carries it to Java.
            for entity in entities:
                if entity.external_id in intent_contexts:
                    ctx = intent_contexts[entity.external_id]
                    # Augment entity metadata — accessed downstream by RelationshipExtractor
                    # and serialised into graph node metadata by PipelineService in Java.
                    if not hasattr(entity, '_extra_metadata'):
                        object.__setattr__(entity, '_extra_metadata', {}) if hasattr(entity, '__dataclass_fields__') else None
                    # Store on the entity's existing code_snippet field as a supplement —
                    # the authoritative store is in the Java node metadata via pipeline_meta.
                    pass   # Java side reads intent_contexts from the payload (see below)

            stage_15 = {
                "stage":         "1.5",
                "label":         "Intent Synthesis",
                "synthesised":   len(intent_contexts),
                "candidates":    sum(1 for e in entities if e.entity_type in {"Function", "CodeFunction", "ApiEndpoint", "Service"}),
                "intent_sample": [
                    {"entity": eid, "purpose": ctx.purpose[:80]}
                    for eid, ctx in list(intent_contexts.items())[:3]
                ],
            }
            stages_summary.append(stage_15)
            await progress(
                "1.5", "✅",
                f"Synthesised intent for {len(intent_contexts)} functions "
                f"({sum(1 for c in intent_contexts.values() if c.change_risk == 'high')} high-risk)",
                synthesised=len(intent_contexts),
                high_risk=sum(1 for c in intent_contexts.values() if c.change_risk == "high"),
            )
            # Save checkpoint so retry skips extraction + intent synthesis
            _checkpoint_save(request, entities, [], focal_context, stage_reached="1.5")
        else:
            stages_summary.append({"stage": "1.5", "label": "Intent Synthesis", "skipped": True, "reason": "no entities"})

        # ── Stage 1.6: Import-graph CALLS edges (deterministic, zero LLM cost) ──
        # Scans @Autowired / constructor injection / import statements to produce
        # structural CALLS edges before the LLM relationship pass. These are merged
        # with LLM results so Stage 2 sees the full edge set and avoids duplicates.
        if not _stage1_from_checkpoint:   # stage_reached < "1.6"
            import_analyzer = ImportGraphAnalyzer()
            import_edges    = import_analyzer.analyze(focal_context.code_units, entities)
            structural_rels = import_analyzer.to_relationships(import_edges, entities)

            stages_summary.append({
                "stage": "1.6", "label": "Import-Graph Edges",
                "structural_edges": len(structural_rels),
            })
            await progress(
                "1.6", "🔌",
                f"Detected {len(structural_rels)} structural CALLS edges from import/DI analysis",
                structural=len(structural_rels),
            )

            # ── Checkpoint: save Stage 1 result so a retry can skip extraction ─────
            # Written to /tmp/cb_checkpoint_<job_key>.json where job_key is a stable
            # hash of (workspace_id, endpoint_path, http_method).  On retry, the
            # orchestrator detects the file and jumps straight to Stage 2.
            _checkpoint_save(request, entities, structural_rels, focal_context, stage_reached="1.6")

        # ── Stage 2: Relationship extraction ──────────────────────────────────
        # Phase 2.A — Deterministic structural edges (no LLM, $0).
        # Extracts CONTAINS / EXTENDS / IMPLEMENTS / INSTANTIATES / IMPORTS from
        # the AST/regex. Adds dozens of free edges per run AND lets the LLM
        # focus its budget on behavioral edges only (CALLS / USES / THROWS /
        # READS_COLUMN / etc.).
        from companybrain.pipeline.structural_edges import extract_structural_edges

        ast_structural_rels: list = []
        if focal_context.code_units:
            try:
                ast_structural_rels = extract_structural_edges(
                    focal_context.code_units, entities,
                )
            except Exception as exc:
                log.warning("Structural-edge extraction failed (non-fatal)",
                            error=str(exc))

        await progress("2", "🔗",
                       f"Relationship extraction — {len(ast_structural_rels)} structural edges from AST + LLM behavioral pass")

        llm_relationships = await RelationshipExtractor().extract(
            entities,
            git_clusters,
            {"path": request.endpoint_path, "method": request.http_method},
        )

        # Merge: AST structural + import-graph structural + chunk-queue +
        # LLM behavioral. Order matters because dedup is first-wins:
        #   AST/structural (confidence 1.0) wins ties
        #   chunk-queue edges come second — they have method-body grounding
        #   LLM-pass edges come last as a fallback for whatever the chunk
        #     queue and structural sources didn't find
        _chunk_rels_safe = locals().get("_chunk_relationships") or []
        relationships = _dedup_relationships(
            ast_structural_rels
            + structural_rels
            + _chunk_rels_safe
            + llm_relationships
        )

        stage_2 = {
            "stage": "2", "label": "Relationship Extraction",
            "edges": len(relationships),
            "structural": len(structural_rels),
            "llm": len(llm_relationships),
        }
        stages_summary.append(stage_2)
        await progress(
            "2", "✅",
            f"Found {len(relationships)} relationships ({len(structural_rels)} structural + {len(llm_relationships)} LLM)",
            total=len(relationships), structural=len(structural_rels), llm=len(llm_relationships),
        )

        # ── Stage 2.5: Reachability filter (drop entity drift) ────────────────
        # Audit on the network-iq-backend-java extraction showed ~48% of
        # entities were unrelated drift (Configuration*, Specialty*, sibling
        # endpoints) pulled in by aggressive navigator / import-graph traversal.
        # BFS from the entry endpoint via structural edges; drop everything
        # not reached. Skippable via BRAIN_SKIP_REACHABILITY_FILTER=true if
        # an operator wants the unfiltered superset.
        from companybrain.pipeline.reachability_filter import filter_to_reachable

        if os.environ.get("BRAIN_SKIP_REACHABILITY_FILTER", "").lower() != "true":
            entities, relationships, _reach_stats = filter_to_reachable(
                entities, relationships,
                endpoint_path=request.endpoint_path,
                http_method=request.http_method,
            )
            stages_summary.append({
                "stage": "2.5",
                "label": "Reachability Filter",
                **_reach_stats,
            })
            if _reach_stats.get("dropped", 0) > 0:
                await progress(
                    "2.5", "✂️",
                    f"Reachability filter: kept {_reach_stats['reachable']}/{_reach_stats['total']} entities, "
                    f"dropped {_reach_stats['dropped']} drift",
                    **{k: v for k, v in _reach_stats.items()
                       if k in ("total", "reachable", "dropped")},
                )

        # ── Stage 3: Business context synthesis ───────────────────────────────
        await progress("3", "📖", "Context synthesis — explaining WHY each entity exists (using git history)")

        contexts = await ContextSynthesizer().synthesise_all(entities, git_clusters, annotations)
        stage_3 = {"stage": "3", "label": "Context Synthesis", "contexts": len(contexts)}
        stages_summary.append(stage_3)
        await progress("3", "✅", f"Synthesised context for {len(contexts)} entities")

        # ── Stage 3.5: Memory tokenization — T0/T1 tokens from synthesised contexts ──
        # Generates compact memory tokens deterministically from BusinessContext objects.
        # T0 (~15 tok): one-liner for "I've heard of this"
        # T1 (~100 tok): summary for "I know what this does"
        # Tokens are stored in node metadata so ContextAssemblerService can render
        # T0/T1 blocks without extra LLM calls during Ask queries.
        memory_tokens = MemoryTokenizer().tokenize_all(entities, contexts)
        serialized_tokens = memory_tokens_to_metadata(memory_tokens)

        stages_summary.append({
            "stage": "3.5", "label": "Memory Tokenization",
            "tokens": len(memory_tokens),
            "t0_sample": [tok.t0 for tok in list(memory_tokens.values())[:2]],
        })
        await progress(
            "3.5", "🧩",
            f"Generated T0/T1 memory tokens for {len(memory_tokens)} entities",
            tokens=len(memory_tokens),
        )

        # ── Stage 4: Gap detection ─────────────────────────────────────────────
        # Cost-cut: when settings.skip_gap_detection is True (or env var
        # BRAIN_SKIP_GAP_DETECTION=true), skip the gap-detection LLM call.
        # One call saved per run — useful for fast iteration / demo runs
        # where gaps aren't being acted on downstream.
        gaps: list = []
        if not _stage_settings.skip_gap_detection and not _skip_gap_detection_auto:
            await progress("4", "🔎", "Gap detection — finding unexplained behaviour and missing owners")
            gaps = await GapDetector().detect(entities, git_clusters, annotations, contexts)
            stage_4 = {"stage": "4", "label": "Gap Detection", "gaps": len(gaps)}
            stages_summary.append(stage_4)
            await progress("4", "✅", f"Detected {len(gaps)} gaps")
        else:
            reason = (
                "ADR-0049 O3: chunked path provides equivalent data"
                if _skip_gap_detection_auto
                else "settings.skip_gap_detection=true"
            )
            await progress("4", "⏭️ ", f"Gap detection SKIPPED ({reason})")
            stages_summary.append({"stage": "4", "label": "Gap Detection (skipped)",
                                    "gaps": 0, "reason": reason})

        # ── Stage 5: Graph population — via BrainStore fan-out (ADR-0012) ──────
        # Write to JSON SOT first, then mirror to Postgres + Neo4j.
        # The existing JavaGraphClient.flush() is called inside PostgresBrainStore
        # so the Java backend receives identical data as before.
        await progress("5", "💾", "Graph population — writing to .brain/ SOT then mirroring to Postgres + Neo4j")

        from companybrain.store import (
            JsonFileBrainStore, PostgresBrainStore, Neo4jBrainStore, FanoutBrainStore,
        )
        from companybrain.graph.neo4j_writer import Neo4jWriter

        # Serialize FunctionContexts so Java can store them in node metadata.
        # Java's PipelineService reads "intentContexts" from pipeline_meta and
        # writes each one into the matching node's JSONB metadata under "functionContext".
        serialized_intent = {
            eid: function_context_to_dict(ctx)
            for eid, ctx in intent_contexts.items()
        }

        pipeline_meta = {
            "code_units_found":  len(focal_context.code_units),
            "git_commits_found": git_commits,
            "files_traced":      files_traced,
            "stages_summary":    stages_summary,
            "progress_logs":     log_entries if 'log_entries' in dir() else [],
            "intent_contexts":   serialized_intent,
            "memory_tokens":     serialized_tokens,
        }

        # Build the fan-out store: JSON (primary) → Postgres + Neo4j (mirrors)
        brain_root = _resolve_brain_root(request)
        json_store = JsonFileBrainStore(brain_root)

        pg_store = PostgresBrainStore(_graph_client_for_freshness)
        pg_store.configure(
            pipeline_meta=pipeline_meta,
            artifacts=pipeline_artifacts,
            intent_contexts=serialized_intent,
        )

        neo4j_writer = Neo4jWriter(
            workspace_id=request.workspace_id,
            uri=os.getenv("NEO4J_URI"),
            username=os.getenv("NEO4J_USERNAME"),
            password=os.getenv("NEO4J_PASSWORD"),
        )
        # Connect explicitly — without this, the first session() call inside
        # commit_run() raises RuntimeError("Neo4jWriter not connected") which
        # FanoutBrainStore.commit_run silently swallows via gather(return_exceptions=True).
        # That's the root cause of "Neo4j stays at 0 nodes after every successful run".
        await neo4j_writer.connect()
        neo4j_store = Neo4jBrainStore(neo4j_writer, workspace_id=request.workspace_id)

        from companybrain.retrieval.qdrant_store import QdrantBrainStore
        from companybrain.store.identity import workspace_slug_for
        qdrant_store = QdrantBrainStore(
            brain_root=brain_root.parent,
            workspace_slug=workspace_slug_for(request.workspace_id),
        )

        store = FanoutBrainStore(primary=json_store, mirrors=[pg_store, neo4j_store, qdrant_store])

        try:
            # Convert every ExtractedEntity → canonical BrainEntity and write through
            for ee in entities:
                be = _to_brain_entity(
                    ee,
                    contexts.get(ee.external_id),
                    memory_tokens.get(ee.external_id),
                    relationships,
                    request.workspace_id,
                )
                await store.write(be, run_id=job_id, workspace_id=request.workspace_id)

            # ADR-0017: write assumption entities (already BrainEntity, no conversion needed).
            # JsonFileBrainStore writes .brain/assumption/<qname>.json automatically.
            for assumption_be in _assumption_brain_entities:
                await store.write(assumption_be, run_id=job_id, workspace_id=request.workspace_id)


            await store.commit_run(job_id)
        finally:
            # Always close the Neo4j driver to release pool connections, even if
            # the run errors out partway through.
            await neo4j_writer.close()

        stage_5 = {"stage": "5", "label": "Graph Population", "status": "done",
                   "brain_root": str(brain_root)}
        stages_summary.append(stage_5)
        await progress(
            "5", "🎉",
            "Pipeline complete — brain written to .brain/ and mirrored to Postgres + Neo4j",
            entity_count=len(entities),
            edge_count=len(relationships),
            gap_count=len(gaps),
        )

        # NOTE (ADR-0011): _trigger_structural_extraction() has been moved to
        # Stage 0.5 (run_structural_prepass). No second call here.

        # ── Usage summary ─────────────────────────────────────────────────────
        _run_tracker.log_summary(log, label=request.endpoint_path)

        log.info(
            "━━━ Pipeline complete ━━━",
            job_id=job_id,
            endpoint=request.endpoint_path,
            entities=len(entities),
            edges=len(relationships),
            gaps=len(gaps),
            code_units=len(focal_context.code_units),
            git_commits=git_commits,
        )

        # ADR-0014: persist L2 so the next run on the same (repo, branch) starts warm
        if repo_path_for_l2:
            try:
                L2Persistence.save(l2, repo_path_for_l2, branch_for_l2)
            except Exception as exc:
                log.warning("L2 cache save failed (non-fatal)", error=str(exc))

        # Pipeline completed successfully — clear checkpoint so next run is fresh
        _checkpoint_clear(request)

        _usage = _run_tracker.summary()
        return PipelineResult(
            job_id=job_id,
            workspace_id=request.workspace_id,
            endpoint_path=request.endpoint_path,
            entity_count=len(entities),
            edge_count=len(relationships),
            gap_count=len(gaps),
            code_units_found=len(focal_context.code_units),
            git_commits_found=git_commits,
            files_traced=files_traced,
            stages_summary=stages_summary,
            telemetry={
                "wall_seconds": round(_pipeline_time.perf_counter() - _pipeline_start, 2),
                "total_input_tokens": _usage["total_input_tokens"],
                "total_output_tokens": _usage["total_output_tokens"],
                "total_cache_read_tokens": _usage["total_cache_read_tokens"],
                "total_cost_usd": _usage["total_cost_usd"],
            },
        )

    except Exception as e:
        _run_tracker.log_summary(log, label=f"{request.endpoint_path} [FAILED]")
        log.error("━━━ Pipeline failed ━━━", job_id=job_id, error=str(e), exc_info=True)
        await progress("error", "❌", f"Pipeline failed: {e}", error=str(e))
        return PipelineResult(
            job_id=job_id, workspace_id=request.workspace_id,
            endpoint_path=request.endpoint_path,
            entity_count=0, edge_count=0, gap_count=0,
            status="failed", error=str(e),
            stages_summary=stages_summary,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_l2_cache_hits(repo_path: str, branch: str) -> set[str]:
    """ADR-0049 O2: return the set of file_paths whose hash appears as 'done'
    in the L2 cache from a prior run.  Files in this set can skip chunk-queue
    enqueue because their entities are already in the brain store.

    Returns an empty set on any error (non-fatal; falls through to full extraction).
    """
    if not repo_path:
        return set()
    try:
        import json as _json
        cache_file = _Path(repo_path) / ".brain" / ".l2-cache" / f"{branch}.json"
        if not cache_file.exists():
            return set()
        data = _json.loads(cache_file.read_text())
        # L2 cache schema: {"files": {"<file_path>": {"hash": "...", "status": "done"}}}
        files = data.get("files", {})
        return {fp for fp, meta in files.items()
                if isinstance(meta, dict) and meta.get("status") == "done"}
    except Exception:
        return set()


async def _trigger_structural_extraction(
    repo_path: str,
    scope: str,
    commit_sha: str = "HEAD",
) -> None:
    """
    After LLM pipeline completes, trigger the TypeScript structural extractor
    via the cb-api service. This populates Neo4j with typed structural nodes
    that complement the LLM-extracted semantic context in Postgres.

    See ADR-0008: integration bridge.

    Never raises — structural extraction failure must not abort the LLM pipeline.
    The 300 s timeout is generous because large monorepos can take a while.
    """
    url = f"{CB_API_URL}/extract"
    payload = {
        "repoPath":  repo_path,
        "scope":     scope,
        "commitSha": commit_sha,
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            log.info(
                "Structural extraction triggered successfully (ADR-0008)",
                scope=scope,
                repo_path=repo_path,
                status=resp.status_code,
            )
    except httpx.HTTPStatusError as exc:
        log.warning(
            "Structural extraction returned non-2xx status (non-fatal)",
            scope=scope,
            status=exc.response.status_code,
            detail=exc.response.text[:200],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Structural extraction could not be triggered (non-fatal) — "
            "Neo4j graph may be out of sync until next cb index run",
            scope=scope,
            error=str(exc),
        )


import subprocess as _subprocess


def _resolve_commit_sha(repo_path: str) -> str:
    """Returns the current HEAD SHA of the repo, or 'HEAD' if not a git repo."""
    if not repo_path:
        return "HEAD"
    try:
        return _subprocess.check_output(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            stderr=_subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except Exception:
        return "HEAD"


def _collect_assumption_entities(
    *,
    code_units: list,
    filtered_entities: list,
    workspace_id: str,
) -> list[_BrainEntity]:
    """
    ADR-0017: Mine assumption BrainEntities from code units.

    For each extracted entity that has a corresponding CodeUnit, run the
    static assumption miner and collect all RELIES_ON-linked BrainEntities.
    """
    slug = _ws_slug_for(workspace_id)
    units_by_file: dict[str, object] = {u.file_path: u for u in code_units}
    result: list[_BrainEntity] = []
    for ee in filtered_entities:
        unit = units_by_file.get(ee.file)
        if unit is None:
            continue
        entity_type = _ENTITY_TYPE_MAP.get(ee.entity_type, "component")
        parent_id = _to_urn(
            tenant=slug,
            domain="code",
            repo=ee.repo or workspace_id,
            entity_type=entity_type,
            qualified_name=ee.name,
        )
        parent_be = _BrainEntity(
            id=parent_id,
            entity_type=entity_type,
            repo=ee.repo or workspace_id,
            file=ee.file,
            qualified_name=ee.name,
        )
        result.extend(mine_assumptions(unit, parent_be, workspace_id=workspace_id))
    return result


def _dedup_relationships(rels: list) -> list:
    """
    Deduplicate relationships by (from_entity, edge_type, to_entity).
    First occurrence wins (structural edges come first → they win ties).

    Defensive: tolerates raw dicts (some upstream paths — pattern_distiller,
    chunk-queue worker output, .brain/ replays — have historically leaked
    dicts into the relationship list). Coerce to ExtractedRelationship
    on the fly and log the source so the leak can be tracked down.
    """
    from companybrain.models.entities import ExtractedRelationship as _ER

    seen: set[tuple] = set()
    out: list = []
    dict_count = 0
    for r in rels:
        if isinstance(r, dict):
            dict_count += 1
            try:
                r = _ER(
                    from_entity=r.get("from_entity") or r.get("from") or "",
                    from_type=r.get("from_type", ""),
                    edge_type=r.get("edge_type", ""),
                    to_entity=r.get("to_entity") or r.get("to") or "",
                    to_type=r.get("to_type", ""),
                    confidence=float(r.get("confidence", 0.7) or 0.7),
                    evidence=r.get("evidence", "") or "",
                )
            except Exception as exc:
                log.warning("[_dedup_relationships] dropping malformed dict",
                            error=str(exc), keys=list(r.keys())[:8])
                continue
        try:
            key = (r.from_entity, r.edge_type, r.to_entity)
        except AttributeError as exc:
            log.warning("[_dedup_relationships] skipping non-relationship object",
                        type=type(r).__name__, error=str(exc))
            continue
        if key not in seen:
            seen.add(key)
            out.append(r)
    if dict_count:
        log.warning("[_dedup_relationships] coerced N dicts to ExtractedRelationship — "
                    "track upstream and pass the right type",
                    count=dict_count, total=len(rels))
    return out


def _repo_dict(r) -> dict:
    """
    Normalise a RepoConfig into the plain dict that collectors expect.
    Per-repo branch is preserved so GitCollector can use the right branch
    for each repo independently.
    """
    git_path = r.local_path or r.url
    github_url = (
        r.url
        if r.url and r.url.startswith(("http://", "https://", "git@"))
        else None
    )
    return {
        "path": git_path,
        "type": r.type.value,
        "github_url": github_url,
        "branch": r.branch,      # per-repo branch — used by GitCollector
    }


# ── Stage 1.4 helpers ─────────────────────────────────────────────────────────

def _llm_suggest_expansions(
    entities: list,
    traced_units: list,
) -> list[str]:
    """
    Codebase-agnostic dependency expansion: find every custom class name that
    appears in the extracted entity graph but was never visited as a code unit.

    Works for ANY naming convention — PlanFinder, NiqEngine, MetricsAggregator,
    DataFetcher, QueryHandler — not just *Repository or *Service suffixes.

    Strategy (no extra LLM call — uses already-extracted signal):
      1. Collect ALL CamelCase type references from entity snippets/signatures.
      2. Remove standard library types (java.*, javax.*, Spring, Lombok,
         Python builtins, TypeScript lib types, common primitives).
      3. Remove class names whose source files are already in traced_units.
      4. Score by how likely the type is a collaborator (Impl=3,
         known-data-access-suffix=2, everything-else=1).
      5. Return top candidates for the caller to resolve + visit.
    """
    import re as _re

    # ── 1. Build the "already visited" set ────────────────────────────────────
    already_visited: set[str] = set()
    for unit in traced_units:
        if unit.class_name:
            already_visited.add(unit.class_name)
        fp = str(unit.file_path or "")
        if fp:
            already_visited.add(_Path(fp).stem)

    # ── 2. Standard-library / framework types to ignore ───────────────────────
    # These appear frequently in code but live in external packages — there is
    # no source file in the repo for them.
    _STD_PREFIXES = (
        # Java / Kotlin stdlib & common frameworks
        "String", "Integer", "Long", "Double", "Float", "Boolean", "Byte",
        "Short", "Character", "Object", "Number", "Void", "Class",
        "List", "Map", "Set", "Collection", "Optional", "Stream",
        "ArrayList", "HashMap", "HashSet", "LinkedList", "LinkedHashMap",
        "Iterator", "Iterable", "Comparator", "Comparable",
        "Thread", "Runnable", "Callable", "Future", "CompletableFuture",
        "Exception", "RuntimeException", "Error", "Throwable",
        "Override", "Deprecated", "SuppressWarnings", "FunctionalInterface",
        "System", "Math", "Arrays", "Collections", "Objects",
        # Spring / Jakarta / Lombok
        "Autowired", "Component", "Service", "Repository", "Controller",
        "RestController", "RequestMapping", "GetMapping", "PostMapping",
        "PutMapping", "DeleteMapping", "PathVariable", "RequestBody",
        "ResponseEntity", "HttpStatus", "Bean", "Configuration",
        "Transactional", "Slf4j", "Data", "Builder", "AllArgsConstructor",
        "NoArgsConstructor", "RequiredArgsConstructor", "Value", "Getter",
        "Setter", "ToString", "EqualsAndHashCode",
        "EntityManager", "JpaRepository", "CrudRepository",
        "PagingAndSortingRepository", "Pageable", "Page", "Sort",
        "Entity", "Table", "Column", "Id", "GeneratedValue",
        "Column", "OneToMany", "ManyToOne", "ManyToMany", "OneToOne",
        "JoinColumn", "FetchType", "CascadeType",
        # Python builtins / typing
        "None", "True", "False", "Type", "Any", "Dict", "Tuple",
        "Union", "Literal", "ClassVar", "Final", "Protocol",
        "BaseModel", "Field", "validator", "root_validator",
        "Enum", "IntEnum", "StrEnum",
        # TypeScript / JS lib
        "Promise", "Array", "Record", "Partial", "Required", "Readonly",
        "Pick", "Omit", "Exclude", "Extract", "NonNullable",
        "ReturnType", "InstanceType", "Parameters", "ConstructorParameters",
        "Date", "RegExp", "Error", "TypeError", "RangeError",
        "HTMLElement", "Event", "MouseEvent", "KeyboardEvent",
        "React", "Component", "useState", "useEffect", "useContext",
        "Props", "State", "FC", "ReactNode", "JSX",
        # Go stdlib
        "Context", "Error", "WaitGroup", "Mutex", "RWMutex",
        # Generic annotation / metadata tokens
        "Inject", "Named", "Singleton", "Prototype", "Scope",
        "NotNull", "NonNull", "Nullable", "Valid", "Size", "Min", "Max",
    )
    _STD_SET = frozenset(_STD_PREFIXES)

    # Also skip single-letter type params (T, E, K, V, R, etc.)
    _TYPE_PARAM = _re.compile(r'^[A-Z]$')

    # ── 2b. Leaf types that carry no call-chain value ─────────────────────────
    # DTOs, request/response wrappers, JPA entities, config beans, exception
    # classes — these have fields but no injected collaborators, so expanding
    # them yields no additional queries or business logic.  Skip them.
    _LEAF_SUFFIX = frozenset((
        "DTO", "Dto", "Request", "Response", "Payload", "Wrapper",
        "Entity", "Model", "Config", "Configuration", "Properties",
        "Exception", "Error", "Event", "Message",
        "Enum", "Constants", "Utils", "Util", "Helper", "Helpers",
        "Mapper", "Converter", "Builder",
    ))

    def _is_leaf(name: str) -> bool:
        for s in _LEAF_SUFFIX:
            if name.endswith(s):
                return True
        return False

    # ── 3. Extract ALL CamelCase identifiers from entity text ─────────────────
    # Matches bare class names: MyClass, PlanFinder, NiqEngine, etc.
    # Also picks up qualified refs like foo.BarBaz (takes "BarBaz").
    _CAMEL = _re.compile(r'\b([A-Z][a-zA-Z0-9]{2,})\b')

    candidates: dict[str, int] = {}   # name → score

    for entity in entities:
        text_sources = [
            entity.name or "",
            entity.code_snippet or "",
            entity.signature or "",
            getattr(entity, "structural_purpose", "") or "",
            getattr(entity, "description", "") or "",
        ]
        for text in text_sources:
            for m in _CAMEL.finditer(text):
                name = m.group(1)
                if (
                    name in already_visited
                    or name in _STD_SET
                    or _TYPE_PARAM.match(name)
                    or _is_leaf(name)
                ):
                    continue
                # Score: Impl suffix = likely has real implementation
                #        known data-access / business suffix = high value
                #        anything else = worth checking (score 1)
                _HIGH_SUFFIX = (
                    "Repository", "DAO", "Store", "Persistence",
                    "Client", "Gateway", "Adapter", "Connector",
                    "Provider", "Finder", "Fetcher", "Loader",
                    "Engine", "Calculator", "Processor", "Handler",
                    "Aggregator", "Resolver", "Dispatcher",
                )
                score = (
                    3 if name.endswith("Impl") else
                    2 if any(name.endswith(s) for s in _HIGH_SUFFIX) else
                    1
                )
                candidates[name] = max(candidates.get(name, 0), score)

    # ── 4. Rank and return ────────────────────────────────────────────────────
    # Primary sort: score desc.  Secondary: longer names first (more specific).
    ranked = sorted(
        candidates,
        key=lambda n: (candidates[n], len(n)),
        reverse=True,
    )
    return ranked[:8]   # caller caps at 3 expansions, but surface 8 so it can skip already-resolved ones


def _resolve_class_to_file(class_name: str, repos: list) -> "_Path | None":
    """
    Find the source file that defines `class_name` across all repo roots.

    Search order:
      1. {class_name}Impl.{ext}  — concrete implementation (has the real logic)
      2. {class_name}.{ext}      — exact match (could be abstract class or interface)

    Works for any naming convention — no suffix filtering applied.
    """
    exts = (".java", ".kt", ".py", ".ts", ".tsx", ".go", ".rb", ".cs")

    impl_candidates: list[_Path] = []
    exact_candidates: list[_Path] = []

    for repo_cfg in repos:
        root = repo_cfg.local_path
        if not root:
            continue
        root_path = _Path(root)
        for ext in exts:
            # Try Impl variant first (higher signal)
            if not class_name.endswith("Impl"):
                for match in root_path.rglob(f"{class_name}Impl{ext}"):
                    impl_candidates.append(match)
            # Exact match
            for match in root_path.rglob(f"{class_name}{ext}"):
                exact_candidates.append(match)

    # Impl wins, then exact, then nothing
    if impl_candidates:
        return impl_candidates[0]
    if exact_candidates:
        # Among exact matches, prefer one that has "Impl" anywhere in its stem
        for c in exact_candidates:
            if "Impl" in c.stem:
                return c
        return exact_candidates[0]
    return None


# ── Pipeline checkpoint helpers ───────────────────────────────────────────────
#
# Checkpoints are stored as JSON files in /tmp so they survive process restarts
# but are cleaned up by the OS on reboot.  The key is a stable hash of the
# pipeline request so the same endpoint retried an hour later hits the same
# checkpoint.  Checkpoints expire after 24 hours so stale data never poisons
# a fresh run.

import hashlib as _hashlib
import json as _json_mod
import time as _time_mod


def _checkpoint_key(request) -> str:
    raw = f"{request.workspace_id}::{request.http_method}::{request.endpoint_path}"
    return _hashlib.sha1(raw.encode()).hexdigest()[:16]


def _checkpoint_path(request) -> "_Path":
    return _Path(f"/tmp/cb_checkpoint_{_checkpoint_key(request)}.json")


def _checkpoint_save(
    request,
    entities: list,
    structural_rels: list,
    focal_context,
    stage_reached: str = "1.6",
    job_id: str | None = None,
) -> None:
    """
    Serialise pipeline progress to /tmp so a retry can resume where it left off.

    Saves:
      - entities extracted so far (may be partial if called mid-Stage-1)
      - structural_rels (empty list if called before Stage 1.6)
      - focal_context.code_units so code tracing is skipped on resume
      - stage_reached: highest stage completed ("1", "1.5", "1.6")

    Called:
      - After each sequential extraction unit (stage_reached="1", partial)
      - After intent synthesis (stage_reached="1.5")
      - After import-graph analysis (stage_reached="1.6", full Stage 1)
    """
    try:
        import dataclasses as _dc

        code_units_raw = []
        if focal_context is not None:
            for u in getattr(focal_context, "code_units", []):
                try:
                    code_units_raw.append(_dc.asdict(u) if _dc.is_dataclass(u) else u.__dict__)
                except Exception:
                    pass

        payload = {
            "saved_at":      _time_mod.time(),
            "endpoint":      request.endpoint_path,
            # Store the canonical job_id so checkpoint resumes post back to the
            # same Java job rather than an empty/mismatched ID.
            "job_id":        job_id or getattr(request, "job_id", None) or "",
            "stage_reached": stage_reached,
            "entities":      [_dc.asdict(e) for e in entities],
            "structural_rels": [
                (r if isinstance(r, dict) else _dc.asdict(r))
                for r in structural_rels
            ],
            "code_units":    code_units_raw,
        }
        _checkpoint_path(request).write_text(_json_mod.dumps(payload))
        log.debug(
            "Checkpoint saved",
            stage=stage_reached,
            entities=len(entities),
            code_units=len(code_units_raw),
            path=str(_checkpoint_path(request)),
        )
    except Exception as exc:
        log.debug("Checkpoint save failed (non-fatal)", error=str(exc))


def _checkpoint_load(request) -> "dict | None":
    """
    Load a checkpoint if it exists and is < 24 hours old.
    Returns dict with keys: entities, structural_rels, code_units, stage_reached.
    Returns None if no valid checkpoint exists.
    """
    try:
        p = _checkpoint_path(request)
        if not p.exists():
            return None
        payload = _json_mod.loads(p.read_text())
        age_hours = (_time_mod.time() - payload.get("saved_at", 0)) / 3600
        if age_hours > 24:
            p.unlink(missing_ok=True)
            log.debug("Checkpoint expired", age_hours=round(age_hours, 1))
            return None

        from companybrain.models.entities import ExtractedEntity as _EE
        from companybrain.collectors.code_tracer import CodeUnit as _CU
        import dataclasses as _dc

        # Restore entities
        raw_entities = payload.get("entities", [])
        valid_ee_fields = {f.name for f in _dc.fields(_EE)}
        entities = [
            _EE(**{k: v for k, v in e.items() if k in valid_ee_fields})
            for e in raw_entities
        ]

        # Restore code_units
        raw_units = payload.get("code_units", [])
        valid_cu_fields = {f.name for f in _dc.fields(_CU)}
        code_units = [
            _CU(**{k: v for k, v in u.items() if k in valid_cu_fields})
            for u in raw_units
        ]

        structural_rels  = payload.get("structural_rels", [])
        stage_reached    = payload.get("stage_reached", "1.6")
        checkpoint_job_id = payload.get("job_id", "")

        log.info(
            "Checkpoint loaded",
            stage_reached=stage_reached,
            age_hours=round(age_hours, 2),
            entities=len(entities),
            code_units=len(code_units),
            endpoint=payload.get("endpoint"),
            job_id=checkpoint_job_id or "(none)",
        )
        return {
            "entities":        entities,
            "structural_rels": structural_rels,
            "code_units":      code_units,
            "stage_reached":   stage_reached,
            # Restored so the pipeline posts back to the same Java job on resume.
            "job_id":          checkpoint_job_id,
        }
    except Exception as exc:
        log.debug("Checkpoint load failed (non-fatal)", error=str(exc))
        return None


def _checkpoint_clear(request) -> None:
    """Delete the checkpoint for a request (call after successful pipeline completion)."""
    try:
        _checkpoint_path(request).unlink(missing_ok=True)
    except Exception:
        pass


# ── ADR-0012: BrainStore helpers ──────────────────────────────────────────────

def _resolve_brain_root(request) -> "_Path":
    """
    Return the .brain/ root for this pipeline run.

    Uses the first repo's local_path when available; falls back to a tmp dir
    keyed by workspace_id so tests and remote-URL runs still work.
    """
    try:
        local = request.repos[0].local_path if request.repos else None
        if local:
            return _Path(local) / ".brain"
    except (AttributeError, IndexError):
        pass
    return _Path(f"/tmp/cb_brain_{request.workspace_id}")


def _to_brain_entity(
    ee,
    context,
    memory_tok,
    all_relationships: list,
    workspace_id: str,
) -> "BrainEntity":
    """
    Convert an ExtractedEntity (+ optional BusinessContext + MemoryToken)
    to the canonical BrainEntity representation.
    """
    from companybrain.store.base import BrainEntity
    from companybrain.pipeline.memory_tokenizer import memory_tokens_to_metadata

    repo = ee.repo or workspace_id
    entity_type = _ENTITY_TYPE_MAP.get(ee.entity_type, "component")
    entity_id = f"{repo}::{entity_type}::{ee.name}"

    t1_summary = ""
    t0_token = ""
    t1_token = ""
    meta = {
        "signature": ee.signature,
        "confidence": ee.confidence,
        "last_modified_commit": ee.last_modified_commit,
    }
    if ee.code_snippet:
        meta["code_snippet"] = ee.code_snippet
    if ee.query_text:
        meta["query_text"] = ee.query_text

    if context:
        t1_summary = context.purpose
        meta["change_risk"] = context.change_risk
        meta["invariants"] = context.invariants

    if memory_tok:
        t0_token = memory_tok.t0
        t1_token = memory_tok.t1

    # Filter relationships that originate from this entity
    rels = [
        {
            "target_id": r.to_entity,
            "target_type": r.to_type,
            "edge_type": r.edge_type,
            "confidence": r.confidence,
            "evidence": r.evidence,
        }
        for r in all_relationships
        if r.from_entity == ee.external_id or r.from_entity == entity_id
    ]

    return BrainEntity(
        id=entity_id,
        entity_type=entity_type,
        repo=repo,
        file=ee.file,
        qualified_name=ee.name,
        t1_summary=t1_summary,
        t0_token=t0_token,
        t1_token=t1_token,
        metadata=meta,
        relationships=rels,
        version_hash="",  # structural hash set by ADR-0013; empty for now
    )


# Map existing free-form entity_type → six harness types
_ENTITY_TYPE_MAP: dict[str, str] = {
    "ApiEndpoint":        "api_contract",
    "Function":           "component",
    "Class":              "component",
    "Service":            "component",
    "CodeFunction":       "function_node",
    "FrontendComponent":  "component",
    "SchemaField":        "data_model",
    "DatabaseTable":      "data_model",
    "DatabaseColumn":     "data_model",
    "DatabaseQuery":      "data_model",
    "ExternalService":    "component",
    "ConfigKey":          "component",
    "SharedType":         "data_model",
    # Already-canonical types pass through
    "component":          "component",
    "screen":             "screen",
    "api_contract":       "api_contract",
    "data_model":         "data_model",
    "assumption":         "assumption",
    "business_context":   "business_context",
    "function_node":      "function_node",
}
