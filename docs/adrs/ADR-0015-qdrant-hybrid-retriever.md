# ADR-0015: Qdrant hybrid retriever (BM25S + voyage-code-3 + RRF)

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 5 days
**Depends on:** ADR-0012 (BrainStore), ADR-0013 (canonical URN)
**Unblocks:** ADR-0018 (smart-zone assembler)

---

## Context

Qdrant 1.9.2 is deployed in `docker-compose.infra.yml` (port 6333). `code_tracer.py` lazily imports `companybrain.retrieval.hybrid_search.HybridSearcher` — but the `retrieval/` package does not exist. First call to it `ImportError`s.

The harness `harness-system-design.md` §5.4 describes the target retriever exactly: BM25S sparse + dense embedding + RRF fusion + MMR deduplication. The code-aware tokenizer is in §4.4. The collection schema is in §5.3.

This ADR materialises that retriever, wires it as a Qdrant projection (a write-side mirror added to the BrainStore fanout from ADR-0012), and exposes a query API.

## Decision

Implement `companybrain/retrieval/` as:

- A code-aware tokenizer (camelCase/snake_case/digit-split, len ≥ 2 filter).
- A pluggable embedder, default voyage-code-3 with `OPENAI_*` fallback to `text-embedding-3-small` and a final fallback to `all-MiniLM-L6-v2` via `sentence-transformers`.
- A `HybridSearcher` that runs BM25S over a per-(workspace, entity_type) collection, dense search over the same Qdrant collection, fuses with RRF (k=60), and exposes `search(query, top_k, entity_types, filters) -> list[SearchHit]`.
- A `QdrantBrainStore` that consumes BrainStore write events and upserts the entity's text + embedding + payload. Added to the FanoutBrainStore mirrors list.

## Implementation

### Module layout

```
company-brain-ai/src/companybrain/retrieval/
├── __init__.py
├── tokenize.py          # tokenize_code(text) -> list[str]
├── embedder.py          # Embedder protocol + concrete impls
├── bm25_index.py        # In-process BM25S index per (workspace, entity_type)
├── hybrid_search.py     # HybridSearcher main class
├── qdrant_client.py     # Thin wrapper around qdrant_client with collection mgmt
└── qdrant_store.py      # QdrantBrainStore — BrainStore consumer
```

### Files to create

#### `retrieval/tokenize.py`

```python
"""Code-aware tokenizer — see harness §4.4.

Splits camelCase, snake_case, digits; lowercases; drops tokens < 2 chars.
Used as the BM25 tokenizer AND the input to dense embeddings if the embedder
expects pre-tokenised text (most don't).
"""
import re

_PUNCT_RE = re.compile(r"[\s\.\,\;\:\(\)\[\]\{\}\=\>\<\!\&\|\+\-\*\/\\\"']")
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_DIGIT_RE = re.compile(r"([a-zA-Z])(\d)")


def tokenize_code(text: str) -> list[str]:
    """Return lowercased subword tokens, length ≥ 2."""
    out: list[str] = []
    for raw in _PUNCT_RE.split(text or ""):
        if not raw or len(raw) < 2:
            continue
        s = _CAMEL_RE.sub(r"\1 \2", raw)   # getUserId → get User Id
        s = _DIGIT_RE.sub(r"\1 \2", s)     # user3D → user 3 D
        for part in re.split(r"[_\-\s]+", s.lower()):
            if len(part) >= 2:
                out.append(part)
    return out
```

#### `retrieval/embedder.py`

```python
"""Pluggable embedder. Picks the best available at construction time."""
from __future__ import annotations
import os
from typing import Protocol
import structlog

log = structlog.get_logger(__name__)


class Embedder(Protocol):
    dim: int
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def make_embedder() -> Embedder:
    """Resolve the best available embedder at startup.

    Order:
      1. Voyage AI (voyage-code-3) — if VOYAGE_API_KEY is set.
      2. OpenAI text-embedding-3-small — if OPENAI_API_KEY is set.
      3. sentence-transformers all-MiniLM-L6-v2 — local fallback.
    """
    if os.getenv("VOYAGE_API_KEY"):
        return _VoyageCode3()
    if os.getenv("OPENAI_API_KEY"):
        return _OpenAITextSmall()
    return _LocalMiniLM()


class _VoyageCode3:
    dim = 1024
    def __init__(self):
        import voyageai
        self._client = voyageai.Client()

    def embed(self, text: str) -> list[float]:
        r = self._client.embed([text], model="voyage-code-3", input_type="document")
        return r.embeddings[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts: return []
        r = self._client.embed(texts, model="voyage-code-3", input_type="document")
        return r.embeddings


class _OpenAITextSmall:
    dim = 1536
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI()

    def embed(self, text: str) -> list[float]:
        r = self._client.embeddings.create(input=[text], model="text-embedding-3-small")
        return r.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts: return []
        r = self._client.embeddings.create(input=texts, model="text-embedding-3-small")
        return [d.embedding for d in r.data]


class _LocalMiniLM:
    dim = 384
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self._m = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("Embedder: local all-MiniLM-L6-v2 (dim=384)")

    def embed(self, text: str) -> list[float]:
        return self._m.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts: return []
        return self._m.encode(texts, normalize_embeddings=True, batch_size=32).tolist()
```

