"""
ADR-0055 SP-2 — Anti-pattern / inconsistency detection (deterministic).

Given the Pattern entities emitted by SP-1 plus a list of candidate
"convention sites", flag the minority that diverges from the majority and
emit VIOLATES_PATTERN edges. Runs entirely on data already in memory; no
LLM, no I/O.

Two distinct shapes are handled:

1. Pattern-strength flips. For each Pattern emitted by SP-1, compare the
   list of entities that COULD have implemented the pattern (e.g. all
   classes that share a similar shape) against the actual implementer
   list. If the strength of adoption is above ``min_strength`` (default
   0.80), the non-implementers are flagged as VIOLATES_PATTERN.

2. Convention-vs-literal inconsistency. Given a list of
   ConventionSite objects (one per call site that uses either a constant
   reference or a string literal), build the population per logical
   field. If the constant-using majority is above ``min_strength``, the
   literal-using minority sites are flagged as violators of an inferred
   "use the named constant" pattern.

Shape 2 is what powers the lob anti-pattern smoke test: 16 DTOs use
``JsonKeyMapping.LOB`` and one uses literal ``"lob"`` — the latter is the
violator.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

from companybrain.models.entities import (
    EDGE_IMPLEMENTS_PATTERN,
    EDGE_VIOLATES_PATTERN,
    ExtractedRelationship,
    Pattern,
)

log = structlog.get_logger(__name__)


@dataclass
class ConventionSite:
    """One site that participates in a literal-vs-constant convention.

    ``entity_external_id`` — the entity that owns the site (e.g. the DTO).
    ``field_key``          — the logical key, e.g. ``"lob"``.
    ``uses_constant``      — True if the site references a named constant
                              (``JsonKeyMapping.LOB``); False if it inlines
                              a string literal.
    ``constant_name``      — full constant identifier when known, else None.
    """
    entity_external_id: str
    field_key: str
    uses_constant: bool
    constant_name: Optional[str] = None


@dataclass
class AntipatternResult:
    patterns: list[Pattern] = field(default_factory=list)
    edges: list[ExtractedRelationship] = field(default_factory=list)


def detect_antipatterns(
    *,
    patterns: Iterable[Pattern] | None = None,
    candidate_universe: dict[str, list[str]] | None = None,
    convention_sites: Iterable[ConventionSite] | None = None,
    min_strength: float = 0.80,
    min_population: int = 5,
) -> AntipatternResult:
    """Flag minority sites that diverge from a strongly-adopted convention.

    Parameters
    ----------
    patterns : iterable of Pattern
        Patterns produced by SP-1. Used to evaluate "strength flips" when
        a candidate universe is known for the pattern.
    candidate_universe : dict[pattern_external_id, list[entity_external_id]]
        For each Pattern, the full population of entities that could have
        implemented it. Entries missing from ``Pattern.instance_urns`` are
        flagged as violators when adoption strength exceeds
        ``min_strength``.
    convention_sites : iterable of ConventionSite
        Sites that pair a literal value with a flag indicating whether the
        site used a named constant. Drives the lob-style anti-pattern
        detection.
    min_strength : float
        Adoption ratio above which the minority is flagged. Default 0.80.
    min_population : int
        Minimum population size before any flag is raised. Stops the
        detector from raising false positives on tiny graphs.
    """
    out = AntipatternResult()

    # ── Shape 1: pattern strength flips ───────────────────────────────────
    if patterns and candidate_universe:
        for pattern in patterns:
            universe = list({u for u in candidate_universe.get(pattern.external_id, []) if u})
            if len(universe) < min_population:
                continue
            implementers = set(pattern.instance_urns)
            adopted = [u for u in universe if u in implementers]
            violators = [u for u in universe if u not in implementers]
            strength = len(adopted) / max(1, len(universe))
            if strength < min_strength or not violators:
                continue
            for v in violators:
                out.edges.append(_violation_edge(v, pattern, strength))

    # ── Shape 2: convention-vs-literal inconsistency ──────────────────────
    if convention_sites:
        sites_by_key: dict[str, list[ConventionSite]] = defaultdict(list)
        for s in convention_sites:
            if not s.field_key or not s.entity_external_id:
                continue
            sites_by_key[s.field_key].append(s)

        for key, sites in sites_by_key.items():
            if len(sites) < min_population:
                continue
            constant_users = [s for s in sites if s.uses_constant]
            literal_users  = [s for s in sites if not s.uses_constant]
            strength = len(constant_users) / len(sites)
            if strength < min_strength or not literal_users:
                continue

            constant_name = _majority_constant_name(constant_users) or key.upper()
            pattern = Pattern(
                name=_safe_name(f"use_constant_{constant_name}"),
                description=(
                    f"{len(constant_users)}/{len(sites)} sites for field "
                    f"\"{key}\" use the named constant {constant_name}; "
                    f"{len(literal_users)} site(s) inline the literal "
                    f"\"{key}\" instead."
                ),
                instance_count=len(constant_users),
                confidence=min(0.95, strength),
                inferred_from="deterministic",
                instance_urns=sorted(s.entity_external_id for s in constant_users),
            )
            out.patterns.append(pattern)
            for s in constant_users:
                out.edges.append(_implements_edge(s.entity_external_id, pattern))
            for s in literal_users:
                out.edges.append(_violation_edge(s.entity_external_id, pattern, strength))

    log.info(
        "antipattern_detector.detect_antipatterns",
        new_patterns=len(out.patterns),
        new_edges=len(out.edges),
        min_strength=min_strength,
    )
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _majority_constant_name(sites: list[ConventionSite]) -> Optional[str]:
    counts: dict[str, int] = defaultdict(int)
    for s in sites:
        if s.constant_name:
            counts[s.constant_name] += 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _safe_name(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in "_-.":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "pattern"


def _violation_edge(
    entity: str, pattern: Pattern, strength: float,
) -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=entity,
        from_type="Class",
        edge_type=EDGE_VIOLATES_PATTERN,
        to_entity=pattern.external_id,
        to_type="Pattern",
        confidence=min(0.95, strength),
        evidence=(
            f"diverges from {pattern.name} adopted by "
            f"{pattern.instance_count} other site(s)"
        ),
    )


def _implements_edge(entity: str, pattern: Pattern) -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=entity,
        from_type="Class",
        edge_type=EDGE_IMPLEMENTS_PATTERN,
        to_entity=pattern.external_id,
        to_type="Pattern",
        confidence=pattern.confidence,
        evidence=f"convention adoption: {pattern.name}",
    )
