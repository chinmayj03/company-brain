"""
brain enrich --repo <path>

Re-enrich the existing graph WITHOUT re-running Stage 1 (entity extraction).
NB: this CLI is run standalone (no `make backend` env wrapper) so we eagerly
load .env and patch a few sensible defaults at import time:
  - ANTHROPIC_API_KEY is required for Stage 2/3 LLM calls.
  - NEO4J_URI defaults to bolt://neo4j:7687 (Docker network alias) which the
    standalone CLI cannot resolve — falls back to bolt://localhost:7687 when
    the env var is unset or pointing at a Docker hostname.
"""
from __future__ import annotations

import os
from pathlib import Path as _Path

# ── Eager env loading — must happen BEFORE anything imports settings ─────────
def _load_env_for_cli() -> None:
    """Walk parents of CWD looking for a .env, load it without overriding existing env."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        # python-dotenv may not be installed in slimmer envs — fall back silently.
        return
    # Search a few parent dirs of the package + repo root for .env
    here = _Path(__file__).resolve()
    candidates = [
        _Path.cwd() / ".env",
        here.parent.parent.parent.parent / ".env",          # company-brain-ai/.env
        here.parent.parent.parent.parent.parent / ".env",   # company-brain/.env
    ]
    for c in candidates:
        if c.is_file():
            load_dotenv(c, override=False)
            break

    # Standalone CLI cannot resolve the Docker network alias `neo4j`. Fall back
    # to localhost when the env var is unset or still references the alias.
    uri = os.environ.get("NEO4J_URI", "")
    if not uri or "neo4j:7687" in uri:
        os.environ["NEO4J_URI"] = "bolt://localhost:7687"

_load_env_for_cli()
# ── End env bootstrapping ────────────────────────────────────────────────────

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
        try:
            # synthesise_all (British spelling) is the canonical entry; takes
            # entities + clusters + annotations and gathers per-entity coroutines
            # internally, returning {external_id: BusinessContext}.
            contexts = await ctx_syn.synthesise_all(
                entities=entities,
                clusters=[],
                annotations=[],
            )
        except Exception as exc:
            log.error("Context synthesis failed during enrich", error=str(exc))
            print(f"[enrich] Stage 3 FAILED: {exc} — continuing without new contexts")
            contexts = {}
        print(f"[enrich] Stage 3: synthesised {len(contexts)} contexts")

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
