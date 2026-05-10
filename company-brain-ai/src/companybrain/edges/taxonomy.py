"""
Edge taxonomy — single source of truth for all 50 typed edge labels.

ADR-0043 WS2: every consumer of edge types (RelationshipExtractor prompt,
Neo4jWriter validator, graph query helpers, docs) should import from here
so that adding a new edge type is a one-file change.

Usage
-----
    from companybrain.edges.taxonomy import EDGE_TYPES, EDGE_GROUPS, is_valid_edge

    assert is_valid_edge("CALLS")       # True
    assert not is_valid_edge("JUMPS")   # False

    for group, edges in EDGE_GROUPS.items():
        print(group, edges)
"""
from __future__ import annotations

from typing import Literal

# ── Canonical edge type strings ───────────────────────────────────────────────

# fmt: off
EDGE_TYPES: frozenset[str] = frozenset({
    # STRUCTURE / INHERITANCE
    "EXTENDS",
    "IMPLEMENTS",
    "OVERRIDES",
    "CONTAINS",
    "ANNOTATES",
    "IMPORTS",

    # BEHAVIOR / CALL FLOW
    "CALLS",
    "INVOKES",
    "AWAITS",
    "CALLS_ENDPOINT",
    "DELEGATES_TO",
    "INSTANTIATES",
    "USES",

    # DATA FLOW
    "READS_COLUMN",
    "WRITES_COLUMN",
    "READS_FIELD",
    "WRITES_FIELD",
    "RETURNS",
    "ACCEPTS_PARAM",
    "TRANSFORMS",
    "SERIALIZES_TO",

    # PERSISTENCE / STORAGE
    "PERSISTS_TO",
    "CACHED_BY",
    "INDEXED_BY",
    "CONSTRAINED_BY",

    # VALIDATION
    "VALIDATES",
    "ENFORCES",
    "SANITIZES",

    # ERROR / EXCEPTION FLOW
    "THROWS",
    "CATCHES",
    "WRAPS_EXCEPTION",
    "HANDLES_ERROR",

    # UI / FRONTEND
    "RENDERS",
    "RENDERS_FIELD",
    "BINDS_TO",
    "ROUTED_BY",
    "LISTENS_TO",

    # AUTHZ / SECURITY
    "AUTHORIZED_BY",
    "PROTECTED_BY",
    "AUDITED_BY",

    # ASYNC / EVENTING
    "PUBLISHES_TO",
    "SUBSCRIBES_TO",
    "SCHEDULED_BY",

    # OBSERVABILITY
    "LOGS_TO",
    "EMITS_METRIC",
    "TRACED_BY",

    # TESTING
    "TESTED_BY",
    "MOCKS",
    "FIXTURE_FOR",

    # CONFIG / LIFECYCLE
    "CONFIGURED_BY",
    "INITIALIZED_BY",
    "RATE_LIMITED_BY",
})
# fmt: on

# ── Grouped view (for documentation and prompt generation) ────────────────────

EDGE_GROUPS: dict[str, list[str]] = {
    "STRUCTURE / INHERITANCE": [
        "EXTENDS", "IMPLEMENTS", "OVERRIDES", "CONTAINS", "ANNOTATES", "IMPORTS",
    ],
    "BEHAVIOR / CALL FLOW": [
        "CALLS", "INVOKES", "AWAITS", "CALLS_ENDPOINT",
        "DELEGATES_TO", "INSTANTIATES", "USES",
    ],
    "DATA FLOW": [
        "READS_COLUMN", "WRITES_COLUMN", "READS_FIELD", "WRITES_FIELD",
        "RETURNS", "ACCEPTS_PARAM", "TRANSFORMS", "SERIALIZES_TO",
    ],
    "PERSISTENCE / STORAGE": [
        "PERSISTS_TO", "CACHED_BY", "INDEXED_BY", "CONSTRAINED_BY",
    ],
    "VALIDATION": ["VALIDATES", "ENFORCES", "SANITIZES"],
    "ERROR / EXCEPTION FLOW": [
        "THROWS", "CATCHES", "WRAPS_EXCEPTION", "HANDLES_ERROR",
    ],
    "UI / FRONTEND": [
        "RENDERS", "RENDERS_FIELD", "BINDS_TO", "ROUTED_BY", "LISTENS_TO",
    ],
    "AUTHZ / SECURITY": ["AUTHORIZED_BY", "PROTECTED_BY", "AUDITED_BY"],
    "ASYNC / EVENTING": ["PUBLISHES_TO", "SUBSCRIBES_TO", "SCHEDULED_BY"],
    "OBSERVABILITY": ["LOGS_TO", "EMITS_METRIC", "TRACED_BY"],
    "TESTING": ["TESTED_BY", "MOCKS", "FIXTURE_FOR"],
    "CONFIG / LIFECYCLE": ["CONFIGURED_BY", "INITIALIZED_BY", "RATE_LIMITED_BY"],
}

# ── Structural edges pre-extracted by AST (never emit from LLM pass) ──────────
# These are computed deterministically before the LLM relationship pass runs.
# The LLM should skip them; if emitted they'll dedup harmlessly but waste budget.
STRUCTURAL_EDGES: frozenset[str] = frozenset({
    "EXTENDS", "IMPLEMENTS", "CONTAINS", "INSTANTIATES", "IMPORTS",
})

# ── High-value behavioral edges (LLM should prioritise these) ─────────────────
BEHAVIORAL_EDGES: frozenset[str] = frozenset({
    "CALLS", "USES", "THROWS", "CATCHES",
    "READS_COLUMN", "WRITES_COLUMN",
    "VALIDATES", "RENDERS_FIELD",
    "CALLS_ENDPOINT", "AWAITS", "DELEGATES_TO",
    "LISTENS_TO", "PUBLISHES_TO", "SUBSCRIBES_TO", "SCHEDULED_BY",
    "AUTHORIZED_BY",
})


def is_valid_edge(edge_type: str) -> bool:
    """Return True iff edge_type is in the canonical taxonomy."""
    return edge_type in EDGE_TYPES


def validate_edge(edge_type: str) -> str:
    """Return edge_type if valid, else raise ValueError."""
    if not is_valid_edge(edge_type):
        raise ValueError(
            f"Unknown edge type {edge_type!r}. "
            f"Valid types: {sorted(EDGE_TYPES)}"
        )
    return edge_type


def render_prompt_reference() -> str:
    """Render the edge taxonomy as a compact string for inclusion in LLM prompts."""
    lines = []
    for group, edges in EDGE_GROUPS.items():
        lines.append(f"\n# {group}")
        for e in edges:
            lines.append(f"- {e}")
    return "\n".join(lines)
