"""Integration test for HybridSearcher + QdrantBrainStore.

Requires a running Qdrant instance (localhost:6333) to run the @pytest.mark.qdrant
tests. All other tests are unit-level and run without any external services.
"""
import pytest
from pathlib import Path

from companybrain.retrieval.hybrid_search import HybridSearcher, SearchHit
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store.base import BrainEntity


def _qdrant_available() -> bool:
    try:
        from qdrant_client import QdrantClient
        c = QdrantClient(url="http://localhost:6333", timeout=2)
        c.get_collections()
        return True
    except Exception:
        return False


requires_qdrant = pytest.mark.skipif(
    not _qdrant_available(),
    reason="Qdrant not running on localhost:6333",
)


@requires_qdrant
@pytest.mark.asyncio
async def test_end_to_end(tmp_path: Path):
    store = QdrantBrainStore(tmp_path, "test_e2e")
    e1 = BrainEntity(
        id="urn:cb:test_e2e:code:r:component:LoginForm",
        entity_type="component",
        repo="r",
        file="Login.tsx",
        qualified_name="LoginForm",
        t1_summary="Authentication form with email/password fields",
    )
    e2 = BrainEntity(
        id="urn:cb:test_e2e:code:r:component:OrderTable",
        entity_type="component",
        repo="r",
        file="Order.tsx",
        qualified_name="OrderTable",
        t1_summary="Paginated list of customer orders",
    )
    await store.write(e1, run_id="r", workspace_id="ws")
    await store.write(e2, run_id="r", workspace_id="ws")
    await store.commit_run("r")

    searcher = HybridSearcher(tmp_path, "test_e2e")
    hits = searcher.search("how do users sign in", top_k=5)
    assert hits, "Expected at least one search result"
    assert hits[0].urn.endswith("LoginForm"), (
        f"Expected LoginForm ranked first, got {hits[0].urn}"
    )


@requires_qdrant
@pytest.mark.asyncio
async def test_commit_creates_bm25_corpus(tmp_path: Path):
    store = QdrantBrainStore(tmp_path, "test_bm25")
    e = BrainEntity(
        id="urn:cb:test_bm25:code:r:api_contract:CreateOrder",
        entity_type="api_contract",
        repo="r",
        file="orders.py",
        qualified_name="CreateOrder",
        t1_summary="POST endpoint to create a new order",
    )
    await store.write(e, run_id="run1", workspace_id="ws")
    await store.commit_run("run1")

    corpus_path = (
        tmp_path / ".brain" / ".bm25" / "test_bm25" / "api_contract" / "corpus.jsonl"
    )
    assert corpus_path.exists(), f"BM25 corpus file not created at {corpus_path}"
    import json
    rows = [json.loads(line) for line in corpus_path.read_text().splitlines() if line.strip()]
    assert any(r["id"] == e.id for r in rows), "Entity URN not found in corpus"


@requires_qdrant
@pytest.mark.asyncio
async def test_search_payment_processing(tmp_path: Path):
    store = QdrantBrainStore(tmp_path, "test_payment")
    entities = [
        BrainEntity(
            id="urn:cb:test_payment:code:r:component:PaymentService",
            entity_type="component",
            repo="r", file="PaymentService.java",
            qualified_name="PaymentService",
            t1_summary="Handles charge, refund, and payment method management",
        ),
        BrainEntity(
            id="urn:cb:test_payment:code:r:component:UserProfile",
            entity_type="component",
            repo="r", file="UserProfile.tsx",
            qualified_name="UserProfile",
            t1_summary="Displays user name, avatar, and account settings",
        ),
    ]
    for e in entities:
        await store.write(e, run_id="r2", workspace_id="ws")
    await store.commit_run("r2")

    searcher = HybridSearcher(tmp_path, "test_payment")
    hits = searcher.search("payment processing", top_k=5)
    assert hits, "Expected results for 'payment processing'"
    assert hits[0].urn.endswith("PaymentService"), (
        f"Expected PaymentService ranked first, got {hits[0].urn}"
    )


def test_search_hit_fields():
    hit = SearchHit(urn="urn:cb:dev:code:r:component:Foo", score=0.5)
    assert hit.urn.endswith("Foo")
    assert hit.bm25_rank is None
    assert hit.dense_rank is None
    assert hit.payload == {}
