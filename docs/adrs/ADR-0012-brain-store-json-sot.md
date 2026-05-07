# ADR-0012: BrainStore + `.brain/` JSON source-of-truth

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 3 days
**Depends on:** ADR-0011 (structural pre-pass shape stabilises before introducing JSON SOT)
**Unblocks:** ADR-0014, ADR-0015, ADR-0016, ADR-0017

---

## Context

Today every brain entity lands directly in Postgres via `JavaGraphClient.flush()` → `/v1/internal/pipeline-result` and dual-writes to Neo4j via `Neo4jWriter`. There is no per-repo JSON artifact. Consequences:

1. The brain is not git-trackable. A code change can ship without the corresponding brain change being reviewable in the PR.
2. If Postgres or Neo4j is wiped, the only way to repopulate is a full pipeline rerun (re-paying LLM cost).
3. There is no diff between brain runs — engineers cannot see "the brain learned X today."
4. The dual writers (Postgres + Neo4j + future Qdrant) repeatedly translate the same entity into different shapes. No canonical representation.

The harness `harness-system-design.md` §5 prescribes JSON files as the SOT. v2 §1 reaffirms: "JSON files remain as the export format. The brain stays git-friendly."

## Decision

Introduce a `BrainStore` interface whose primary implementation writes to `.brain/` in the target repo. Postgres (`PostgresBrainStore`) and Neo4j (`Neo4jBrainStore`) become **event consumers**, not direct writers. Pipeline outputs flow as `BrainEvent`s through a synchronous fan-out: JSON SOT → Postgres → Neo4j (→ Qdrant in ADR-0015).

JSON files are the source of truth. If both stores are wiped, `brain rebuild --from-json` reconstructs them.

## Implementation

### Module layout

```
company-brain-ai/src/companybrain/store/
├── __init__.py                  # exports public API
├── base.py                      # BrainStore interface, BrainEvent, BrainEntity
├── identity.py                  # placeholder for ADR-0013 (URN), here just to_external_id helper
├── json_store.py                # JsonFileBrainStore (THIS IS THE SOT)
├── postgres_consumer.py         # replays events into Java backend (existing JavaGraphClient under the hood)
├── neo4j_consumer.py            # replays events into Neo4j (wraps Neo4jWriter)
├── fanout.py                    # FanoutBrainStore — writes to one primary, mirrors to N consumers
└── freshness.py                 # is_fresh(entity_id, content_hash) — lifted from JavaGraphClient
```

### Files to create

#### `store/base.py`
```python
"""
BrainStore interface and event model.

A BrainEntity is the canonical in-memory representation of any brain entity.
A BrainEvent describes a write/upsert/delete. Stores consume events.

Storage hierarchy (read priority):
  JsonFileBrainStore  ← source of truth, always-correct
  PostgresBrainStore  ← projection, fast read for Java backend
  Neo4jBrainStore     ← projection, fast traversal
  QdrantBrainStore    ← projection, fast similarity (ADR-0015)
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, AsyncIterator, Optional


# ── Canonical entity shape ───────────────────────────────────────────────────

@dataclass
class BrainEntity:
    """
    The canonical brain entity. JSON-serialisable.

    `id` is the canonical identifier. ADR-0013 promotes this to a URN; today
    it is `{repo}::{entity_type}::{qualified_name}`.
    """
    id: str
    entity_type: str           # component | screen | api_contract | data_model | assumption | business_context | function_node
    repo: str
    file: str                  # relative to repo root
    qualified_name: str
    t1_summary: str = ""
    t0_token: str = ""         # ~15 tok
    t1_token: str = ""         # ~100 tok
    metadata: dict = field(default_factory=dict)
    relationships: list[dict] = field(default_factory=list)  # {target_id, edge_type, confidence, source}
    version_hash: str = ""     # sha256 of the entity's structural fingerprint
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_updated_by: str = "harness/extractor"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BrainEntity":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in valid})


# ── Events ────────────────────────────────────────────────────────────────────

@dataclass
class BrainEvent:
    kind: str                  # "upsert" | "invalidate" | "delete"
    entity: Optional[BrainEntity] = None    # set for upsert
    entity_id: Optional[str] = None         # set for invalidate / delete
    run_id: str = ""
    workspace_id: str = ""
    occurred_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# ── Interface ─────────────────────────────────────────────────────────────────

class BrainStore(ABC):
    """
    Stores can read, write, and emit events. Most implementations are
    write-through to a backing data store; the JSON store is the SOT.
    """

    @abstractmethod
    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None: ...

    @abstractmethod
    async def read(self, entity_id: str) -> Optional[BrainEntity]: ...

    @abstractmethod
    async def is_fresh(self, entity_id: str, version_hash: str) -> bool: ...

    @abstractmethod
    async def list_ids(self) -> AsyncIterator[str]: ...

    @abstractmethod
    async def commit_run(self, run_id: str) -> None:
        """Called once at end of pipeline. Stores can persist any in-memory state."""
```

