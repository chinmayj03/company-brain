"""
Acceptance tests: edge taxonomy contract — ADR-0043 WS2.

These tests verify that the taxonomy module and its consumers are
internally consistent and correctly wire together. No live infrastructure
(LLM, Neo4j, Qdrant) is required.
"""
from __future__ import annotations

import pytest

from companybrain.edges.taxonomy import (
    EDGE_TYPES,
    EDGE_GROUPS,
    STRUCTURAL_EDGES,
    BEHAVIORAL_EDGES,
    is_valid_edge,
    validate_edge,
    render_prompt_reference,
)


# ── EDGE_TYPES integrity ──────────────────────────────────────────────────────

def test_edge_types_nonempty():
    assert len(EDGE_TYPES) >= 50


def test_all_edge_types_are_uppercase():
    for et in EDGE_TYPES:
        assert et == et.upper(), f"Edge type {et!r} is not all-uppercase"


def test_all_edge_types_are_strings():
    for et in EDGE_TYPES:
        assert isinstance(et, str)


# ── EDGE_GROUPS consistency ───────────────────────────────────────────────────

def test_groups_cover_all_edge_types():
    grouped = {e for edges in EDGE_GROUPS.values() for e in edges}
    missing = EDGE_TYPES - grouped
    assert not missing, f"Edge types missing from EDGE_GROUPS: {missing}"


def test_groups_contain_no_unknown_types():
    grouped = {e for edges in EDGE_GROUPS.values() for e in edges}
    extra = grouped - EDGE_TYPES
    assert not extra, f"EDGE_GROUPS contains unknown edge types: {extra}"


def test_no_duplicate_edges_across_groups():
    all_edges: list[str] = []
    for edges in EDGE_GROUPS.values():
        all_edges.extend(edges)
    assert len(all_edges) == len(set(all_edges)), "Duplicate edge types across groups"


# ── Sub-set consistency ───────────────────────────────────────────────────────

def test_structural_edges_are_subset_of_edge_types():
    assert STRUCTURAL_EDGES <= EDGE_TYPES


def test_behavioral_edges_are_subset_of_edge_types():
    assert BEHAVIORAL_EDGES <= EDGE_TYPES


def test_structural_and_behavioral_disjoint():
    overlap = STRUCTURAL_EDGES & BEHAVIORAL_EDGES
    assert not overlap, f"Structural and behavioral edges overlap: {overlap}"


# ── is_valid_edge / validate_edge ─────────────────────────────────────────────

def test_is_valid_edge_true_for_known():
    for et in ["CALLS", "READS_COLUMN", "THROWS", "AUTHORIZED_BY"]:
        assert is_valid_edge(et), f"Expected {et!r} to be valid"


def test_is_valid_edge_false_for_unknown():
    for bad in ["JUMPS", "calls", "CALL", "", "EXTENDS_INTERFACE"]:
        assert not is_valid_edge(bad), f"Expected {bad!r} to be invalid"


def test_validate_edge_returns_valid():
    assert validate_edge("CALLS") == "CALLS"


def test_validate_edge_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown edge type"):
        validate_edge("NOT_A_REAL_EDGE")


# ── render_prompt_reference ───────────────────────────────────────────────────

def test_render_prompt_reference_contains_all_groups():
    rendered = render_prompt_reference()
    for group in EDGE_GROUPS:
        assert group in rendered, f"Group {group!r} missing from rendered prompt reference"


def test_render_prompt_reference_contains_sample_edges():
    rendered = render_prompt_reference()
    for edge in ["CALLS", "READS_COLUMN", "THROWS", "RENDERS_FIELD"]:
        assert edge in rendered


def test_render_prompt_reference_is_nonempty():
    rendered = render_prompt_reference()
    assert len(rendered) > 100


# ── RelationshipExtractor prompt vs taxonomy ──────────────────────────────────

def test_relationship_extractor_prompt_uses_only_taxonomy_edges():
    """Every underscore-joined ALL_CAPS token in the prompt must be a known edge type."""
    from companybrain.pipeline.relationship_extractor import RELATIONSHIP_SYSTEM_PROMPT

    unknown = []
    for word in RELATIONSHIP_SYSTEM_PROMPT.split():
        # Strip punctuation that may trail the token (comma, period, etc.)
        token = word.strip(",-.:;\"'()")
        # Only test tokens that look like edge types: all-caps + underscore, ≥5 chars
        if token and "_" in token and token == token.upper() and len(token) >= 5:
            if token not in EDGE_TYPES:
                unknown.append(token)

    assert not unknown, (
        f"Tokens in RelationshipExtractor prompt not in taxonomy: {unknown}. "
        f"Add them to taxonomy.py or fix the prompt."
    )
