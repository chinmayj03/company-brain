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

...WITHOUT paying ~$0.30 per run for Stage 1 LLM entity extraction (which is the
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
for a full pipeline run). Roughly 5x cheaper.

NB: this CLI is run standalone (no `make backend` env wrapper) so we eagerly
load .env and patch a few sensible defaults at import time:
  - ANTHROPIC_API_KEY is required for Stage 2/3 LLM calls.
  - NEO4J_URI defaults to bolt://neo4j:7687 (Docker network alias) which the
    standalone CLI cannot resolve - falls back to bolt://localhost:7687 when
    the env var is unset or pointing at a Docker hostname.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import structlog


# ── Eager env loading - must happen BEFORE anything imports settings ─────────
def _load_env_for_cli() -> None:
    """Load .env from every candidate path (company-brain-ai/, company-brain/, cwd).

    Loads ALL of them, not just the first match — an incomplete .env in
    company-brain-ai/ used to shadow the real one in company-brain/. With
    override=False, the first file that defines a var wins, but later files
    can fill in vars the earlier ones missed.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        # python-dotenv may not be installed in slimmer envs - fall back silently.
        print("[enrich] python-dotenv not installed — relying on already-exported env")
        return
    here = Path(__file__).resolve()
    # The repo-root file is the source of truth — load it WITH override=True so
    # a stale ANTHROPIC_API_KEY left in the shell env (from `make backend` or a
    # previous terminal) cannot shadow the correct key in .env.  The other
    # candidates load with override=False to only fill in missing vars.
    repo_root_env = here.parent.parent.parent.parent.parent / ".env"  # company-brain/.env
    fallback_candidates = [
        Path.cwd() / ".env",
        here.parent.parent.parent.parent / ".env",          # company-brain-ai/.env
        here.parent.parent.parent.parent.parent.parent / ".env",  # one above repo root
    ]

    pre_key = os.environ.get("ANTHROPIC_API_KEY", "")
    pre_src = "shell" if pre_key else "(unset)"

    loaded_any = False
    if repo_root_env.is_file():
        load_dotenv(repo_root_env, override=True)
        print(f"[enrich] loaded env from {repo_root_env} (override=True)")
        loaded_any = True

    seen: set[str] = {str(repo_root_env.resolve())} if repo_root_env.is_file() else set()
    for c in fallback_candidates:
        try:
            real = c.resolve()
        except OSError:
            continue
        if str(real) in seen:
            continue
        seen.add(str(real))
        if real.is_file():
            load_dotenv(real, override=False)
            print(f"[enrich] loaded env from {real} (override=False)")
            loaded_any = True

    if not loaded_any:
        print("[enrich] No .env file found in any candidate path; "
              "relying on already-exported env vars")

    # Standalone CLI cannot resolve the Docker network alias `neo4j`. Fall back
    # to localhost when the env var is unset or still references the alias.
    uri = os.environ.get("NEO4J_URI", "")
    if not uri or "neo4j:7687" in uri:
        os.environ["NEO4J_URI"] = "bolt://localhost:7687"

    # Surface a clear error early if the API key didn't make it through, rather
    # than letting the LLM client raise an opaque 401 a few seconds later.
    final_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not final_key:
        print(
            "[enrich] WARNING: ANTHROPIC_API_KEY not set after .env load. "
            "Stage 2 (relationships) and Stage 3 (context) will 401."
        )
    else:
        # Print just the prefix and tail (~10 chars total) — enough for the
        # user to recognise WHICH key is being used without leaking the secret.
        masked = f"{final_key[:7]}…{final_key[-4:]}"
        changed = "CHANGED" if pre_key and pre_key != final_key else "kept"
        print(f"[enrich] ANTHROPIC_API_KEY in use: {masked}  (was {pre_src}, now {changed})")


_load_env_for_cli()
# ── End env bootstrapping ────────────────────────────────────────────────────


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
    """Re-run Stage 2 (relationships) + Stage 3 (context) over existing .brain/ entities."""
    brain_root = repo_path / ".brain"
    if not brain_root.exists():
        raise FileNotFoundError(f"No .brain/ in {repo_path}")

    # Lazy imports - these pull in heavy LLM deps; defer until we know we'll use them.
    from companybrain.pipeline.relationship_extractor import RelationshipExtractor
    from companybrain.pipeline.context_synthesizer import ContextSynthesizer

    json_store = JsonFileBrainStore(brain_root)

    print(f"[enrich] Loading entities from {brain_root}...")
    entities_brain: list[Any] = []
    async for eid in json_store.list_ids():
        e = await json_store.read(eid)
        if e is not None:
            entities_brain.append(e)
    print(f"[enrich] Loaded {len(entities_brain)} entities")

    if not entities_brain:
        print("[enrich] No entities found - run the full pipeline first to populate .brain/.")
        return

    # Translate BrainEntity -> ExtractedEntity so existing extractors accept them.
    entities = [_to_extracted_entity(e) for e in entities_brain]

    enrich_job_id = str(uuid.uuid4())
    java = JavaGraphClient(workspace_id=workspace_id, job_id=enrich_job_id)
    pg = PostgresBrainStore(java)
    n4j = Neo4jBrainStore(Neo4jWriter(workspace_id=workspace_id), workspace_id=workspace_id)
    qd = QdrantBrainStore(
        brain_root=repo_path,
        workspace_slug=workspace_slug_for(workspace_id),
    )
    fanout = FanoutBrainStore(primary=json_store, mirrors=[pg, n4j, qd])

    # ── Stage 2 - Relationships ──────────────────────────────────────────────
    relationships: list[Any] = []
    if not skip_relationships:
        print(f"[enrich] Stage 2: Relationship extraction over {len(entities)} entities...")
        rel_extractor = RelationshipExtractor()
        api_snapshot = {
            "method": "GET",
            "path": f"workspace:{workspace_id}",
            "handler_code": "",
        }
        try:
            relationships = await rel_extractor.extract(
                entities=entities,
                clusters=[],
                api_snapshot=api_snapshot,
            )
            print(f"[enrich] Stage 2: extracted {len(relationships)} relationships")
        except Exception as exc:
            log.error("Relationship extraction failed during enrich", error=str(exc))
            print(f"[enrich] Stage 2 FAILED: {exc} - continuing without new relationships")
            relationships = []

    # ── Stage 3 - Context synthesis ──────────────────────────────────────────
    contexts: dict[str, Any] = {}
    if not skip_context:
        print(f"[enrich] Stage 3: Context synthesis over {len(entities)} entities...")
        ctx_syn = ContextSynthesizer()
        try:
            # synthesise_all (British spelling) is the canonical entry; takes
            # entities + clusters + annotations and gathers per-entity coroutines
            # internally with return_exceptions=True so per-entity failures
            # don't poison the whole batch.
            contexts = await ctx_syn.synthesise_all(
                entities=entities,
                clusters=[],
                annotations=[],
            )
        except Exception as exc:
            log.error("Context synthesis failed during enrich", error=str(exc))
            print(f"[enrich] Stage 3 FAILED: {exc} - continuing without new contexts")
            contexts = {}
        print(f"[enrich] Stage 3: synthesised {len(contexts)} contexts")

    # ── Persist LLM output to .brain/ FIRST so it survives mirror failures ──
    # If Java's persistence fails (FK violation, schema drift, network blip),
    # the user shouldn't have to pay for the LLM passes again. By updating
    # each BrainEntity's relationships/context in the JSON SOT before any
    # network call, a subsequent `brain rebuild-from-json` recovers everything
    # for free.
    print("[enrich] Persisting LLM output to .brain/ JSON (durable; no LLM re-run on failure)")
    rels_by_from: dict[str, list[dict]] = {}
    for r in relationships:
        rels_by_from.setdefault(r.from_entity, []).append({
            "edge_type":   r.edge_type,
            "target_id":   r.to_entity,
            "target_type": r.to_type,
            "confidence":  r.confidence,
            "evidence":    r.evidence,
            "source":      "enrich",
        })

    for be in entities_brain:
        # Match relationships keyed either by URN (be.id) or by qualified_name.
        new_rels = rels_by_from.get(be.id, []) + rels_by_from.get(be.qualified_name, [])
        if new_rels:
            # Replace, don't append: this run is the source of truth for the
            # new edge taxonomy.
            be.relationships = new_rels

        # Inject the context blob keyed by external_id (urn or repo/file::name).
        # ContextSynthesizer keys by entity.external_id which on ExtractedEntity
        # is `repo/file::name`; check both forms.
        ctx_key_a = f"{be.repo}/{be.file}::{be.qualified_name}"
        ctx_key_b = be.id
        ctx = contexts.get(ctx_key_a) or contexts.get(ctx_key_b)
        if ctx is not None:
            # BusinessContext is a dataclass — convert to dict for the JSON store.
            from dataclasses import asdict
            be.metadata = {**(be.metadata or {}), "business_context": asdict(ctx)}

        # Write the updated entity back to .brain/ JSON.
        await json_store.write(be, run_id="enrich", workspace_id=workspace_id)

    await json_store.commit_run("enrich")
    print(f"[enrich] .brain/ updated for {len(entities_brain)} entities")

    # ── Now mirror to Java/Neo4j/Qdrant ──────────────────────────────────────
    # If any of these fail, the user can recover for $0 with:
    #   brain rebuild-from-json --repo <repo> --workspace-id <id>
    print("[enrich] Mirroring to Java/Postgres + Neo4j + Qdrant ...")
    try:
        await java.flush(
            entities=entities,
            relationships=relationships,
            contexts=contexts,
            pipeline_meta={"run_id": "enrich"},
            status="completed",
        )
        print("[enrich] Java/Postgres mirror OK")
    except Exception as exc:
        log.error("Java mirror failed (non-fatal — .brain/ already persisted)",
                  error=str(exc))
        print(f"[enrich] Java mirror FAILED: {exc}")
        print("[enrich] Recover with: brain rebuild-from-json --repo <path> "
              "--workspace-id <id>  (no LLM re-run needed)")

    for mirror in fanout.mirrors:
        try:
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
