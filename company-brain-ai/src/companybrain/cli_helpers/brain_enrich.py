"""
brain enrich --repo <path>

Re-enrich the existing graph WITHOUT re-running Stage 1 (entity extraction).

Use case
--------
You already ran the full pipeline once. The .brain/ JSON has all entities with
their code_snippet bodies. Source files have not changed (or only a few
methods inside them have). You want to:
  - Re-extract relationships with the new prompt / new edge taxonomy
  - Re-synthesize business context with the expanded schema
  - Mirror everything back to Postgres / Neo4j / Qdrant

…WITHOUT paying ~$0.30 per run for Stage 1 LLM entity extraction (which is the
most expensive stage and produces the same entities as last time anyway).

What this skips:  Stage 0a/0b (collectors), Stage 0c (freshness), Stage 1
                  (entity extraction), Stage 1.4 (import graph), Stage 1.5
                  (intent synth), Stage 1.6 (sub-flow extension).
What this runs:   Stage 2 (relationships) and Stage 3 (context synthesis),
                  using existing entities as input.
What this writes: Updated .brain/ JSON (relationships + business_context blobs),
                  Postgres edges + node_context, Neo4j relationships, Qdrant
                  re-embed.

Cost: ~$0.05 - $0.15 per run depending on entity count (vs ~$0.30 - $0.70
for a full pipeline run). Roughly 5× cheaper.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import structlog

from companybrain.graph.java_client import JavaGraphClient
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store import (
    FanoutBrainStore,
    JsonFileBrainStore,
    Neo4jBrainStore,
    PostgresBrainStore,
)
from companybrain.store.identity import workspace_slug_for
from companybrain.store.postgres_consumer import _to_extracted_entity

log = structlog.get_logger(__name__)


async def enrich_existing(
    repo_path: Path,
    workspace_id: str,
    *,
    skip_relationships: bool = False,
    skip_context: bool = False,
) -> None:
    """Re-run Stage 2 (relationships) + Stage 3 (context) over existing .brain/ entities.

    Args:
        repo_path:          Repo root containing .brain/.
        workspace_id:       Target workspace UUID string.
        skip_relationships: If True, only context synthesis runs.
        skip_context:       If True, only relationship extraction runs.
    """
    brain_root = repo_path / ".brain"
    if not brain_root.exists():
        raise FileNotFoundError(f"No .brain/ in {repo_path}")

    # Lazy imports — these pull in heavy LLM deps; defer until we know we'll use them.
    from companybrain.pipeline.relationship_extractor import RelationshipExtractor
    from companybrain.pipeline.context_synthesizer import ContextSynthesizer

    json_store = JsonFileBrainStore(brain_root)

    # Load all existing entities from .brain/ JSON SOT
    print(f"[enrich] Loading entities from {brain_root}...")
    entities_brain: list[Any] = []
    async for eid in json_store.list_ids():
        e = await json_store.read(eid)
        if e is not None:
            entities_brain.append(e)
    print(f"[enrich] Loaded {len(entities_brain)} entities")

    if not entities_brain:
        print("[enrich] No entities found — run the full pipeline first to populate .brain/.")
        return

    # Translate BrainEntity → ExtractedEntity so existing extractors accept them
    entities = [_to_extracted_entity(e) for e in entities_brain]

    # Build the result-poster (Java sink)
    enrich_job_id = str(uuid.uuid4())
    java = JavaGraphClient(workspace_id=workspace_id, job_id=enrich_job_id)
    pg = PostgresBrainStore(java)
    n4j = Neo4jBrainStore(Neo4jWriter(workspace_id=workspace_id), workspace_id=workspace_id)
    qd = QdrantBrainStore(
        brain_root=repo_path,
        workspace_slug=workspace_slug_for(workspace_id),
    )
    fanout = FanoutBrainStore(primary=json_store, mirrors=[pg, n4j, qd])

    # ── Stage 2 — Relationships ──────────────────────────────────────────────
    relationships: list[Any] = []
    if not skip_relationships:
        print(f"[enrich] Stage 2: Relationship extraction over {len(entities)} entities...")
        rel_extractor = RelationshipExtractor()
        api_snapshot = {
            "method": "GET",
            "path": f"workspace:{workspace_id}",
            "handler_code": "",
        }
        # No git clusters in enrich-mode; relationship extractor handles empty input.
        try:
            relationships = await rel_extractor.extract(
                entities=entities,
                clusters=[],
                api_snapshot=api_snapshot,
            )
            print(f"[enrich] Stage 2: extracted {len(relationships)} relationships")
        except Exception as exc:
            log.error("Relationship extraction failed during enrich", error=str(exc))
            print(f"[enrich] Stage 2 FAILED: {exc} — continuing without new relationships")
            relationships = []

    # ── Stage 3 — Context synthesis ──────────────────────────────────────────
    contexts: dict[str, Any] = {}
    if not skip_context:
        print(f"[enrich] Stage 3: Context synthesis over {len(entities)} entities...")
        ctx_syn = ContextSynthesizer()
        # Synthesize one entity at a time so a single failure doesn't poison the batch.
        for e in entities:
            try:
                ctx = await ctx_syn.synthesize_one(
                    entity=e,
                    relationships=relationships,
                    clusters=[],
                )
                if ctx is not None:
                    contexts[e.external_id] = ctx
            except AttributeError:
                # Older synthesizer doesn't expose synthesize_one — fall back to batch.
                try:
                    batch = await ctx_syn.synthesize(
                        entities=[e],
                        relationships=relationships,
                        clusters=[],
                    )
                    contexts.update(batch)
                except Exception as exc:
                    log.warning("Context synth failed for entity",
                                entity=e.external_id, error=str(exc))
            except Exception as exc:
                log.warning("Context synth failed for entity",
                            entity=e.external_id, error=str(exc))
        print(f"[enrich] Stage 3: synthesized {len(contexts)} contexts")

    # ── Flush back through fanout ────────────────────────────────────────────
    print(f"[enrich] Posting results to Java backend...")
    await java.flush(
        entities=entities,
        relationships=relationships,
        contexts=contexts,
        pipeline_meta={"run_id": "enrich"},
        status="completed",
    )

    # Mirror to Neo4j + Qdrant via the fanout's per-mirror commit
    for mirror in fanout.mirrors:
        try:
            # Replay each entity through the mirror so its buffers fill before commit.
            for be in entities_brain:
                await mirror.write(be, run_id="enrich", workspace_id=workspace_id)
            await mirror.commit_run("enrich")
        except Exception as exc:
            log.warning(
                "Mirror commit failed (non-fatal)",
                store=type(mirror).__name__,
                error=str(exc),
            )

    print(
        f"[enrich] DONE. "
        f"entities={len(entities)} relationships={len(relationships)} contexts={len(contexts)}"
    )


def enrich_existing_sync(
    repo_path: Path,
    workspace_id: str,
    *,
    skip_relationships: bool = False,
    skip_context: bool = False,
) -> None:
    """Sync wrapper used by the typer CLI command."""
    asyncio.run(enrich_existing(
        repo_path,
        workspace_id,
        skip_relationships=skip_relationships,
        skip_context=skip_context,
    ))
