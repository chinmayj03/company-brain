"""
Data models for Cross-Source Entity Resolution (ADR-0093).

Tier ladder
-----------
EXPLICIT_LINK    → artifact carries an explicit domain URN reference  (0.95)
NAME_MATCH       → normalized names are equal within domain            (0.82)
SEMANTIC_EMBED   → cosine(embed(a.title), embed(b.title)) ≥ 0.80      (0.72)
HUMAN_CONFIRMED  → a human operator confirmed the match               (1.00)

Auto-resolve threshold : confidence ≥ 0.80
Suggest threshold      : 0.60 ≤ confidence < 0.80
Separate entities      : confidence < 0.60
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ResolutionTier(str, Enum):
    """Ordered evidence tiers, weakest to strongest (except HUMAN_CONFIRMED)."""
    SEMANTIC_EMBED   = "semantic_embed"    # cosine similarity over embeddings
    NAME_MATCH       = "name_match"        # normalized name equality
    EXPLICIT_LINK    = "explicit_link"     # artifact carries explicit URN ref
    HUMAN_CONFIRMED  = "human_confirmed"   # operator confirmed via /resolution/confirm


TIER_CONFIDENCE: dict[ResolutionTier, float] = {
    ResolutionTier.EXPLICIT_LINK:   0.95,
    ResolutionTier.NAME_MATCH:      0.82,
    ResolutionTier.SEMANTIC_EMBED:  0.72,
    ResolutionTier.HUMAN_CONFIRMED: 1.0,
}

# Thresholds
CONFIDENCE_AUTO_RESOLVE: float = 0.80    # ≥ this → merge automatically
CONFIDENCE_SUGGEST:      float = 0.60    # ≥ this → surface for human review
# < CONFIDENCE_SUGGEST → treat as separate entities


@dataclass
class EntityCandidate:
    """
    Lightweight, source-agnostic view of one artifact for resolution.

    ``artifact_urn`` follows the connector URN convention established by ADR-0092:
        source://{source_type}/{resource_type}/{id}@{workspace}
    e.g. ``source://notion/page/abc123@acme``

    ``domain_hints`` are lowercased domain keywords extracted from the artifact
    (e.g. ``["payer", "billing", "module"]``) — used for fast pre-filtering
    before expensive embedding similarity.
    """
    artifact_urn:    str              # source://notion/page/abc123@workspace
    source_type:     str              # "notion" | "code" | "slack" | …
    title:           str              # human-readable name / title
    content_snippet: str              # first ~200 chars of body content
    domain_hints:    list[str] = field(default_factory=list)
    # Optional: artifact may already carry a reference to a known domain entity
    explicit_domain_urn: Optional[str] = None  # domain://customer@workspace


@dataclass
class ResolutionMatch:
    """
    A proposed or confirmed link between two artifacts to the same domain entity.

    ``id`` is generated as ``sha1(sorted(artifact_urns))[:12]`` so the same pair
    always produces the same id regardless of argument order.
    """
    id:            str               # deterministic 12-char hex id for API reference
    candidate_a:   EntityCandidate
    candidate_b:   EntityCandidate
    tier:          ResolutionTier
    confidence:    float             # 0.0–1.0
    domain_urn:    str               # domain://customer@workspace — resolved entity
    # Lifecycle: "pending" | "confirmed" | "rejected"
    status:        str = "pending"


@dataclass
class ResolutionResult:
    """
    Final resolution outcome for a single domain entity.

    ``resolution_path`` records which tiers contributed evidence (ordered by
    when each was applied — may contain duplicates if multiple pairs were merged).
    ``cross_source_confidence`` is the mean of each individual match's confidence.
    """
    domain_urn:                str
    merged_artifacts:          list[str]            # artifact URNs resolved here
    resolution_path:           list[ResolutionTier] # tiers used, in order
    cross_source_confidence:   float                # mean confidence across matches

    @property
    def should_auto_resolve(self) -> bool:
        return self.cross_source_confidence >= CONFIDENCE_AUTO_RESOLVE

    @property
    def should_suggest(self) -> bool:
        return CONFIDENCE_SUGGEST <= self.cross_source_confidence < CONFIDENCE_AUTO_RESOLVE