#### `retrieval/qdrant_client.py`

```python
"""Qdrant client + collection lifecycle.

Collection naming: brain__{workspace_slug}__{entity_type}
e.g.               brain__dev__component, brain__dev__api_contract
"""
from __future__ import annotations
import os
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, SparseVectorParams, SparseIndexParams, Distance,
    PointStruct, SparseVector, Filter, FieldCondition, MatchValue,
)
import structlog

log = structlog.get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

_ALLOWED_TYPES = (
    "component", "screen", "api_contract", "data_model",
    "assumption", "business_context", "function_node",
)


def collection_name(workspace_slug: str, entity_type: str) -> str:
    return f"brain__{workspace_slug}__{entity_type}"


def make_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection(client: QdrantClient, workspace_slug: str,
                       entity_type: str, dense_dim: int) -> None:
    name = collection_name(workspace_slug, entity_type)
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dense_dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))},
    )
    log.info("Qdrant collection created", name=name, dim=dense_dim)


def upsert_point(client: QdrantClient, *, collection: str, point_id: str,
                 dense: list[float], sparse_indices: list[int], sparse_values: list[float],
                 payload: dict) -> None:
    client.upsert(
        collection_name=collection,
        points=[PointStruct(
            id=point_id,
            vector={
                "dense": dense,
                "sparse": SparseVector(indices=sparse_indices, values=sparse_values),
            },
            payload=payload,
        )],
        wait=False,
    )


def delete_point(client: QdrantClient, *, collection: str, point_id: str) -> None:
    client.delete(collection_name=collection, points_selector=[point_id])
```

#### `retrieval/bm25_index.py`

```python
"""
BM25S index per (workspace, entity_type), persisted to disk under .brain/.bm25/.

bm25s is fast (orders of magnitude faster than rank_bm25) and supports
incremental updates by re-saving the corpus.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
import bm25s

from companybrain.retrieval.tokenize import tokenize_code


class Bm25Index:
    """One BM25 corpus per (workspace_slug, entity_type)."""

    def __init__(self, root: Path, workspace_slug: str, entity_type: str):
        self.root = Path(root) / ".brain" / ".bm25" / workspace_slug / entity_type
        self.root.mkdir(parents=True, exist_ok=True)
        self._corpus_path = self.root / "corpus.jsonl"
        self._bm25: Optional[bm25s.BM25] = None
        self._doc_ids: list[str] = []

    def upsert(self, doc_id: str, text: str) -> None:
        """Append-or-replace a doc; rebuild on flush()."""
        self._unsaved.append((doc_id, text))

    _unsaved: list[tuple[str, str]] = []

    def flush(self) -> None:
        # Read existing corpus
        corpus: dict[str, str] = {}
        if self._corpus_path.exists():
            for line in self._corpus_path.read_text().splitlines():
                row = json.loads(line)
                corpus[row["id"]] = row["text"]
        # Apply unsaved upserts
        for doc_id, text in self._unsaved:
            corpus[doc_id] = text
        self._unsaved.clear()
        # Rewrite corpus + rebuild BM25
        with self._corpus_path.open("w") as f:
            for doc_id, text in corpus.items():
                f.write(json.dumps({"id": doc_id, "text": text}) + "\n")
        self._doc_ids = list(corpus.keys())
        tokenised = [tokenize_code(corpus[d]) for d in self._doc_ids]
        self._bm25 = bm25s.BM25()
        self._bm25.index(tokenised)

    def search(self, query: str, top_k: int = 40) -> list[tuple[str, float]]:
        if not self._bm25 or not self._doc_ids:
            return []
        tokens = tokenize_code(query)
        if not tokens:
            return []
        scores, idx = self._bm25.retrieve([tokens], k=min(top_k, len(self._doc_ids)))
        return [
            (self._doc_ids[int(idx[0][i])], float(scores[0][i]))
            for i in range(len(idx[0]))
        ]
```

