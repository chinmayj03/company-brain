"""
ADR-0044 PR-0044-5: Deterministic merger for per-chunk extraction results.

Two problems solved here:
  1. merge_chunk_entities() — when multiple chunks produce the same qname
     (e.g. interface + implementation both yield "OrderRepo.findById"),
     pick the winner by confidence and merge metadata from the loser.
  2. resolve_edges() — replace symbolic targets ("fetchAllCompetitors") with
     canonical URNs when the qname is in the name-to-urn index.

Both operations are deterministic and idempotent:
  merge(merge(x)) == merge(x)
  resolve(resolve(edges, idx), idx) == resolve(edges, idx)
"""
from __future__ import annotations

from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge


def merge_chunk_entities(
    entities: list[ExtractedChunkEntity],
) -> list[ExtractedChunkEntity]:
    """
    Merge a flat list of chunk entities so each qname appears at most once.

    Tie-break rules (applied in order):
      1. Higher confidence wins.
      2. Longer query_text wins (more complete SQL).
      3. Longer code_snippet wins (richer context).
      4. Longer signature wins.
    The winner keeps its core fields; loser contributes metadata
    for any fields the winner left empty.
    """
    by_qname: dict[str, ExtractedChunkEntity] = {}

    for e in entities:
        if e.qname not in by_qname:
            by_qname[e.qname] = e
            continue

        existing = by_qname[e.qname]
        if _score(e) > _score(existing):
            winner, loser = e, existing
        else:
            winner, loser = existing, e

        # Merge metadata: take the best (longest non-empty) value from each
        winner = _merge_metadata(winner, loser)
        by_qname[e.qname] = winner

    return list(by_qname.values())


def resolve_edges(
    edges: list[ExtractedEdge],
    name_to_urn: dict[str, str],
) -> list[ExtractedEdge]:
    """
    Replace edge targets that are plain names with their canonical URNs.
    Targets that are not in name_to_urn are left as-is.
    """
    result: list[ExtractedEdge] = []
    for edge in edges:
        target = edge.target
        if target in name_to_urn:
            target = name_to_urn[target]
        result.append(ExtractedEdge(
            edge_type=edge.edge_type,
            target=target,
            confidence=edge.confidence,
            evidence=edge.evidence,
        ))
    return result


def dedup_edges(edges: list[ExtractedEdge]) -> list[ExtractedEdge]:
    """
    Remove duplicate edges by (edge_type, target).
    First occurrence wins (highest confidence since list is pre-sorted by confidence).
    """
    seen: set[tuple[str, str]] = set()
    result: list[ExtractedEdge] = []
    for edge in edges:
        key = (edge.edge_type, edge.target)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score(e: ExtractedChunkEntity) -> tuple:
    """Higher is better. Lexicographically compared."""
    return (
        e.confidence,
        len(e.query_text or ""),
        len(e.code_snippet or ""),
        len(e.signature or ""),
    )


def _merge_metadata(
    winner: ExtractedChunkEntity,
    loser: ExtractedChunkEntity,
) -> ExtractedChunkEntity:
    """
    Return a new entity with winner's identity but supplemented by loser's
    metadata for any fields the winner left empty.
    """
    from dataclasses import replace

    updates: dict = {}
    for field in ("query_text", "code_snippet", "signature"):
        if not getattr(winner, field) and getattr(loser, field):
            updates[field] = getattr(loser, field)

    if not updates:
        return winner

    return ExtractedChunkEntity(
        entity_type=winner.entity_type,
        name=winner.name,
        qname=winner.qname,
        file_path=winner.file_path,
        signature=updates.get("signature", winner.signature),
        confidence=winner.confidence,
        query_text=updates.get("query_text", winner.query_text),
        code_snippet=updates.get("code_snippet", winner.code_snippet),
        language=winner.language,
    )
