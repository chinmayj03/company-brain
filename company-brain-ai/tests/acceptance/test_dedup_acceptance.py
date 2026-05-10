"""
Acceptance tests: confidence-weighted dedup under realistic pipeline conditions.

Simulates a RelationshipExtractor output where both LLM-extracted and
pattern-distilled edges are merged, then verifies dedup contract properties
hold end-to-end.
"""
from __future__ import annotations

import pytest

from companybrain.models.entities import ExtractedRelationship

dedup_relationships_by_confidence = pytest.importorskip(
    "companybrain.pipeline._dedup",
    reason="Requires PR-0043-3 (storage layer) to be merged",
).dedup_relationships_by_confidence


def _make_rel(from_e, edge, to_e, conf, evidence=""):
    return ExtractedRelationship(
        from_entity=from_e, from_type="Function",
        edge_type=edge, to_entity=to_e, to_type="Function",
        confidence=conf, evidence=evidence,
    )


# ── Realistic merge scenario ──────────────────────────────────────────────────

def test_llm_high_conf_beats_distiller_low_conf():
    """LLM found a CALLS edge at 0.9; pattern distiller found same at 0.6.
    Dedup should keep the LLM version."""
    llm_edge = _make_rel("ChargeController", "CALLS", "PaymentService",
                         conf=0.9, evidence="paymentService.charge(request)")
    distiller_edge = _make_rel("ChargeController", "CALLS", "PaymentService",
                               conf=0.6, evidence="maybe calls payment")

    result = dedup_relationships_by_confidence([distiller_edge, llm_edge])
    assert len(result) == 1
    assert result[0].confidence == 0.9
    assert "paymentService.charge" in result[0].evidence


def test_independent_edges_all_preserved():
    rels = [
        _make_rel("A", "CALLS", "B", 0.9),
        _make_rel("A", "READS_COLUMN", "amount_cents", 0.85),
        _make_rel("B", "THROWS", "PaymentException", 0.8),
        _make_rel("B", "CALLS", "AuditService", 0.7),
    ]
    result = dedup_relationships_by_confidence(rels)
    assert len(result) == 4


def test_same_entities_different_edge_types_preserved():
    """CALLS and USES between same pair are different triples."""
    calls = _make_rel("A", "CALLS", "B", conf=0.9)
    uses  = _make_rel("A", "USES",  "B", conf=0.9)
    result = dedup_relationships_by_confidence([calls, uses])
    assert len(result) == 2


def test_large_batch_dedup_drops_duplicates():
    """50-edge batch with 10 unique triples, each appearing 5 times at varying confidence."""
    base_triples = [
        ("A", "CALLS", "B"),
        ("B", "READS_COLUMN", "col"),
        ("C", "THROWS", "ExA"),
        ("D", "CALLS_ENDPOINT", "E"),
        ("F", "VALIDATES", "field"),
        ("G", "AUTHORIZED_BY", "role"),
        ("H", "PUBLISHES_TO", "topic"),
        ("I", "USES", "J"),
        ("K", "AWAITS", "L"),
        ("M", "CACHED_BY", "redis"),
    ]
    rels = []
    for i, (f, e, t) in enumerate(base_triples):
        for variant in range(5):
            conf = 0.5 + variant * 0.1  # 0.5, 0.6, 0.7, 0.8, 0.9
            rels.append(_make_rel(f, e, t, conf=conf, evidence=f"ev-{variant}"))

    result = dedup_relationships_by_confidence(rels)
    assert len(result) == 10

    # Every surviving edge should have confidence 0.9 (the highest variant)
    for r in result:
        assert r.confidence == pytest.approx(0.9)


def test_dedup_is_idempotent():
    """Running dedup twice produces the same result."""
    rels = [
        _make_rel("A", "CALLS", "B", 0.8, "call(b)"),
        _make_rel("A", "CALLS", "B", 0.6, "maybe"),
        _make_rel("C", "READS_COLUMN", "D", 0.9, "SELECT d"),
    ]
    first  = dedup_relationships_by_confidence(rels)
    second = dedup_relationships_by_confidence(first)
    assert len(first) == len(second)
    for r1, r2 in zip(
        sorted(first,  key=lambda r: (r.from_entity, r.edge_type, r.to_entity)),
        sorted(second, key=lambda r: (r.from_entity, r.edge_type, r.to_entity)),
    ):
        assert r1.confidence == r2.confidence
        assert r1.evidence == r2.evidence
