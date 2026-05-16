"""Unit tests for ADR-0055 cross-cutting pass focusing on SharedInvariant
creation from a constant pattern (the primary assertion from the task spec).

Covers:
  - SharedInvariant emitted when LLM identifies a cross-method constant usage
  - Deterministic constant-pattern -> Pattern + IMPLEMENTS/VIOLATES edges
  - run_cross_file_pass wires SP-3 results when LLM passes are enabled
  - AntiPattern entity emitted for constant-usage violator
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.models.entities import (
    EDGE_IMPLEMENTS_PATTERN,
    EDGE_SHARES_INVARIANT,
    EDGE_VIOLATES_PATTERN,
    ExtractedEntity,
    ExtractedRelationship,
    SharedInvariant,
)
from companybrain.pipeline.antipattern_detector import ConventionSite, detect_antipatterns
from companybrain.pipeline.cross_file_pass import run_cross_file_pass
from companybrain.pipeline.invariant_inferrer import (
    InvariantInferenceResult,
    infer_shared_invariants,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _entity(name: str, class_prefix: str = "com.example.Service") -> ExtractedEntity:
    # All methods in the same class share the same file so the window builder
    # groups them together (it keys on repo/file::class-prefix).
    class_file = f"src/{class_prefix.replace('.', '/')}.java"
    return ExtractedEntity(
        entity_type="Function",
        name=f"{class_prefix}.{name}",
        file=class_file,
        repo="repo",
        signature=f"public void {name}()",
        last_modified_commit="abc123",
        confidence=0.9,
    )


# ── SharedInvariant creation from constant pattern (LLM mocked) ───────────────

@pytest.mark.asyncio
async def test_shared_invariant_created_for_constant_pattern():
    """Core acceptance assertion: when multiple methods all use the same constant
    (e.g. JsonKeyMapping.LOB), the invariant inferrer emits a SharedInvariant
    whose statement records that fact.

    The LLM response is mocked so the test is deterministic and free.
    """
    methods = [_entity(f"getLob{i}", "com.example.LobService") for i in range(5)]

    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(return_value=json.dumps({
        "invariants": [{
            "name": "use_JsonKeyMapping_LOB_constant",
            "statement": "all methods reference JsonKeyMapping.LOB for the lob JSON key",
            "affected_qnames": [e.name for e in methods],
        }]
    }))

    with patch("companybrain.pipeline.invariant_inferrer.get_provider",
               return_value=mock_provider):
        result = await infer_shared_invariants(methods, min_window=3)

    assert len(result.invariants) == 1
    inv = result.invariants[0]
    assert isinstance(inv, SharedInvariant)
    assert "LOB" in inv.statement or "lob" in inv.statement.lower()
    assert len(inv.affected_method_urns) == len(methods)
    # SHARES_INVARIANT edges, one per affected method
    assert len(result.edges) == len(methods)
    assert all(e.edge_type == EDGE_SHARES_INVARIANT for e in result.edges)


@pytest.mark.asyncio
async def test_shared_invariant_not_created_below_min_window():
    """When the LLM returns fewer affected methods than min_window, the
    invariant is silently dropped (too weak to trust)."""
    methods = [_entity(f"readPlan{i}", "com.example.PlanRepo") for i in range(5)]

    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(return_value=json.dumps({
        "invariants": [{
            "name": "sparse_invariant",
            "statement": "only 2 methods filter is_active=true",
            "affected_qnames": [methods[0].name, methods[1].name],
        }]
    }))

    with patch("companybrain.pipeline.invariant_inferrer.get_provider",
               return_value=mock_provider):
        result = await infer_shared_invariants(methods, min_window=3)

    # 2 affected < min_window=3 → dropped
    assert result.invariants == []
    assert result.edges == []


@pytest.mark.asyncio
async def test_shared_invariant_llm_failure_is_graceful():
    """If the LLM call raises, the inferrer returns empty results without
    propagating the exception."""
    methods = [_entity(f"doWork{i}") for i in range(4)]

    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(side_effect=RuntimeError("provider timeout"))

    with patch("companybrain.pipeline.invariant_inferrer.get_provider",
               return_value=mock_provider):
        result = await infer_shared_invariants(methods, min_window=2)

    assert result.invariants == []
    assert result.edges == []


# ── Deterministic constant-pattern: AntiPattern detection ─────────────────────

def test_constant_pattern_emits_antipattern_for_literal_user():
    """16 DTOs use JsonKeyMapping.LOB (constant); 1 uses inline "lob" (literal).
    The detector must emit a Pattern + 1 VIOLATES_PATTERN edge."""
    sites = [
        ConventionSite(
            entity_external_id=f"Dto{i}::Dto{i}",
            field_key="lob",
            uses_constant=True,
            constant_name="JsonKeyMapping.LOB",
        )
        for i in range(16)
    ] + [
        ConventionSite(
            entity_external_id="CompetitivenessReportRequestDTO::CompetitivenessReportRequestDTO",
            field_key="lob",
            uses_constant=False,
        )
    ]

    result = detect_antipatterns(
        convention_sites=sites,
        min_strength=0.80,
        min_population=5,
    )

    assert len(result.patterns) == 1
    pattern = result.patterns[0]
    assert pattern.instance_count == 16
    assert "LOB" in pattern.name or "lob" in pattern.name.lower()

    impls = [e for e in result.edges if e.edge_type == EDGE_IMPLEMENTS_PATTERN]
    viols = [e for e in result.edges if e.edge_type == EDGE_VIOLATES_PATTERN]
    assert len(impls) == 16
    assert len(viols) == 1
    assert "Competitiveness" in viols[0].from_entity


def test_constant_pattern_below_population_threshold_is_ignored():
    """If there are fewer than min_population sites, no pattern is emitted."""
    sites = [
        ConventionSite(entity_external_id=f"Dto{i}", field_key="plan",
                       uses_constant=True, constant_name="Keys.PLAN")
        for i in range(3)
    ]
    result = detect_antipatterns(convention_sites=sites,
                                 min_strength=0.80, min_population=5)
    assert result.patterns == []
    assert result.edges == []


def test_constant_pattern_all_consistent_no_violation():
    """When everyone uses the constant (no literal users), there should be
    a Pattern + IMPLEMENTS edges but zero VIOLATES edges."""
    sites = [
        ConventionSite(entity_external_id=f"Dto{i}", field_key="status",
                       uses_constant=True, constant_name="StatusCodes.ACTIVE")
        for i in range(10)
    ]
    result = detect_antipatterns(convention_sites=sites,
                                 min_strength=0.80, min_population=5)
    viols = [e for e in result.edges if e.edge_type == EDGE_VIOLATES_PATTERN]
    assert viols == []


# ── Integration: run_cross_file_pass with mocked LLM emits SharedInvariant ───

@pytest.mark.asyncio
async def test_run_cross_file_pass_with_llm_emits_shared_invariant():
    """End-to-end through run_cross_file_pass: with the LLM mocked to return
    a constant-pattern SharedInvariant, the orchestrator includes it in results."""
    methods = [_entity(f"readPlan{i}", "com.example.PlanService") for i in range(5)]
    rels: list[ExtractedRelationship] = []

    mock_invariant_result = InvariantInferenceResult(
        invariants=[SharedInvariant(
            name="use_JsonKeyMapping_PLAN",
            statement="all 5 reads reference JsonKeyMapping.PLAN constant",
            affected_method_urns=[e.name for e in methods],
            evidence_method_urns=[e.name for e in methods],
            confidence=0.85,
        )],
        edges=[
            ExtractedRelationship(
                from_entity=e.name, from_type="Function",
                edge_type=EDGE_SHARES_INVARIANT,
                to_entity="invariant::use_JsonKeyMapping_PLAN", to_type="SharedInvariant",
                confidence=0.85,
                evidence="window-inferred invariant",
            )
            for e in methods
        ],
    )

    with patch("companybrain.pipeline.cross_file_pass.infer_shared_invariants",
               new=AsyncMock(return_value=mock_invariant_result)), \
         patch("companybrain.pipeline.cross_file_pass.infer_implicit_contracts",
               new=AsyncMock(return_value=InvariantInferenceResult())), \
         patch("companybrain.pipeline.cross_file_pass.infer_domain_entities",
               new=AsyncMock(return_value=MagicMock(domains=[], edges=[]))):

        result = await run_cross_file_pass(
            methods, rels, enable_llm_passes=True
        )

    assert len(result.shared_invariants) == 1
    inv = result.shared_invariants[0]
    assert "PLAN" in inv.statement or "plan" in inv.statement.lower()
    shares_inv_edges = [e for e in result.new_edges if e.edge_type == EDGE_SHARES_INVARIANT]
    assert len(shares_inv_edges) == len(methods)
