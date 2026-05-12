"""ADR-0061 E6 — cross-repo similarity surfacing.

When an answer mentions a ``Pattern`` (from ADR-0055) or a hot-path method,
search Qdrant across the user's OTHER workspaces for similar entities. A
high-similarity match (cosine >= ``MIN_SCORE``) becomes a ``SimilarTo``
insight attached to the response.

Design points:

* Searches the cross-type ``code`` granularity collection (it contains
  signatures and t1_token text, which is what we want to match on).
* Enumerates workspace slugs by listing ``brain__*__code`` collections in
  Qdrant — we don't keep a workspace registry yet (ADR-0016 deferred), and
  this enumeration is fast.
* Skips the caller's own workspace.
* Returns at most ``MAX_INSIGHTS`` rows so the response stays UI-friendly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

import structlog

from companybrain.retrieval.embedder import Embedder, make_embedder
from companybrain.retrieval.qdrant_client import (
    GRANULARITY_COLLECTIONS, granularity_collection_name, make_client,
)

log = structlog.get_logger(__name__)


MIN_SCORE = 0.85
MAX_INSIGHTS = 5
MAX_QUERY_TEXT_LEN = 600           # don't embed pages of code; the t1_token works
DEFAULT_GRANULARITY = "code"
SUPPORTED_GRANULARITIES = set(GRANULARITY_COLLECTIONS)


@dataclass
class SimilarityInsight:
    """One cross-workspace match worth surfacing to the user."""
    source_urn: str
    source_name: str
    target_workspace: str
    target_urn: str
    target_name: str
    score: float
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "source_urn": self.source_urn,
            "source_name": self.source_name,
            "target_workspace": self.target_workspace,
            "target_urn": self.target_urn,
            "target_name": self.target_name,
            "score": round(float(self.score), 4),
            "note": self.note,
        }


# ── Public helpers ────────────────────────────────────────────────────────────

def find_similar(
    *,
    own_workspace_slug: str,
    seeds: Iterable[dict],
    embedder: Optional[Embedder] = None,
    granularity: str = DEFAULT_GRANULARITY,
    min_score: float = MIN_SCORE,
    max_insights: int = MAX_INSIGHTS,
) -> list[SimilarityInsight]:
    """For each ``seed`` ({urn, name, text}), query every other workspace's
    code-granularity collection and return high-similarity matches.

    Returns at most ``max_insights`` insights, sorted by score desc.
    """
    if granularity not in SUPPORTED_GRANULARITIES:
        raise ValueError(
            f"Unsupported granularity {granularity!r}; must be one of "
            f"{sorted(SUPPORTED_GRANULARITIES)}"
        )
    seeds = list(seeds)
    if not seeds:
        return []
    try:
        client = make_client()
    except Exception as e:
        log.debug("cross_repo_similarity.qdrant_unavailable", error=str(e))
        return []
    other_workspaces = _list_other_workspaces(client, own_workspace_slug,
                                              granularity=granularity)
    if not other_workspaces:
        return []
    if embedder is None:
        try:
            embedder = make_embedder()
        except Exception as e:
            log.debug("cross_repo_similarity.embedder_unavailable", error=str(e))
            return []

    insights: list[SimilarityInsight] = []
    for seed in seeds:
        text = (seed.get("text") or seed.get("name") or "").strip()
        if not text:
            continue
        if len(text) > MAX_QUERY_TEXT_LEN:
            text = text[:MAX_QUERY_TEXT_LEN]
        try:
            vector = embedder.embed(text)
        except Exception as e:
            log.debug("cross_repo_similarity.embed_failed",
                      seed=seed.get("urn", ""), error=str(e))
            continue
        for ws_slug in other_workspaces:
            coll = granularity_collection_name(ws_slug, granularity)
            try:
                hits = client.search(
                    collection_name=coll,
                    query_vector=("dense", vector),
                    limit=3,
                    with_payload=True,
                )
            except Exception as e:
                log.debug("cross_repo_similarity.search_failed",
                          collection=coll, error=str(e))
                continue
            for h in hits:
                if (h.score or 0.0) < min_score:
                    continue
                payload = h.payload or {}
                target_urn = payload.get("urn") or str(h.id)
                target_name = (
                    payload.get("qualified_name") or payload.get("name") or ""
                )
                # Skip pings to the same URN (e.g. shared fixtures)
                if target_urn == seed.get("urn"):
                    continue
                insights.append(SimilarityInsight(
                    source_urn=seed.get("urn", ""),
                    source_name=seed.get("name", ""),
                    target_workspace=ws_slug,
                    target_urn=target_urn,
                    target_name=target_name,
                    score=float(h.score or 0.0),
                    note=(
                        f"Structurally similar to {target_name or target_urn} "
                        f"in workspace '{ws_slug}'."
                    ),
                ))
    insights.sort(key=lambda x: x.score, reverse=True)
    return insights[:max_insights]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _list_other_workspaces(client, own_workspace_slug: str,
                           *, granularity: str) -> list[str]:
    """Return workspace slugs that have a Qdrant collection at the given
    granularity, minus the caller's own."""
    suffix = f"__{granularity}"
    own_collection = granularity_collection_name(own_workspace_slug, granularity)
    try:
        cols = client.get_collections()
        names = [c.name for c in getattr(cols, "collections", [])]
    except Exception as e:
        log.debug("cross_repo_similarity.list_collections_failed",
                  error=str(e))
        return []
    out: list[str] = []
    for name in names:
        if not name.startswith("brain__"):
            continue
        if name == own_collection:
            continue
        if not name.endswith(suffix):
            continue
        slug = name[len("brain__"):-len(suffix)]
        if slug and slug != own_workspace_slug:
            out.append(slug)
    return out


# ── Orchestrator-friendly facade ──────────────────────────────────────────────

def attach_cross_repo_insights(
    *,
    response,
    own_workspace_slug: str,
    seeds: list[dict],
    granularity: str = DEFAULT_GRANULARITY,
) -> int:
    """Mutate ``response`` in place to attach insights. Returns the number of
    insights added so the caller can update telemetry."""
    if not seeds:
        return 0
    try:
        insights = find_similar(
            own_workspace_slug=own_workspace_slug,
            seeds=seeds,
            granularity=granularity,
        )
    except ValueError:
        raise
    except Exception as e:
        log.debug("cross_repo_similarity.attach_failed", error=str(e))
        return 0
    if not insights:
        return 0
    response.cross_repo_insights = [i.to_dict() for i in insights]
    return len(insights)


__all__ = [
    "MIN_SCORE", "MAX_INSIGHTS", "SimilarityInsight",
    "find_similar", "attach_cross_repo_insights",
]