#### `store/json_store.py`
```python
"""
.brain/ source-of-truth implementation.

File layout (per repo):
  .brain/
  ├── index.json                          ← entity_id → relative path
  ├── manifest.json                       ← run history, last_run_id, last_commit
  ├── component/<qname>.json
  ├── api_contract/<sanitised_qname>.json
  ├── data_model/<qname>.json
  ├── assumption/<qname>.json
  ├── business_context/<qname>.json
  ├── function_node/<qname>.json
  └── .l2-cache/<branch>.json             ← reserved for ADR-0014
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import structlog

from companybrain.store.base import BrainStore, BrainEntity

log = structlog.get_logger(__name__)

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _qname_to_filename(qname: str) -> str:
    """Sanitise a qualified name into a safe filename. Preserves enough for humans."""
    s = _SLUG.sub("_", qname)
    return s[:200]  # filesystem-safe length


class JsonFileBrainStore(BrainStore):
    """Writes one JSON file per entity under .brain/{type}/{qname}.json."""

    def __init__(self, brain_root: Path):
        self.root = Path(brain_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._manifest_path = self.root / "manifest.json"
        self._lock = asyncio.Lock()

    # ── BrainStore implementation ────────────────────────────────────────────

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        async with self._lock:
            entity_file = self._entity_path(entity.entity_type, entity.qualified_name)
            entity_file.parent.mkdir(parents=True, exist_ok=True)
            entity_file.write_text(json.dumps(entity.to_dict(), indent=2, sort_keys=True))
            self._update_index(entity.id, entity_file.relative_to(self.root))

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        idx = self._load_index()
        rel = idx.get(entity_id)
        if not rel:
            return None
        path = self.root / rel
        if not path.exists():
            return None
        return BrainEntity.from_dict(json.loads(path.read_text()))

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        existing = await self.read(entity_id)
        return existing is not None and existing.version_hash == version_hash

    async def list_ids(self) -> AsyncIterator[str]:
        for entity_id in self._load_index().keys():
            yield entity_id

    async def commit_run(self, run_id: str) -> None:
        manifest = self._load_manifest()
        manifest["last_run_id"] = run_id
        manifest["last_commit_at"] = datetime.utcnow().isoformat() + "Z"
        manifest.setdefault("runs", []).append({
            "run_id": run_id, "at": manifest["last_commit_at"],
        })
        self._manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    # ── Internals ────────────────────────────────────────────────────────────

    def _entity_path(self, entity_type: str, qname: str) -> Path:
        return self.root / entity_type / f"{_qname_to_filename(qname)}.json"

    def _load_index(self) -> dict:
        if not self._index_path.exists():
            return {}
        return json.loads(self._index_path.read_text())

    def _update_index(self, entity_id: str, rel_path: Path) -> None:
        idx = self._load_index()
        idx[entity_id] = str(rel_path)
        self._index_path.write_text(json.dumps(idx, indent=2, sort_keys=True))

    def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}
        return json.loads(self._manifest_path.read_text())
```

