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
    assert urns[0].endswith("UserCard"), f"Expected UserCard first, got {urns}"


def test_corpus_persisted(tmp_path: Path):
    idx = Bm25Index(tmp_path, "dev", "component")
    idx.upsert("urn:cb:dev:code:r:component:Auth", "authentication login password")
    idx.flush()
    assert (tmp_path / ".brain" / ".bm25" / "dev" / "component" / "corpus.jsonl").exists()


def test_upsert_replaces_existing(tmp_path: Path):
    idx = Bm25Index(tmp_path, "dev", "api_contract")
    idx.upsert("urn:id:1", "payment processing charge")
    idx.flush()
    idx2 = Bm25Index(tmp_path, "dev", "api_contract")
    idx2.upsert("urn:id:1", "refund processing return")
    idx2.flush()
    out = idx2.search("refund")
    assert any("id:1" in u for u, _ in out)


def test_search_empty_index_returns_empty(tmp_path: Path):
    idx = Bm25Index(tmp_path, "dev", "screen")
    result = idx.search("anything")
    assert result == []


def test_search_empty_query_returns_empty(tmp_path: Path):
    idx = Bm25Index(tmp_path, "dev", "data_model")
    idx.upsert("urn:1", "order table schema")
    idx.flush()
    assert idx.search("") == []


def test_multiple_entity_types_isolated(tmp_path: Path):
    c_idx = Bm25Index(tmp_path, "dev", "component")
    a_idx = Bm25Index(tmp_path, "dev", "api_contract")
    c_idx.upsert("urn:c:1", "user interface button login")
    c_idx.flush()
    a_idx.upsert("urn:a:1", "login endpoint authentication")
    a_idx.flush()
    c_results = c_idx.search("login")
    a_results = a_idx.search("login")
    assert all("urn:c:" in u for u, _ in c_results)
    assert all("urn:a:" in u for u, _ in a_results)
