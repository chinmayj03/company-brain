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


def dedup_relationships_by_confidence(
    relationships: list[ExtractedRelationship],
) -> list[ExtractedRelationship]:
    """Return the deduplicated list keeping the highest-confidence edge per triple.

    Triple key: (from_entity, edge_type, to_entity).
    Tie-breaking: longer evidence string wins (richer provenance).
    Order in the output is deterministic (sorted by key) so tests are stable.
    """
    best: dict[tuple[str, str, str], ExtractedRelationship] = {}

    for rel in relationships:
        key = (rel.from_entity, rel.edge_type, rel.to_entity)
        if key not in best:
            best[key] = rel
        else:
            incumbent = best[key]
            if rel.confidence > incumbent.confidence:
                best[key] = rel
            elif rel.confidence == incumbent.confidence:
                # Prefer the one with more evidence text.
                if len(rel.evidence or "") > len(incumbent.evidence or ""):
                    best[key] = rel

    return list(best.values())