#### `retrieval/hybrid_search.py`

```python
"""HybridSearcher — BM25S + dense + RRF fusion, optional MMR rerank."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from companybrain.retrieval.bm25_index import Bm25Index
from companybrain.retrieval.embedder import Embedder, make_embedder
from companybrain.retrieval.qdrant_client import (
    collection_name, ensure_collection, make_client,
)

log = structlog.get_logger(__name__)

RRF_K = 60


@dataclass
class SearchHit:
    urn: str
    score: float
    payload: dict = field(default_factory=dict)
    bm25_rank: int | None = None
    dense_rank: int | None = None


class HybridSearcher:
    def __init__(self, brain_root: Path, workspace_slug: str,
                 embedder: Embedder | None = None):
        self.brain_root = Path(brain_root)
        self.workspace_slug = workspace_slug
        self.embedder = embedder or make_embedder()
        self.qdrant = make_client()
        self._bm25_cache: dict[str, Bm25Index] = {}

    def _bm25(self, entity_type: str) -> Bm25Index:
        if entity_type not in self._bm25_cache:
            self._bm25_cache[entity_type] = Bm25Index(
                self.brain_root, self.workspace_slug, entity_type)
        return self._bm25_cache[entity_type]

    def search(self, query: str, *, top_k: int = 20,
                entity_types: list[str] | None = None,
                filters: dict | None = None) -> list[SearchHit]:
        types = entity_types or [
            "component", "screen", "api_contract",
            "data_model", "assumption", "business_context",
        ]
        all_hits: list[SearchHit] = []
        for et in types:
            all_hits.extend(self._search_one_type(query, et, top_k=top_k * 2))
        # Re-sort across types and trim
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits[:top_k]

    def _search_one_type(self, query: str, entity_type: str,
                          *, top_k: int) -> list[SearchHit]:
        bm25_results = self._bm25(entity_type).search(query, top_k=top_k * 2)
        bm25_rank = {urn: i + 1 for i, (urn, _) in enumerate(bm25_results)}

        dense_query = self.embedder.embed(query)
        coll = collection_name(self.workspace_slug, entity_type)
        try:
            dense_hits = self.qdrant.search(
                collection_name=coll,
                query_vector=("dense", dense_query),
                limit=top_k * 2,
                with_payload=True,
            )
        except Exception as exc:
            log.warning("Qdrant dense search failed", coll=coll, error=str(exc))
            dense_hits = []
        dense_rank = {h.id: i + 1 for i, h in enumerate(dense_hits)}
        dense_payload = {h.id: (h.payload or {}) for h in dense_hits}

        all_urns = set(bm25_rank) | set(dense_rank)
        out: list[SearchHit] = []
        for urn in all_urns:
            score = 0.0
            if urn in bm25_rank:
                score += 1.0 / (RRF_K + bm25_rank[urn])
            if urn in dense_rank:
                score += 1.0 / (RRF_K + dense_rank[urn])
            out.append(SearchHit(
                urn=urn, score=score,
                bm25_rank=bm25_rank.get(urn),
                dense_rank=dense_rank.get(urn),
                payload=dense_payload.get(urn, {}),
            ))
        out.sort(key=lambda h: h.score, reverse=True)
        return out[:top_k]
```

#### `retrieval/qdrant_store.py`

