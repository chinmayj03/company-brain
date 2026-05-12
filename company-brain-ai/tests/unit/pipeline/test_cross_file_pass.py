"""Unit tests for ADR-0055 cross-file cross-cutting pass.

Covers SP-1 (idiom detection) and SP-2 (anti-pattern detection) end to end
without touching the LLM. SP-3/4/5 are exercised through the orchestrator
with the LLM passes disabled — they should produce empty results and not
raise. The acceptance test covers SP-2 in the lob-anti-pattern scenario.
"""
from __future__ import annotations

import pytest

from companybrain.models.entities import (
    EDGE_IMPLEMENTS_PATTERN,
    EDGE_VIOLATES_PATTERN,
    ExtractedEntity,
    ExtractedRelationship,
)
from companybrain.pipeline.antipattern_detector import (
    ConventionSite,
    detect_antipatterns,
)
from companybrain.pipeline.cross_file_pass import run_cross_file_pass
from companybrain.pipeline.idiom_detector import detect_idioms


def _rel(from_e: str, edge: str, to_e: str,
         confidence: float = 0.8, evidence: str = "") -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=from_e, from_type="Function",
        edge_type=edge,
        to_entity=to_e,   to_type="Function",
        confidence=confidence,
        evidence=evidence,
    )


# ── SP-1: idiom_detector ──────────────────────────────────────────────────────

def test_sp1_empty_input_returns_empty_result():
    result = detect_idioms([])
    assert result.patterns == []
    assert result.edges == []


def test_sp1_below_threshold_yields_no_patterns():
    rels = [_rel(f"caller{i}", "CALLS", "shared.callee") for i in range(3)]
    result = detect_idioms(rels, min_instances=5)
    assert result.patterns == []


def test_sp1_shared_callee_emits_pattern_and_edges():
    """13 callers calling the same callee should become one Pattern."""
    rels = [
        _rel(f"file{i}.caller", "CALLS", "common.defensiveCopy")
        for i in range(13)
    ]
    result = detect_idioms(rels, min_instances=5)
    assert len(result.patterns) == 1
    pattern = result.patterns[0]
    assert pattern.instance_count == 13
    assert pattern.confidence > 0.6
    assert pattern.inferred_from == "deterministic"
    # one IMPLEMENTS_PATTERN edge per caller
    assert len(result.edges) == 13
    assert all(e.edge_type == EDGE_IMPLEMENTS_PATTERN for e in result.edges)
    assert {e.from_entity for e in result.edges} == {f"file{i}.caller" for i in range(13)}


def test_sp1_shared_filter_idiom_extracts_literal():
    """Multiple READS_COLUMN edges that filter the same literal → Pattern."""
    rels = [
        ExtractedRelationship(
            from_entity=f"plan_reader{i}", from_type="Function",
            edge_type="READS_COLUMN",
            to_entity="plan_info.is_current", to_type="DatabaseColumn",
            confidence=0.9,
            evidence=f"WHERE plan_info.is_current = true (line {i})",
        )
        for i in range(6)
    ]
    result = detect_idioms(rels, min_instances=5)
    assert len(result.patterns) == 1
    p = result.patterns[0]
    assert "is_current" in p.name
    assert "true" in p.name
    assert p.instance_count == 6


def test_sp1_unrelated_callees_do_not_collapse():
    """Distinct callees stay distinct even when each individually crosses the threshold."""
    rels = (
        [_rel(f"a{i}", "CALLS", "callee.A") for i in range(6)]
        + [_rel(f"b{i}", "CALLS", "callee.B") for i in range(6)]
    )
    result = detect_idioms(rels, min_instances=5)
    assert len(result.patterns) == 2
    names = {p.name for p in result.patterns}
    assert any("A" in n for n in names)
    assert any("B" in n for n in names)


# ── SP-2: antipattern_detector ────────────────────────────────────────────────

def test_sp2_empty_inputs_returns_empty_result():
    result = detect_antipatterns()
    assert result.patterns == []
    assert result.edges == []


def test_sp2_strength_flip_flags_minority():
    """If 9 of 10 entities implement a pattern, the 10th is a violator."""
    from companybrain.models.entities import Pattern as _P

    pattern = _P(
        name="defensive_copy", description="Copy then mutate.",
        instance_count=9, confidence=0.85, inferred_from="deterministic",
        instance_urns=[f"file{i}" for i in range(9)],
    )
    universe = {pattern.external_id: [f"file{i}" for i in range(10)]}
    result = detect_antipatterns(
        patterns=[pattern], candidate_universe=universe,
        min_strength=0.80, min_population=5,
    )
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.edge_type == EDGE_VIOLATES_PATTERN
    assert edge.from_entity == "file9"
    assert edge.to_entity == pattern.external_id