#### `store/postgres_consumer.py`
```python
"""
Postgres consumer — replays BrainEvents through the existing JavaGraphClient.

This is a thin shim. It exists so the orchestrator stops calling JavaGraphClient
directly and instead writes through BrainStore → events → consumer. Net effect on
the Java side: identical (still POSTs to /v1/internal/pipeline-result).
"""
from __future__ import annotations
from typing import Optional

from companybrain.graph.java_client import JavaGraphClient
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship, BusinessContext
from companybrain.store.base import BrainStore, BrainEntity


class PostgresBrainStore(BrainStore):
    """Wraps JavaGraphClient for write-path; read-path queries the Java REST API."""

    def __init__(self, java_client: JavaGraphClient):
        self._client = java_client
        self._buffered_entities: list[ExtractedEntity] = []
        self._buffered_relationships: list[ExtractedRelationship] = []
        self._buffered_contexts: dict[str, BusinessContext] = {}

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        self._buffered_entities.append(_to_extracted_entity(entity))
        self._buffered_relationships.extend(_to_relationships(entity))

    async def read(self, entity_id: str):
        # Optional: implement via Java REST API. Tests should hit the JSON store.
        return None

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        # Java side has its own freshness; forward.
        return False  # conservative — JSON store is the freshness oracle

    async def list_ids(self):
        if False: yield  # not implemented; Java is not a list source

    async def commit_run(self, run_id: str) -> None:
        if not self._buffered_entities:
            return
        await self._client.flush(
            entities=self._buffered_entities,
            relationships=self._buffered_relationships,
            contexts=self._buffered_contexts,
            pipeline_meta={"run_id": run_id},
            artifacts=[],
            intent_contexts={},
        )
        self._buffered_entities.clear()
        self._buffered_relationships.clear()
        self._buffered_contexts.clear()


def _to_extracted_entity(entity: BrainEntity) -> ExtractedEntity:
    """Translate canonical BrainEntity → existing ExtractedEntity."""
    from companybrain.models.entities import ExtractedEntity as EE
    return EE(
        entity_type=entity.entity_type,
        name=entity.qualified_name,
        file=entity.file,
        repo=entity.repo,
        signature=entity.metadata.get("signature", ""),
        confidence=entity.metadata.get("confidence", 0.9),
        code_snippet=entity.metadata.get("code_snippet"),
        query_text=entity.metadata.get("query_text"),
    )


def _to_relationships(entity: BrainEntity) -> list:
    from companybrain.models.entities import ExtractedRelationship as ER
    out = []
    for rel in entity.relationships:
        out.append(ER(
            from_entity=entity.id,
            to_entity=rel["target_id"],
            edge_type=rel["edge_type"],
            confidence=rel.get("confidence", 0.9),
            source=rel.get("source", "llm_extraction"),
            metadata=rel.get("metadata", {}),
        ))
    return out
```

#### `store/neo4j_consumer.py`
```python
"""
Neo4j consumer — wraps the existing graph/neo4j_writer.py.

Keeps the dual-write semantics that exist today; just hidden behind the
BrainStore contract.
"""
from __future__ import annotations
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.store.base import BrainStore, BrainEntity
from companybrain.store.postgres_consumer import _to_extracted_entity, _to_relationships


class Neo4jBrainStore(BrainStore):
    def __init__(self, writer: Neo4jWriter, workspace_id: str):
        self._writer = writer
        self._ws = workspace_id
        self._buf_e: list[ExtractedEntity] = []
        self._buf_r: list[ExtractedRelationship] = []

    async def write(self, entity, *, run_id, workspace_id):
        self._buf_e.append(_to_extracted_entity(entity))
        self._buf_r.extend(_to_relationships(entity))

    async def read(self, entity_id): return None
    async def is_fresh(self, entity_id, version_hash): return False
    async def list_ids(self):
        if False: yield

    async def commit_run(self, run_id):
        if not self._buf_e:
            return
        await self._writer.upsert_entities(self._buf_e, workspace_id=self._ws)
        await self._writer.upsert_relationships(self._buf_r, workspace_id=self._ws)
        self._buf_e.clear()
        self._buf_r.clear()
```

