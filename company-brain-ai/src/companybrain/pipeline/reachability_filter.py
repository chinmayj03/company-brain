"""
Reachability filter — drop entities not on the entry endpoint's call graph.

Symptom this fixes: ~48% of entities extracted for a single endpoint were
unrelated drift (Configuration*, Specialty*, sibling controllers). They came
from the navigator/import-graph pulling in too many files OR the LLM emitting
sibling methods adjacent to the target one.

Strategy (language-agnostic):

  1. Find the entry-point ApiEndpoint(s) — given the request's endpoint_path
     + http_method, match against entity.signature / entity.name.
  2. Build a directed graph from the relationships extracted in Stage 2.
  3. BFS from the entry-point via "structural" edge types
     (CALLS, USES, CONTAINS, READS_COLUMN, WRITES_COLUMN, RENDERS_FIELD,
      CALLS_ENDPOINT, DELEGATES_TO, INSTANTIATES, EXTENDS, IMPLEMENTS).
  4. Mark every reachable node + its inbound TESTED_BY / ANNOTATES neighbours
     (so tests covering reachable code stay; annotations on reachable code stay).
  5. Drop everything else.

Safety rails:
  - If we can't identify the entry-point (e.g. ad-hoc map command), this
    function returns the input unchanged.
  - If reachability would drop more than 90% of the entities, log a warning
    and return the input unchanged — likely the entry-point match failed.
  - The filter never drops DatabaseTable / DatabaseColumn entities even if
    unreached, because the LLM commonly references them by short name and
    edge resolution may miss them; keeping them adds zero noise.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from companybrain.models.entities import (
        ExtractedEntity,
        ExtractedRelationship,
    )

log = structlog.get_logger(__name__)


# Edge types that PROPAGATE reachability — if A is reachable and edge
# A→B exists, B is reachable.  Picked to mirror the relationships that
# meaningfully extend the call/data flow from the endpoint.
_PROPAGATE_EDGES = frozenset({
    "CALLS", "INVOKES", "AWAITS", "CALLS_ENDPOINT", "DELEGATES_TO",
    "USES", "DEPENDS_ON", "INSTANTIATES",
    "CONTAINS",
    "EXTENDS", "IMPLEMENTS", "OVERRIDES",
    "READS_COLUMN", "WRITES_COLUMN", "READS_FIELD", "WRITES_FIELD",
    "RETURNS", "ACCEPTS_PARAM", "TRANSFORMS", "SERIALIZES_TO",
    "PERSISTS_TO", "INDEXED_BY", "CACHED_BY",
    "RENDERS", "RENDERS_FIELD", "BINDS_TO", "ROUTED_BY",
    "PUBLISHES_TO", "SUBSCRIBES_TO", "LISTENS_TO", "SCHEDULED_BY",
    "AUTHORIZED_BY", "PROTECTED_BY", "AUDITED_BY",
    "VALIDATES", "ENFORCES", "SANITIZES",
    "THROWS", "CATCHES", "WRAPS_EXCEPTION", "HANDLES_ERROR",
})

# Edge types that ATTACH context to already-reachable nodes — annotation /
# test edges.  We follow them in the *reverse* direction: if A is reachable
# and edge B→A exists with one of these types, then B (the test or
# annotation) is reachable too.
_REVERSE_ATTACH_EDGES = frozenset({
    "TESTED_BY",
    "ANNOTATES",
    "MOCKS",
    "FIXTURE_FOR",
    "DOCUMENTED_BY",
})

# Entity types we always keep regardless of reachability — DatabaseTable /
# DatabaseColumn are commonly referenced by short name in SQL and our edge
# resolution may legitimately miss them. Keeping them is high-signal and
# cheap.
_ALWAYS_KEEP_TYPES = frozenset({
    "DatabaseTable",
    "DatabaseColumn",
    "ExternalService",
    "Annotation",
})

# Safety: if the filter would drop more than this fraction of entities,
# bail out — likely the entry-point match failed and we'd nuke real data.
_MAX_DROP_FRACTION = 0.90


def _find_entry_point_names(
    entities: list["ExtractedEntity"],
    endpoint_path: str | None,
    http_method: str | None,
) -> set[str]:
    """Identify the entry-point entity by matching the request shape.

    Strategy (in order):
      1. ApiEndpoint with signature containing both method + path.
      2. ApiEndpoint with signature containing the path.
      3. Function whose name matches the path's last meaningful segment.
      4. If nothing matches, return empty set (caller bails out safely).
    """
    if not entities:
        return set()
    method = (http_method or "").upper()
    path   = (endpoint_path or "").strip()

    found: set[str] = set()

    if path:
        for e in entities:
            if e.entity_type != "ApiEndpoint":
                continue
            sig = (e.signature or "")
            if path in sig and (not method or method in sig.upper()):
                found.add(e.name)

        if not found:
            for e in entities:
                if e.entity_type == "ApiEndpoint" and path in (e.signature or ""):
                    found.add(e.name)

        if not found:
            # Last meaningful segment fallback — e.g. /v1/payers/{id}/competitors
            # → "competitors" → Function name match.
            tokens = [t for t in path.split("/") if t and "{" not in t]
            if tokens:
                tail = tokens[-1].lower()
                for e in entities:
                    if e.entity_type in ("ApiEndpoint", "Function") \
                            and tail in e.name.lower():
                        found.add(e.name)

    return found


def filter_to_reachable(
    entities: list["ExtractedEntity"],
    relationships: list["ExtractedRelationship"],
    *,
    endpoint_path: str | None,
    http_method: str | None,
) -> tuple[list["ExtractedEntity"], list["ExtractedRelationship"], dict]:
    """Drop entities and edges not reachable from the entry point.

    Returns (kept_entities, kept_relationships, stats_dict).
    Stats include: total / reachable / dropped / dropped_by_type for the log.
    """
    if not entities:
        return entities, relationships, {"total": 0, "reachable": 0, "dropped": 0}

    entry_names = _find_entry_point_names(entities, endpoint_path, http_method)
    if not entry_names:
        log.warning(
            "Reachability filter: could not identify entry point — keeping all entities",
            endpoint_path=endpoint_path,
            http_method=http_method,
            entity_count=len(entities),
        )
        return entities, relationships, {
            "total": len(entities),
            "reachable": len(entities),
            "dropped": 0,
            "skipped": "no_entry_point",
        }

    # Build adjacency by name (the LLM emits names; keep it simple).
    forward: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = {}
    for r in relationships:
        if r.edge_type in _PROPAGATE_EDGES:
            forward.setdefault(r.from_entity, set()).add(r.to_entity)
        if r.edge_type in _REVERSE_ATTACH_EDGES:
            reverse.setdefault(r.to_entity, set()).add(r.from_entity)

    # BFS forward from every entry-point name.
    reachable: set[str] = set(entry_names)
    queue = list(entry_names)
    while queue:
        node = queue.pop()
        for nxt in forward.get(node, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    # Attach annotations / tests / fixtures pointing at reachable nodes.
    for n in list(reachable):
        for src in reverse.get(n, ()):
            reachable.add(src)

    # Always-keep entity types (DatabaseTable / Column / ExternalService).
    for e in entities:
        if e.entity_type in _ALWAYS_KEEP_TYPES:
            reachable.add(e.name)

    kept_entities = [e for e in entities if e.name in reachable]
    dropped_count = len(entities) - len(kept_entities)
    drop_fraction = dropped_count / max(1, len(entities))

    if drop_fraction > _MAX_DROP_FRACTION:
        log.warning(
            "Reachability filter would drop > 90% of entities — bailing, "
            "likely the entry point match failed",
            entry_names=list(entry_names),
            total=len(entities),
            would_drop=dropped_count,
        )
        return entities, relationships, {
            "total": len(entities),
            "reachable": len(entities),
            "dropped": 0,
            "skipped": "drop_fraction_too_high",
        }

    kept_names = {e.name for e in kept_entities}
    kept_relationships = [
        r for r in relationships
        if r.from_entity in kept_names and r.to_entity in kept_names
    ]

    # Per-type drop breakdown for the log.
    dropped_by_type: dict[str, int] = {}
    for e in entities:
        if e.name not in reachable:
            dropped_by_type[e.entity_type] = dropped_by_type.get(e.entity_type, 0) + 1

    log.info(
        "Reachability filter applied",
        entry_names=list(entry_names),
        total=len(entities),
        reachable=len(kept_entities),
        dropped=dropped_count,
        edges_dropped=len(relationships) - len(kept_relationships),
        dropped_by_type=dropped_by_type,
    )
    return kept_entities, kept_relationships, {
        "total": len(entities),
        "reachable": len(kept_entities),
        "dropped": dropped_count,
        "dropped_by_type": dropped_by_type,
        "edges_dropped": len(relationships) - len(kept_relationships),
        "entry_names": list(entry_names),
    }