def test_sp2_strength_below_threshold_does_not_flag():
    from companybrain.models.entities import Pattern as _P

    pattern = _P(
        name="weak_pattern", description="x", instance_count=5,
        confidence=0.5, inferred_from="deterministic",
        instance_urns=[f"file{i}" for i in range(5)],
    )
    universe = {pattern.external_id: [f"file{i}" for i in range(10)]}
    result = detect_antipatterns(
        patterns=[pattern], candidate_universe=universe,
        min_strength=0.80, min_population=5,
    )
    assert result.edges == []


def test_sp2_convention_site_lob_pattern():
    """Acceptance-style smoke: 16 DTOs use the constant, 1 uses the literal."""
    sites = [
        ConventionSite(
            entity_external_id=f"java/Dto{i}.java::Dto{i}",
            field_key="lob",
            uses_constant=True,
            constant_name="JsonKeyMapping.LOB",
        )
        for i in range(16)
    ] + [
        ConventionSite(
            entity_external_id="java/CompetitivenessReportRequestDTO.java::CompetitivenessReportRequestDTO",
            field_key="lob",
            uses_constant=False,
        )
    ]
    result = detect_antipatterns(
        convention_sites=sites,
        min_strength=0.80,
        min_population=5,
    )
    # one Pattern + 16 implements + 1 violates
    assert len(result.patterns) == 1
    pattern = result.patterns[0]
    assert pattern.instance_count == 16
    assert "LOB" in pattern.name or "lob" in pattern.name.lower()

    impls   = [e for e in result.edges if e.edge_type == EDGE_IMPLEMENTS_PATTERN]
    viols   = [e for e in result.edges if e.edge_type == EDGE_VIOLATES_PATTERN]
    assert len(impls) == 16
    assert len(viols) == 1
    assert "Competitiveness" in viols[0].from_entity


def test_sp2_convention_site_below_population_skipped():
    sites = [
        ConventionSite(entity_external_id=f"x{i}", field_key="k",
                       uses_constant=True, constant_name="C")
        for i in range(3)
    ]
    result = detect_antipatterns(convention_sites=sites,
                                 min_strength=0.80, min_population=5)
    assert result.patterns == []
    assert result.edges == []


# ── orchestrator (SP-1 + SP-2 integration; LLM passes disabled) ────────────────

@pytest.mark.asyncio
async def test_run_cross_file_pass_no_llm_combines_sp1_and_sp2():
    """SP-1 finds the shared-callee idiom; SP-2 finds the lob convention violator."""
    rels = [_rel(f"caller{i}", "CALLS", "common.defensiveCopy") for i in range(7)]
    sites = [
        ConventionSite(entity_external_id=f"Dto{i}", field_key="lob",
                       uses_constant=True, constant_name="JsonKeyMapping.LOB")
        for i in range(8)
    ] + [
        ConventionSite(entity_external_id="OddDTO", field_key="lob",
                       uses_constant=False),
    ]
    entities: list[ExtractedEntity] = []   # SP-3/4/5 skipped via flag
    result = await run_cross_file_pass(
        entities, rels,
        convention_sites=sites,
        enable_llm_passes=False,
    )
    assert result.summary["patterns"] >= 2          # one from SP-1, one from SP-2
    assert result.summary["shared_invariants"] == 0
    assert result.summary["domain_entities"] == 0
    assert any(e.edge_type == EDGE_VIOLATES_PATTERN for e in result.new_edges)


@pytest.mark.asyncio
async def test_run_cross_file_pass_handles_empty_input():
    result = await run_cross_file_pass([], [], enable_llm_passes=False)
    assert result.summary == {
        "patterns": 0, "shared_invariants": 0, "domain_entities": 0,
        "implicit_contracts": 0, "new_edges": 0,
    }


# ── taxonomy registration ─────────────────────────────────────────────────────

def test_new_edges_are_in_canonical_taxonomy():
    """ADR-0055 added 5 edges; they must be valid per edges.taxonomy SOT."""
    from companybrain.edges.taxonomy import is_valid_edge
    for et in (
        "IMPLEMENTS_PATTERN", "VIOLATES_PATTERN",
        "SHARES_INVARIANT", "REPRESENTS", "HAS_IMPLICIT_CONTRACT",
    ):
        assert is_valid_edge(et), f"{et} missing from EDGE_TYPES"
