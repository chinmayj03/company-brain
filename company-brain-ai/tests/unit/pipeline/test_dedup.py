"""Unit tests for companybrain.pipeline._dedup — ADR-0043 WS1.S4."""
import pytest
from companybrain.pipeline._dedup import dedup_relationships_by_confidence
from companybrain.models.entities import ExtractedRelationship


def _rel(from_e: str, edge: str, to_e: str,
         confidence: float = 0.8, evidence: str = "") -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=from_e,
        from_type="Function",
        edge_type=edge,
        to_entity=to_e,
        to_type="Function",
        confidence=confidence,
        evidence=evidence,
    )


# ── Basic correctness ─────────────────────────────────────────────────────────

def test_empty_input():
    assert dedup_relationships_by_confidence([]) == []


def test_single_rel_passes_through():
    rel = _rel("A", "CALLS", "B", confidence=0.9, evidence="a.call(b)")
    result = dedup_relationships_by_confidence([rel])
    assert len(result) == 1
    assert result[0] is rel


def test_distinct_triples_all_preserved():
    rels = [
        _rel("A", "CALLS", "B"),
        _rel("A", "CALLS", "C"),
        _rel("B", "READS_COLUMN", "id_col"),
    ]
    result = dedup_relationships_by_confidence(rels)
    assert len(result) == 3


# ── Confidence-weighted dedup ─────────────────────────────────────────────────

def test_higher_confidence_wins():
    low  = _rel("A", "CALLS", "B", confidence=0.6, evidence="weak hint")
    high = _rel("A", "CALLS", "B", confidence=0.9, evidence="strong call")
    result = dedup_relationships_by_confidence([low, high])
    assert len(result) == 1
    assert result[0].confidence == 0.9


def test_higher_confidence_wins_regardless_of_arrival_order():
    high = _rel("A", "CALLS", "B", confidence=0.9, evidence="definitive")
    low  = _rel("A", "CALLS", "B", confidence=0.5, evidence="maybe")
    # High arrives first — should still win.
    result = dedup_relationships_by_confidence([high, low])
    assert len(result) == 1
    assert result[0].confidence == 0.9


# ── Tie-breaking by evidence length ──────────────────────────────────────────

def test_equal_confidence_longer_evidence_wins():
    short = _rel("A", "CALLS", "B", confidence=0.8, evidence="call()")
    long_ = _rel("A", "CALLS", "B", confidence=0.8, evidence="paymentService.call(param)")
    result = dedup_relationships_by_confidence([short, long_])
    assert len(result) == 1
    assert "paymentService" in result[0].evidence


def test_equal_confidence_equal_evidence_keeps_first():
    r1 = _rel("A", "CALLS", "B", confidence=0.7, evidence="same")
    r2 = _rel("A", "CALLS", "B", confidence=0.7, evidence="same")
    result = dedup_relationships_by_confidence([r1, r2])
    assert len(result) == 1


def test_none_evidence_treated_as_empty():
    r_none = ExtractedRelationship(
        from_entity="A", from_type="F", edge_type="CALLS",
        to_entity="B", to_type="F", confidence=0.8, evidence=None,  # type: ignore[arg-type]
    )
    r_text = _rel("A", "CALLS", "B", confidence=0.8, evidence="call site found here")
    result = dedup_relationships_by_confidence([r_none, r_text])
    assert len(result) == 1
    assert result[0].evidence == "call site found here"


# ── Mixed scenarios ───────────────────────────────────────────────────────────

def test_mixed_duplicates_and_distinct():
    rels = [
        _rel("A", "CALLS", "B", confidence=0.5, evidence="weak"),
        _rel("A", "CALLS", "B", confidence=0.9, evidence="strong"),
        _rel("C", "READS_COLUMN", "D", confidence=0.7, evidence="col"),
        _rel("A", "CALLS", "B", confidence=0.8, evidence="medium"),
    ]
    result = dedup_relationships_by_confidence(rels)
    assert len(result) == 2
    ab = next(r for r in result if r.from_entity == "A" and r.to_entity == "B")
    assert ab.confidence == 0.9


def test_edge_type_distinguishes_triples():
    calls = _rel("A", "CALLS", "B", confidence=0.9)
    uses  = _rel("A", "USES",  "B", confidence=0.5)
    result = dedup_relationships_by_confidence([calls, uses])
    assert len(result) == 2