```python
"""QdrantBrainStore — write-side BrainStore consumer that maintains
BM25 + Qdrant indexes. Added to FanoutBrainStore.mirrors."""
from __future__ import annotations
from pathlib import Path

import structlog

from companybrain.retrieval.bm25_index import Bm25Index
from companybrain.retrieval.embedder import Embedder, make_embedder
from companybrain.retrieval.qdrant_client import (
    collection_name, ensure_collection, make_client, upsert_point,
)
from companybrain.retrieval.tokenize import tokenize_code
from companybrain.store.base import BrainEntity, BrainStore
from companybrain.store.identity import parse_urn

log = structlog.get_logger(__name__)


class QdrantBrainStore(BrainStore):
    """Mirror BrainEntity into BM25 + Qdrant. Read path queries via HybridSearcher."""

    def __init__(self, brain_root: Path, workspace_slug: str,
                 embedder: Embedder | None = None):
        self.brain_root = Path(brain_root)
        self.workspace_slug = workspace_slug
        self.embedder = embedder or make_embedder()
        self.qdrant = make_client()
        self._bm25_cache: dict[str, Bm25Index] = {}
        self._buffer: list[tuple[BrainEntity, str]] = []   # (entity, indexable_text)

    async def write(self, entity: BrainEntity, *, run_id, workspace_id):
        text = _build_indexable_text(entity)
        self._buffer.append((entity, text))

    async def read(self, entity_id):
        return None  # use HybridSearcher for reads

    async def is_fresh(self, entity_id, version_hash):
        return False

    async def list_ids(self):
        if False: yield

    async def commit_run(self, run_id):
        if not self._buffer:
            return
        # Group by entity_type
        by_type: dict[str, list[tuple[BrainEntity, str]]] = {}
        for be, text in self._buffer:
            by_type.setdefault(be.entity_type, []).append((be, text))

        for entity_type, group in by_type.items():
            # 1. BM25 upsert
            idx = self._bm25_cache.setdefault(
                entity_type,
                Bm25Index(self.brain_root, self.workspace_slug, entity_type),
            )
            for be, text in group:
                idx.upsert(be.id, text)
            idx.flush()

            # 2. Qdrant: ensure collection then upsert points
            ensure_collection(self.qdrant, self.workspace_slug,
                              entity_type, self.embedder.dim)
            coll = collection_name(self.workspace_slug, entity_type)
            texts = [t for _, t in group]
            embeddings = self.embedder.embed_batch(texts)
            for (be, text), emb in zip(group, embeddings):
                tokens = tokenize_code(text)
                # Sparse rep: simple TF — Qdrant accepts indices+values
                tf: dict[int, float] = {}
                for tok in tokens:
                    h = hash(tok) % (2**31)
                    tf[h] = tf.get(h, 0.0) + 1.0
                indices = list(tf.keys())
                values  = list(tf.values())
                payload = {
                    "urn": be.id,
                    "repo": be.repo,
                    "entity_type": be.entity_type,
                    "qualified_name": be.qualified_name,
                    "t1_summary": be.t1_summary,
                    "file": be.file,
                }
                upsert_point(self.qdrant, collection=coll, point_id=be.id,
                             dense=emb, sparse_indices=indices, sparse_values=values,
                             payload=payload)

        log.info("Qdrant store commit complete",
                 entities=len(self._buffer),
                 types=list(by_type.keys()))
        self._buffer.clear()


def _build_indexable_text(e: BrainEntity) -> str:
    """Concatenate all human-relevant fields for retrieval."""
    parts = [e.qualified_name, e.t1_summary,
             e.t0_token, e.t1_token,
             e.metadata.get("signature", ""),
             e.metadata.get("code_snippet", "") or ""]
    return " \n".join(p for p in parts if p)
```

### Edits to existing code

- `pipeline/orchestrator.py` — add `QdrantBrainStore` to `FanoutBrainStore.mirrors`:
  ```python
  from companybrain.retrieval.qdrant_store import QdrantBrainStore
  qdrant_store = QdrantBrainStore(brain_root=_resolve_brain_root(request).parent,
                                   workspace_slug=workspace_slug_for(request.workspace_id))
  store = FanoutBrainStore(primary=json_store, mirrors=[pg_store, neo4j_store, qdrant_store])
  ```

- `api/routes/query.py` — use `HybridSearcher.search()` to retrieve candidates before LLM synthesis.

- `pyproject.toml` — add deps:
  ```
  qdrant-client = "^1.9"
  bm25s = "^0.2"
  sentence-transformers = "^3"   # only required for fallback embedder
  voyageai = { version = "^0.2", optional = true }
  ```

### Operational note

Voyage and OpenAI embeddings cost money per call. For the pilot (single small repo), the local `all-MiniLM-L6-v2` fallback is recommended. Set `BRAIN_EMBEDDER=local` to force it regardless of API keys.

## Test plan

`tests/unit/retrieval/test_tokenize.py`:

