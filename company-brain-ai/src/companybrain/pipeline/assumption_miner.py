"""
Static assumption miner — extracts invariants from source code patterns.

Heuristics (deterministic, zero LLM cost):
  - JSDoc/docstring '@assumption' tags
  - Python comments:  # ASSUMPTION: ...
  - JS/TS inline:     // ASSUME: ...
  - Non-null assertions in TypeScript: `user!.role`
  - Guard-clause throws: `if (!user) throw`
  - Assertion library calls: `assert(...)`, `invariant(...)`
  - Zod / Pydantic `.parse()` — runtime contract enforcement

Each matched pattern produces one BrainEntity of entity_type='assumption'
with a RELIES_ON back-edge pointing to the parent entity.

ADR-0017: Promote assumption to first-class graph node.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from companybrain.store.base import BrainEntity
from companybrain.store.identity import to_urn, workspace_slug_for

if TYPE_CHECKING:
    from companybrain.collectors.code_tracer import CodeUnit


# ── Pattern registry ──────────────────────────────────────────────────────────

_PATTERNS: dict[str, re.Pattern[str]] = {
    "explicit_jsdoc":    re.compile(r"@assumption\s+(.+)", re.IGNORECASE),
    "explicit_python":   re.compile(r"#\s*ASSUMPTION:\s*(.+)", re.IGNORECASE),
    "explicit_js":       re.compile(r"//\s*ASSUME:\s*(.+)", re.IGNORECASE),
    "non_null_ts":       re.compile(r"(\w+)!\.(\w+)"),
    "guard_throw":       re.compile(r"if\s*\(\s*!(.+?)\s*\)\s*(?:throw|raise)"),
    "assert":            re.compile(r"\bassert\s*\((.+?)\)"),
    "invariant":         re.compile(r"\binvariant\s*\((.+?)\)"),
    "zod_parse":         re.compile(r"\.parse\s*\((.+?)\)"),
}

_SEVERITY: dict[str, str] = {
    "explicit_jsdoc":    "low",
    "explicit_python":   "low",
    "explicit_js":       "low",
    "non_null_ts":       "medium",
    "guard_throw":       "high",
    "assert":            "high",
    "invariant":         "critical",
    "zod_parse":         "medium",
}

_CONFIDENCE: dict[str, float] = {
    "explicit_jsdoc":    0.95,
    "explicit_python":   0.95,
    "explicit_js":       0.95,
    "non_null_ts":       0.70,
    "guard_throw":       0.85,
    "assert":            0.90,
    "invariant":         0.95,
    "zod_parse":         0.80,
}

_MAX_STATEMENT_LEN = 200


# ── Public API ────────────────────────────────────────────────────────────────

def mine_assumptions(
    unit: "CodeUnit",
    parent: BrainEntity,
    *,
    workspace_id: str,
) -> list[BrainEntity]:
    """
    Return a list of BrainEntity(entity_type='assumption') found in *unit*.

    Each result has a RELIES_ON relationship back to *parent*.
    Duplicate statements (same pattern + same text) within a unit are
    deduplicated — the first match wins.
    """
    out: list[BrainEntity] = []
    slug = workspace_slug_for(workspace_id)
    seen: set[str] = set()

    for pattern_name, pattern in _PATTERNS.items():
        for m in pattern.finditer(unit.content or ""):
            statement = m.group(1).strip()[:_MAX_STATEMENT_LEN]
            # Dedup key: pattern × normalised statement
            dedup_key = f"{pattern_name}|{statement.lower()}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Stable qualified_name: parent qname + pattern tag + hash of statement
            qname = (
                f"{parent.qualified_name}"
                f"__{pattern_name}"
                f"__{hash(statement) & 0xFFFF:04x}"
            )
            urn = to_urn(
                tenant=slug,
                domain="code",
                repo=parent.repo,
                entity_type="assumption",
                qualified_name=qname,
            )
            out.append(BrainEntity(
                id=urn,
                entity_type="assumption",
                repo=parent.repo,
                file=unit.file_path,
                qualified_name=qname,
                t1_summary=f"{pattern_name}: {statement}",
                metadata={
                    "statement":  statement,
                    "pattern":    pattern_name,
                    "severity":   _SEVERITY.get(pattern_name, "low"),
                    "origin":     "static_extractor",
                },
                relationships=[{
                    "target_id":  parent.id,
                    "edge_type":  "RELIES_ON",
                    "confidence": _CONFIDENCE.get(pattern_name, 0.5),
                    "source":     "static_analysis",
                }],
            ))

    return out
