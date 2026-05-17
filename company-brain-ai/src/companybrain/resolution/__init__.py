"""
Cross-Source Entity Resolution — ADR-0093.

When connectors from different sources (Notion, code, Slack, …) produce
artifacts that describe the same real-world entity, this package identifies
and merges them under a canonical ``domain://`` URN.

Public surface
--------------
* :class:`EntityCandidate`     — lightweight view of one source artifact
* :class:`ResolutionMatch`     — a proposed (or confirmed) pair → domain URN
* :class:`ResolutionResult`    — final resolved entity with merged artifact list
* :class:`ResolutionTier`      — four-tier confidence ladder
* :class:`CrossSourceEntityResolver` — resolves candidates against stored state
* :class:`ResolutionStore`     — persists resolution decisions as JSON
"""

from companybrain.resolution.models import (
    EntityCandidate,
    ResolutionMatch,
    ResolutionResult,
    ResolutionTier,
    TIER_CONFIDENCE,
)
from companybrain.resolution.resolver import CrossSourceEntityResolver
from companybrain.resolution.store import ResolutionStore

__all__ = [
    "EntityCandidate",
    "ResolutionMatch",
    "ResolutionResult",
    "ResolutionTier",
    "TIER_CONFIDENCE",
    "CrossSourceEntityResolver",
    "ResolutionStore",
]
