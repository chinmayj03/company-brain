"""rebuild-from-json: read .brain/ JSONs, fan out to Postgres + Neo4j + Qdrant.

Postgres mirror requires the Java API (`/v1/internal/pipeline-result`). When
that service is not running (common in dev where `make up-all` does not start
the Java app), the mirror is skipped with a warning rather than aborting the
whole rebuild — the JSON store is the source of truth and Neo4j + Qdrant can
still be refreshed.
"""
from __future__ import annotations
import asyncio
import uuid
from pathlib import Path

import httpx
import structlog

from companybrain.config import settings
from companybrain.graph.java_client import JavaGraphClient
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store import (
    FanoutBrainStore, JsonFileBrainStore, Neo4jBrainStore, PostgresBrainStore,
)
from companybrain.store.identity import workspace_slug_for

log = structlog.get_logger(__name__)


async def _java_api_reachable(java: JavaGraphClient) -> bool:
    """Probe the Java API's pipeline-result endpoint with a short timeout.

    Returns True only if a real HTTP connection succeeds (any non-network
    response counts — auth failures, 404s, etc. all mean "the server is up").
    """
    # _result_url ends with /v1/internal/pipeline-result; the server root is
    # one parent up.
    base = str(java._result_url).rsplit("/v1/", 1)[0]
    probes = [f"{base}/actuator/health", f"{base}/health", base]
    for url in probes:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0)) as client:
                resp = await client.get(url)
                # Any HTTP response (even 4xx) means the server is reachable.
                _ = resp.status_code
                return True
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            continue
        except Exception:   # noqa: BLE001
            continue
    return False


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

    java_ok = await _java_api_reachable(java)

    mirrors: list = []
    if java_ok:
        mirrors.append(PostgresBrainStore(java))
    else:
        log.warning(
            "rebuild_from_json.skip_postgres_mirror",
            reason="Java API unreachable",
            hint="Start the Java service or set JAVA_API_URL; rebuild will still refresh Neo4j + Qdrant.",
        )
        print("⚠ Java API not reachable — skipping Postgres mirror; "
              "Neo4j + Qdrant + .brain/ JSON will still be rebuilt.")

    mirrors.append(Neo4jBrainStore(Neo4jWriter(workspace_id=workspace_id), workspace_id=workspace_id))
    mirrors.append(QdrantBrainStore(brain_root=repo_path,
                                    workspace_slug=workspace_slug_for(workspace_id)))

    fanout = FanoutBrainStore(primary=json_store, mirrors=mirrors)

    count = 0
    write_failures: dict[str, int] = {}
    async for entity_id in json_store.list_ids():
        entity = await json_store.read(entity_id)
        if entity is None:
            continue
        for mirror in fanout.mirrors:
            try:
                await mirror.write(entity, run_id="rebuild", workspace_id=workspace_id)
            except Exception as exc:   # noqa: BLE001
                key = f"{mirror.__class__.__name__}:{type(exc).__name__}"
                write_failures[key] = write_failures.get(key, 0) + 1
        count += 1

    for mirror in fanout.mirrors:
        try:
            await mirror.commit_run("rebuild")
        except Exception as exc:   # noqa: BLE001
            log.error(
                "rebuild_from_json.commit_failed",
                mirror=mirror.__class__.__name__,
                error=str(exc),
            )
            print(f"⚠ {mirror.__class__.__name__}.commit_run failed: {type(exc).__name__}: {exc}")

    if write_failures:
        for key, n in write_failures.items():
            print(f"⚠ {n} per-entity write failures: {key}")

    print(f"✓ rebuilt {count} entities from {brain_root} "
          f"({len(fanout.mirrors)} mirror(s)"
          f"{', java skipped' if not java_ok else ''})")