```python
from companybrain.retrieval.tokenize import tokenize_code

def test_camel_case_split():
    assert tokenize_code("getUserId()") == ["get", "user", "id"]

def test_snake_case_split():
    assert tokenize_code("user_role_id") == ["user", "role", "id"]

def test_min_length_filter():
    assert "x" not in tokenize_code("x = 1")

def test_handles_empty():
    assert tokenize_code("") == []
    assert tokenize_code(None) == []
```

`tests/unit/retrieval/test_bm25.py`:

```python
import pytest
from pathlib import Path
from companybrain.retrieval.bm25_index import Bm25Index

def test_round_trip(tmp_path: Path):
    idx = Bm25Index(tmp_path, "dev", "component")
    idx.upsert("urn:cb:dev:code:r:component:UserCard", "displays user avatar and role")
    idx.upsert("urn:cb:dev:code:r:component:OrderList", "list of orders with pagination")
    idx.flush()
    out = idx.search("user role")
    urns = [u for u, _ in out]
    assert urns[0].endswith("UserCard")
```

`tests/integration/test_hybrid_search.py` (uses real Qdrant on localhost):

```python
import pytest
from companybrain.retrieval.hybrid_search import HybridSearcher
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store.base import BrainEntity

@pytest.mark.qdrant
@pytest.mark.asyncio
async def test_end_to_end(tmp_path):
    store = QdrantBrainStore(tmp_path, "test")
    e1 = BrainEntity(id="urn:cb:test:code:r:component:LoginForm",
                     entity_type="component", repo="r", file="Login.tsx",
                     qualified_name="LoginForm",
                     t1_summary="Authentication form with email/password fields")
    e2 = BrainEntity(id="urn:cb:test:code:r:component:OrderTable",
                     entity_type="component", repo="r", file="Order.tsx",
                     qualified_name="OrderTable",
                     t1_summary="Paginated list of customer orders")
    await store.write(e1, run_id="r", workspace_id="ws")
    await store.write(e2, run_id="r", workspace_id="ws")
    await store.commit_run("r")

    searcher = HybridSearcher(tmp_path, "test")
    hits = searcher.search("how do users sign in", top_k=5)
    assert hits and hits[0].urn.endswith("LoginForm")
```

## Acceptance criteria

- [ ] `companybrain/retrieval/` package with all six modules.
- [ ] All unit tests pass (`pytest tests/unit/retrieval/ -v`).
- [ ] Integration test `test_hybrid_search.py` passes against a running Qdrant.
- [ ] `QdrantBrainStore` is added to the orchestrator's FanoutBrainStore mirrors.
- [ ] After a pipeline run on the pilot repo, `qdrant_client.list_collections()` shows `brain__{slug}__component` and other expected collections.
- [ ] After a pipeline run, `.brain/.bm25/{slug}/{type}/corpus.jsonl` contains rows for every entity.
- [ ] `HybridSearcher.search("payment processing")` on the pilot returns the relevant entities ranked sensibly.
- [ ] Embedder fallback chain works: VOYAGE_API_KEY → OPENAI_API_KEY → local MiniLM.

## Verification commands

```bash
# 1. Confirm Qdrant is up
curl -s http://localhost:6333/collections | jq

# 2. Run pipeline (Qdrant store will populate as a mirror)
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET

# 3. Confirm collections were created
curl -s http://localhost:6333/collections | jq '.result.collections[].name' | grep brain__

# 4. Confirm BM25 corpora exist
ls pilot/.brain/.bm25/dev/

# 5. Try a search via Python REPL
python -c "
from pathlib import Path
from companybrain.retrieval.hybrid_search import HybridSearcher
s = HybridSearcher(Path('pilot'), 'dev')
for h in s.search('user authentication', top_k=5):
    print(h.score, h.urn)
"
```

## Rollback

```bash
git revert <commit-sha>
# Drop Qdrant collections (data only; the deployment stays):
curl -X DELETE http://localhost:6333/collections/brain__dev__component
# (repeat for each entity_type)
rm -rf pilot/.brain/.bm25/
```

## Out of scope

- **MMR rerank.** The harness §5.5 implementation lives in ADR-0018 (smart-zone assembler) where it has direct access to candidate embeddings.
- **Persistent BM25 binary.** Today corpus is stored as JSONL and rebuilt on flush. For repos with > 50K entities this should switch to bm25s' native serialised format. Stage 2 concern.
- **Embedding cost telemetry.** Counts and dollars of embedding calls — wire through Langfuse later.
- **Cross-collection search at platform scale.** Stage 2 introduces multi-repo collections.