#### `store/fanout.py`
```python
"""
Fanout — primary store + N mirrors.

Writes go to `primary` first (JSON SOT). On success, mirrors are written
in parallel; mirror failures log but do not roll back the primary.
"""
from __future__ import annotations
import asyncio
import structlog

from companybrain.store.base import BrainStore, BrainEntity

log = structlog.get_logger(__name__)


class FanoutBrainStore(BrainStore):
    def __init__(self, primary: BrainStore, mirrors: list[BrainStore]):
        self.primary = primary
        self.mirrors = mirrors

    async def write(self, entity, *, run_id, workspace_id):
        await self.primary.write(entity, run_id=run_id, workspace_id=workspace_id)
        await asyncio.gather(*[
            self._safe_write(m, entity, run_id, workspace_id) for m in self.mirrors
        ])

    async def read(self, entity_id):
        return await self.primary.read(entity_id)

    async def is_fresh(self, entity_id, version_hash):
        return await self.primary.is_fresh(entity_id, version_hash)

    async def list_ids(self):
        async for x in self.primary.list_ids():
            yield x

    async def commit_run(self, run_id):
        await self.primary.commit_run(run_id)
        await asyncio.gather(*[m.commit_run(run_id) for m in self.mirrors],
                              return_exceptions=True)

    async def _safe_write(self, mirror, entity, run_id, workspace_id):
        try:
            await mirror.write(entity, run_id=run_id, workspace_id=workspace_id)
        except Exception as exc:
            log.warning("Mirror store write failed (non-fatal)",
                        store=type(mirror).__name__, error=str(exc))
```

#### `store/__init__.py`
```python
from companybrain.store.base import BrainStore, BrainEntity, BrainEvent
from companybrain.store.json_store import JsonFileBrainStore
from companybrain.store.postgres_consumer import PostgresBrainStore
from companybrain.store.neo4j_consumer import Neo4jBrainStore
from companybrain.store.fanout import FanoutBrainStore

__all__ = [
    "BrainStore", "BrainEntity", "BrainEvent",
    "JsonFileBrainStore", "PostgresBrainStore", "Neo4jBrainStore",
    "FanoutBrainStore",
]
```

### Edits to `pipeline/orchestrator.py`

After Stage 5 (graph population), add a step that converts the existing `entities`, `relationships`, and `contexts` into `BrainEntity` instances, writes them through a `FanoutBrainStore`, and calls `commit_run(job_id)`.

Skeleton:
```python
# at top of run_pipeline()
from companybrain.store import (
    JsonFileBrainStore, PostgresBrainStore, Neo4jBrainStore, FanoutBrainStore, BrainEntity,
)
from companybrain.graph.neo4j_writer import Neo4jWriter

# replace the existing graph_client.flush(...) block:
brain_root = _resolve_brain_root(request)   # <repo>/.brain
json_store = JsonFileBrainStore(brain_root)

pg_store = PostgresBrainStore(_graph_client_for_freshness)
neo4j_writer = Neo4jWriter(uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
                           user=os.getenv("NEO4J_USER", "neo4j"),
                           password=os.getenv("NEO4J_PASSWORD", "password"))
neo4j_store = Neo4jBrainStore(neo4j_writer, workspace_id=request.workspace_id)

store = FanoutBrainStore(primary=json_store, mirrors=[pg_store, neo4j_store])

for ee in entities:
    be = _to_brain_entity(ee, contexts.get(ee.external_id), memory_tokens.get(ee.external_id),
                          relationships, request.workspace_id)
    await store.write(be, run_id=job_id, workspace_id=request.workspace_id)

await store.commit_run(job_id)
```

Add helper `_to_brain_entity()` near the bottom of `orchestrator.py`. The `_resolve_brain_root()` helper returns `<repo_path>/.brain` if `request.repos[0].local_path` is set, otherwise a path under `/tmp`.

## Test plan

### Unit tests

