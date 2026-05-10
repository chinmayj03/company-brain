"""
ADR-0044 PR-0044-5: Merger tests.
"""
from __future__ import annotations

import pytest

from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge
from companybrain.pipeline.merger import (
    merge_chunk_entities,
    resolve_edges,
    dedup_edges,
)


def _entity(qname: str, confidence: float = 0.9, **kwargs) -> ExtractedChunkEntity:
    defaults = dict(
        entity_type="Function",
        name=qname,
        qname=qname,
        file_path="Foo.java",
        signature="",
        query_text="",
        code_snippet="",
        language="java",
    )
    defaults.update(kwargs)
    return ExtractedChunkEntity(confidence=confidence, **defaults)


def _edge(edge_type: str, target: str, confidence: float = 0.9) -> ExtractedEdge:
    return ExtractedEdge(edge_type=edge_type, target=target, confidence=confidence)


# ── merge_chunk_entities ──────────────────────────────────────────────────────

def test_merge_no_duplicates():
    entities = [_entity("Foo.a"), _entity("Foo.b"), _entity("Foo.c")]
    merged = merge_chunk_entities(entities)
    assert len(merged) == 3


def test_merge_duplicate_qname_higher_confidence_wins():
    low = _entity("Foo.bar", confidence=0.7, code_snippet="snippet-low")
    high = _entity("Foo.bar", confidence=0.95, code_snippet="snippet-high")
    merged = merge_chunk_entities([low, high])
    assert len(merged) == 1
    assert merged[0].confidence == 0.95
    assert merged[0].code_snippet == "snippet-high"


def test_merge_borrows_missing_query_text_from_loser():
    winner = _entity("Repo.find", confidence=0.95, query_text="")
    loser  = _entity("Repo.find", confidence=0.7, query_text="SELECT * FROM users")
    merged = merge_chunk_entities([winner, loser])
    assert len(merged) == 1
    assert merged[0].query_text == "SELECT * FROM users"


def test_merge_does_not_overwrite_winners_query_text():
    winner = _entity("Repo.find", confidence=0.95, query_text="SELECT a FROM t")
    loser  = _entity("Repo.find", confidence=0.7,  query_text="SELECT b FROM t")
    merged = merge_chunk_entities([winner, loser])
    assert merged[0].query_text == "SELECT a FROM t"


def test_merge_longer_query_text_wins_on_equal_confidence():
    e1 = _entity("Repo.find", confidence=0.9, query_text="SELECT id FROM users")
    e2 = _entity("Repo.find", confidence=0.9, query_text="SELECT id, email, name FROM users WHERE active = true")
    merged = merge_chunk_entities([e1, e2])
    assert "email" in merged[0].query_text


def test_merge_is_idempotent():
    entities = [
        _entity("Foo.a", confidence=0.9),
        _entity("Foo.a", confidence=0.7),
        _entity("Foo.b", confidence=0.8),
    ]
    once = merge_chunk_entities(entities)
    twice = merge_chunk_entities(once)
    assert len(once) == len(twice) == 2
    for o, t in zip(sorted(once, key=lambda e: e.qname),
                    sorted(twice, key=lambda e: e.qname)):
        assert o.qname == t.qname
        assert o.confidence == t.confidence


def test_merge_empty_list():
    assert merge_chunk_entities([]) == []


# ── resolve_edges ─────────────────────────────────────────────────────────────

def test_resolve_edges_replaces_known_targets():
    edges = [
        _edge("CALLS", "fetchAllCompetitors"),
        _edge("READS_COLUMN", "plan_info.lob"),
    ]
    name_to_urn = {
        "fetchAllCompetitors": "urn:cb:repo:java:function:fetchAllCompetitors",
    }
    resolved = resolve_edges(edges, name_to_urn)
    assert resolved[0].target == "urn:cb:repo:java:function:fetchAllCompetitors"
    assert resolved[1].target == "plan_info.lob"  # unchanged


def test_resolve_edges_unknown_target_unchanged():
    edges = [_edge("CALLS", "UnknownClass.method")]
    resolved = resolve_edges(edges, {})
    assert resolved[0].target == "UnknownClass.method"


def test_resolve_edges_preserves_edge_type_and_confidence():
    edges = [_edge("READS_COLUMN", "orders.status", confidence=0.85)]
    resolved = resolve_edges(edges, {"orders.status": "urn:orders:status"})
    assert resolved[0].edge_type == "READS_COLUMN"
    assert resolved[0].confidence == 0.85
    assert resolved[0].target == "urn:orders:status"


def test_resolve_edges_idempotent():
    urn = "urn:cb:repo:java:function:findByLob"
    edges = [_edge("CALLS", urn)]
    name_to_urn = {urn: "other-urn"}  # URN itself is not in index normally
    once = resolve_edges(edges, {})
    twice = resolve_edges(once, {})
    assert once[0].target == twice[0].target


# ── dedup_edges ───────────────────────────────────────────────────────────────

def test_dedup_edges_removes_exact_duplicates():
    edges = [
        _edge("CALLS", "Foo.bar"),
        _edge("CALLS", "Foo.bar"),  # exact duplicate
        _edge("READS_COLUMN", "t.col"),
    ]
    deduped = dedup_edges(edges)
    assert len(deduped) == 2


def test_dedup_edges_different_edge_type_kept():
    edges = [
        _edge("CALLS", "Foo.bar"),
        _edge("DELEGATES_TO", "Foo.bar"),  # different type, same target
    ]
    deduped = dedup_edges(edges)
    assert len(deduped) == 2


def test_dedup_edges_first_wins():
    edges = [
        _edge("CALLS", "Foo.bar", confidence=0.95),
        _edge("CALLS", "Foo.bar", confidence=0.5),
    ]
    deduped = dedup_edges(edges)
    assert len(deduped) == 1
    assert deduped[0].confidence == 0.95
