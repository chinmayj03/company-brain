# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/__init__.py
"""Structural layer — tree-sitter parsing, risk scoring, and topology analysis.

ADR-006: Adopt code-review-graph as Structural Layer + MCP Tool Surface.
This package ports CRG's structural algorithms (clean-room, with attribution)
against our PostgreSQL schema instead of CRG's SQLite store.
"""

from .parser import NodeInfo, EdgeInfo, ParseResult, parse_file, parse_directory
from .risk import compute_risk_score, RiskFactors

__all__ = [
    "NodeInfo",
    "EdgeInfo",
    "ParseResult",
    "parse_file",
    "parse_directory",
    "compute_risk_score",
    "RiskFactors",
]
