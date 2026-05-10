"""
Relationship deduplication helpers — ADR-0043 WS1.S4.

The existing first-wins dedup silently drops higher-confidence edges when a
lower-confidence edge for the same (from, type, to) triple happens to arrive
first. This module replaces it with confidence-weighted dedup: keep the
relationship with the highest confidence; break ties by longest evidence string.
"""
from __future__ import annotations

from typing import TypeVar

from companybrain.models.entities import ExtractedRelationship


def _coerce(rel) -> ExtractedRelationship:
    """Tolerate raw dicts (pattern_distiller / brain_enrich leak them) AND
    proper ExtractedRelationship instances. Returns ExtractedRelationship
    or raises if the dict is too malformed to be useful.
    """
    if isinstance(rel, ExtractedRelationship):
        return rel
    if isinstance(rel, dict):
        return ExtractedRelationship(
            from_entity=rel.get("from_entity") or rel.get("from") or "",
            from_type=rel.get("from_type", ""),
            edge_type=rel.get("edge_type") or "",
            to_entity=rel.get("to_entity") or rel.get("to") or "",
            to_type=rel.get("to_type", ""),
            confidence=float(rel.get("confidence", 0.7) or 0.7),
            evidence=rel.get("evidence", "") or "",
        )
    raise TypeError(f"_dedup got unexpected {type(rel).__name__}")


def dedup_relationships_by_confidence(
    relationships: list,
) -> list[ExtractedRelationship]:
    """Return the deduplicated list keeping the highest-confidence edge per triple.

    Triple key: (from_entity, edge_type, to_entity).
    Tie-breaking: longer evidence string wins (richer provenance).
    Order in the output is deterministic (sorted by key) so tests are stable.

    Defensive: tolerates raw dicts in the input (pattern_distiller and a few
    brain_enrich code paths leak them). Coerces to ExtractedRelationship
    on entry. Drops anything that can't be coerced rather than crashing.
    """
    best: dict[tuple[str, str, str], ExtractedRelationship] = {}
    coerced_count = 0
    dropped_count = 0

    for raw in relationships:
        try:
            rel = _coerce(raw)
            if not isinstance(raw, ExtractedRelationship):
                coerced_count += 1
        except (TypeError, KeyError, ValueError):
            dropped_count += 1
            continue
        if not rel.from_entity or not rel.edge_type or not rel.to_entity:
            dropped_count += 1
            continue
        key = (rel.from_entity, rel.edge_type, rel.to_entity)
        if key not in best:
            best[key] = rel
        else:
            incumbent = best[key]
            if rel.confidence > incumbent.confidence:
                best[key] = rel
            elif rel.confidence == incumbent.confidence:
                if len(rel.evidence or "") > len(incumbent.evidence or ""):
                    best[key] = rel

    if coerced_count or dropped_count:
        import structlog
        structlog.get_logger(__name__).warning(
            "[dedup_relationships_by_confidence] tolerated upstream noise",
            coerced_dicts=coerced_count, dropped=dropped_count,
            input_total=len(relationships),
        )
    return list(best.values())