`tests/unit/store/test_json_store.py`:
```python
import pytest
from pathlib import Path
from companybrain.store import JsonFileBrainStore, BrainEntity


@pytest.mark.asyncio
async def test_round_trip(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(id="repo::component::Foo", entity_type="component",
                    repo="repo", file="src/Foo.tsx", qualified_name="Foo",
                    t1_summary="Foo component", version_hash="abc")
    await store.write(e, run_id="r1", workspace_id="w")
    out = await store.read(e.id)
    assert out is not None and out.t1_summary == "Foo component"


@pytest.mark.asyncio
async def test_fresh_check_after_write(tmp_path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(id="r::c::A", entity_type="component", repo="r",
                    file="A.tsx", qualified_name="A", version_hash="v1")
    await store.write(e, run_id="r1", workspace_id="w")
    assert await store.is_fresh(e.id, "v1") is True
    assert await store.is_fresh(e.id, "v2") is False


@pytest.mark.asyncio
async def test_index_persistence(tmp_path):
    s = JsonFileBrainStore(tmp_path)
    await s.write(BrainEntity(id="x::y::A", entity_type="y", repo="x",
                              file="a", qualified_name="A"),
                  run_id="r", workspace_id="w")
    s2 = JsonFileBrainStore(tmp_path)
    assert await s2.read("x::y::A") is not None
```

`tests/unit/store/test_fanout.py`:
```python
import pytest
from companybrain.store import FanoutBrainStore, JsonFileBrainStore, BrainEntity


class _FailingMirror:
    async def write(self, e, *, run_id, workspace_id): raise RuntimeError("boom")
    async def read(self, _): return None
    async def is_fresh(self, _, __): return False
    async def list_ids(self):
        if False: yield
    async def commit_run(self, run_id): pass


@pytest.mark.asyncio
async def test_mirror_failure_does_not_break_primary(tmp_path):
    primary = JsonFileBrainStore(tmp_path)
    fan = FanoutBrainStore(primary=primary, mirrors=[_FailingMirror()])
    e = BrainEntity(id="r::c::A", entity_type="c", repo="r",
                    file="a", qualified_name="A", version_hash="v")
    await fan.write(e, run_id="r1", workspace_id="w")
    assert await primary.read(e.id) is not None
```

### Integration test

After running the orchestrator on the pilot repo, assert:
- `<pilot>/.brain/index.json` exists.
- `<pilot>/.brain/component/<some_name>.json` exists and is valid JSON.
- `psql -c "SELECT count(*) FROM nodes WHERE workspace_id=...;"` returns the same count as `find <pilot>/.brain -name '*.json' | wc -l` minus the 2 metadata files.

## Acceptance criteria

- [ ] `companybrain/store/` package compiles and exports `BrainStore`, `BrainEntity`, `BrainEvent`, all four store classes.
- [ ] `JsonFileBrainStore` round-trips entities through disk.
- [ ] `FanoutBrainStore` mirror failures do not raise to the caller.
- [ ] Orchestrator writes through `FanoutBrainStore` instead of calling `JavaGraphClient.flush()` directly.
- [ ] After a pipeline run, `<repo>/.brain/index.json` and per-entity JSONs exist and are valid.
- [ ] `git status` in the pilot repo shows the `.brain/` directory as new untracked content (engineer can `git add`/commit).
- [ ] Postgres + Neo4j get the same data they got before this change.
- [ ] All existing tests still pass.

## Verification commands

```bash
# 1. Run pipeline
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET

# 2. Inspect .brain/
ls -la pilot/.brain/
cat pilot/.brain/index.json | jq 'keys | length'      # expect: matches Postgres count
cat pilot/.brain/manifest.json | jq '.last_run_id'    # expect: the job_id from the run

# 3. Verify Postgres parity
psql -h localhost -U companybrain -d companybrain -c "
  SELECT count(*) FROM nodes WHERE workspace_id='<uuid>';
"

# 4. Verify Neo4j parity
cypher-shell -u neo4j -p password "
  MATCH (n) WHERE n.scope='<workspace>' RETURN count(n);
"
```

## Rollback

```bash
git revert <commit-sha>
# Remove .brain/ from pilot repo if its presence interferes with anything:
rm -rf pilot/.brain/
```

## Out of scope

- **Read path through BrainStore.** The Java backend's existing REST API still serves the React UI. Migrating reads to go through `BrainStore.read()` is a follow-up.
- **`brain rebuild --from-json` CLI.** Implemented in ADR-0016.
- **Canonical URN ID format.** Implemented in ADR-0013.
- **Qdrant projection.** Implemented in ADR-0015.
- **Single-event-bus architecture (publish to Kafka/Redis Streams).** This ADR uses synchronous fan-out. A streaming bus is a v2 concern.
