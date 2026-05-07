# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/changes.py  (compute_risk_score function)
#           code_review_graph/constants.py (SECURITY_KEYWORDS)
#
# Key changes from the original:
#   - Replaced SQLite GraphStore with a plain data-class interface so the
#     caller can pass in pre-fetched Postgres data without a DB round-trip
#     inside the scorer.
#   - Added RiskFactors dataclass so callers can store the per-factor
#     breakdown in the nodes.risk_factors JSONB column.
#   - Weights and caps are identical to CRG's implementation.
"""Multi-factor structural risk scoring for company-brain nodes.

Computes a 0.0–1.0 risk score for any code node, combining five factors:

    Factor             Weight   Source
    flow_participation  0–0.25  How many execution flows pass through this node
    community_crossing  0–0.15  Callers from different architectural communities
    test_coverage       0–0.30  Untested = 0.30; ≥5 TESTED_BY edges = 0.05
    security_keywords   +0.20   Name / qualified_name contains auth/token/etc.
    caller_count        0–0.10  Popular nodes are riskier to change

The result is stored in ``nodes.risk_score`` and the factor breakdown in
``nodes.risk_factors`` JSONB so the frontend can show an explainer.

Usage::

    from companybrain.structural.risk import compute_risk_score, NodeRiskInput

    node_input = NodeRiskInput(
        name="chargePayment",
        qualified_name="backend/src/PaymentService.java::PaymentService.chargePayment",
        flow_count=2,
        flow_criticality_sum=0.40,
        cross_community_caller_count=1,
        test_count=0,
        caller_count=8,
    )
    score, factors = compute_risk_score(node_input)
    # score ≈ 0.78, factors.tests = 0.30, factors.security = 0.20 ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Security keyword set
# Ported from CRG's constants.py SECURITY_KEYWORDS.
# ---------------------------------------------------------------------------

_SECURITY_KEYWORDS: frozenset[str] = frozenset({
    "auth", "login", "password", "token", "session", "crypt", "secret",
    "credential", "permission", "sql", "query", "execute", "connect",
    "socket", "request", "http", "sanitize", "validate", "encrypt",
    "decrypt", "hash", "sign", "verify", "admin", "privilege",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RiskFactors:
    """Per-factor breakdown of a node's risk score.

    Stored verbatim in ``nodes.risk_factors`` as JSONB.
    All values are 0.0–1.0 (the factor's contribution, not a raw count).
    """

    flow:      float = 0.0   # Flow participation contribution
    community: float = 0.0   # Cross-community caller contribution
    tests:     float = 0.0   # Test-gap contribution (higher = less tested)
    security:  float = 0.0   # Security keyword contribution
    callers:   float = 0.0   # Caller-count contribution

    def total(self) -> float:
        return round(min(self.flow + self.community + self.tests + self.security + self.callers, 1.0), 4)

    def to_dict(self) -> dict[str, float]:
        """Serialise to the shape stored in nodes.risk_factors JSONB."""
        return {
            "flow":      round(self.flow, 4),
            "community": round(self.community, 4),
            "tests":     round(self.tests, 4),
            "security":  round(self.security, 4),
            "callers":   round(self.callers, 4),
        }


@dataclass
class NodeRiskInput:
    """Caller-supplied inputs to the risk scorer.

    All values are derived from the structural graph (Postgres queries).
    The scorer is a pure function of these inputs — no DB calls inside.

    Fields:
        name:                      Simple name of the node (e.g. "chargePayment")
        qualified_name:            Full qualified name (used for keyword matching)
        flow_count:                Number of flows this node participates in
        flow_criticality_sum:      Sum of criticality scores across those flows
                                   (prefer this over flow_count when available)
        cross_community_caller_count: Callers from a different community
        test_count:                Number of TESTED_BY edges (direct + transitive)
        caller_count:              Number of CALLS edges targeting this node
    """

    name: str
    qualified_name: str
    flow_count: int = 0
    flow_criticality_sum: float = 0.0
    cross_community_caller_count: int = 0
    test_count: int = 0
    caller_count: int = 0


# ---------------------------------------------------------------------------
# Core scorer
# ---------------------------------------------------------------------------

def compute_risk_score(node: NodeRiskInput) -> tuple[float, RiskFactors]:
    """Compute risk score (0.0–1.0) and per-factor breakdown for a node.

    Algorithm ported from CRG's ``compute_risk_score`` in changes.py.

    Returns:
        (score, factors) — ``score`` is the clamped sum of all factors;
        ``factors`` is the per-factor breakdown for UI display / JSONB storage.
    """
    factors = RiskFactors()

    # ── Factor 1: Flow participation (cap 0.25) ────────────────────────────
    # If we have criticality sums (week 4+) use them directly.
    # Until flows are populated, fall back to a flat 0.05 per flow.
    if node.flow_criticality_sum > 0:
        factors.flow = min(node.flow_criticality_sum, 0.25)
    else:
        factors.flow = min(node.flow_count * 0.05, 0.25)

    # ── Factor 2: Cross-community callers (cap 0.15) ──────────────────────
    factors.community = min(node.cross_community_caller_count * 0.05, 0.15)

    # ── Factor 3: Test coverage gap (0.05 – 0.30) ────────────────────────
    # Untested node → +0.30.  5+ transitive tests → +0.05 (residual).
    # Linearly interpolated between 0 and 5 tests.
    if node.test_count >= 5:
        factors.tests = 0.05
    else:
        # 0 tests → 0.30; each test removes 0.05 up to 5 tests.
        factors.tests = round(0.30 - (min(node.test_count / 5.0, 1.0) * 0.25), 4)

    # ── Factor 4: Security keyword sensitivity (+0.20) ────────────────────
    name_lower = node.name.lower()
    qn_lower   = node.qualified_name.lower()
    if any(kw in name_lower or kw in qn_lower for kw in _SECURITY_KEYWORDS):
        factors.security = 0.20

    # ── Factor 5: Caller count (cap 0.10) ─────────────────────────────────
    # Widely-called nodes carry higher blast radius risk.
    factors.callers = min(node.caller_count / 20.0, 0.10)

    # ── Final clamped score ───────────────────────────────────────────────
    score = factors.total()
    return score, factors


# ---------------------------------------------------------------------------
# Convenience: score a batch from raw Postgres rows
# ---------------------------------------------------------------------------

def score_from_row(row: dict) -> tuple[float, dict]:
    """Compute risk from a dict matching the structural query result shape.

    Designed to be called on rows returned by the structural backfill query
    that joins nodes, flow_memberships, node_communities, and CALLS edges.

    Expected keys (all optional — missing keys default to 0):
        name, qualified_name,
        flow_count, flow_criticality_sum,
        cross_community_caller_count,
        test_count, caller_count

    Returns:
        (score, factors_dict) suitable for writing to nodes.risk_score and
        nodes.risk_factors.
    """
    node = NodeRiskInput(
        name=row.get("name") or "",
        qualified_name=row.get("qualified_name") or row.get("name") or "",
        flow_count=int(row.get("flow_count") or 0),
        flow_criticality_sum=float(row.get("flow_criticality_sum") or 0.0),
        cross_community_caller_count=int(row.get("cross_community_caller_count") or 0),
        test_count=int(row.get("test_count") or 0),
        caller_count=int(row.get("caller_count") or 0),
    )
    score, factors = compute_risk_score(node)
    return score, factors.to_dict()
