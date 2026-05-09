"""rebuild-from-json: read .brain/ JSONs, fan out to Postgres + Neo4j + Qdrant."""
from __future__ import annotations
import uuid
from pathlib import Path

from companybrain.graph.java_client import JavaGraphClient
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store import (
    FanoutBrainStore, JsonFileBrainStore, Neo4jBrainStore, PostgresBrainStore,
)
from companybrain.store.identity import workspace_slug_for


async def rebuild_from_json(repo_path: Path, workspace_id: str) -> None:
    brain_root = repo_path / ".brain"
    if not brain_root.exists():
        raise FileNotFoundError(f"No .brain/ in {repo_path}")

    json_store = JsonFileBrainStore(brain_root)

    # Java's PipelineResultRequest.jobId is a UUID; passing the literal string
    # "rebuild" caused Jackson to throw InvalidFormatException → 500.
    # markCompleted is an UPDATE — a fresh UUID with no matching pipeline_jobs
    # row simply hits 0 rows, which is fine for replay.
    rebuild_job_id = str(uuid.uuid4())
    java = JavaGraphClient(workspace_id=workspace_id, job_id=rebuild_job_id)
    pg = PostgresBrainStore(java)
    n4j = Neo4jBrainStore(Neo4jWriter(workspace_id=workspace_id), workspace_id=workspace_id)
    qd = QdrantBrainStore(brain_root=repo_path,
                          workspace_slug=workspace_slug_for(workspace_id))

    fanout = FanoutBrainStore(primary=json_store, mirrors=[pg, n4j, qd])

    count = 0
    async for entity_id in json_store.list_ids():
        entity = await json_store.read(entity_id)
        if entity is not None:
            for mirror in fanout.mirrors:
                await mirror.write(entity, run_id="rebuild", workspace_id=workspace_id)
            count += 1
    for mirror in fanout.mirrors:
        await mirror.commit_run("rebuild")

    print(f"✓ rebuilt {count} entities from {brain_root}")
