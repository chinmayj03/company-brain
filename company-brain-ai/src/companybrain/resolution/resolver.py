"""
CrossSourceEntityResolver — ADR-0093 four-tier resolution algorithm.

Resolution tiers (descending confidence, ascending cost):

1. EXPLICIT_LINK  (0.95) — artifact.explicit_domain_urn is already set.
2. NAME_MATCH     (0.82) — normalized titles are identical.
3. SEMANTIC_EMBED (0.72) — cosine similarity ≥ threshold (lazy, skipped when
                            sentence-transformers is unavailable).
4. HUMAN_CONFIRMED (1.0) — stored via ResolutionStore after operator action.

The resolver is **stateless** — it does not own the ResolutionStore.  Callers
that want persistence should call ``store.record_resolution(match)`` after
receiving a ResolutionResult.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

import structlog

from companybrain.resolution.embed_matcher import EmbedMatcher, EmbedMatcherUnavailable
from companybrain.resolution.models import (
    CONFIDENCE_AUTO_RESOLVE,
    CONFIDENCE_SUGGEST,
    TIER_CONFIDENCE,
    EntityCandidate,
    ResolutionMatch,
    ResolutionResult,
    ResolutionTier,
)
from companybrain.resolution.name_matcher import names_match

log = structlog.get_logger(__name__)


def _make_match_id(urn_a: str, urn_b: str) -> str:
    """Deterministic 12-char hex id regardless of argument order."""
    key = "|".join(sorted([urn_a, urn_b]))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _derive_domain_urn(candidate: EntityCandidate, workspace_id: str) -> str:
    """
    Derive a canonical domain URN from an EntityCandidate.

    If the candidate already carries an explicit domain URN that is a
    ``domain://`` URN, return it unchanged.  Otherwise synthesise one from
    the normalized title:
        domain://{slug}@{workspace_id}
    """
    if candidate.explicit_domain_urn and candidate.explicit_domain_urn.startswith("domain://"):
        return candidate.explicit_domain_urn
    slug = re.sub(r"[^a-z0-9]+", "_", candidate.title.lower()).strip("_")
    return f"domain://{slug}@{workspace_id}"


class CrossSourceEntityResolver:
    """
    Resolves an :class:`EntityCandidate` against a list of existing candidates
    using a four-tier evidence ladder.

    Parameters
    ----------
    embed_matcher:
        Optional pre-configured :class:`EmbedMatcher`.  When omitted a default
        instance is created on first use (lazy-loaded).
    embed_threshold:
        Cosine similarity floor for the SEMANTIC_EMBED tier.  Passed to the
        default EmbedMatcher if ``embed_matcher`` is not supplied.
    """

    def __init__(
        self,
        embed_matcher: Optional[EmbedMatcher] = None,
        embed_threshold: float = 0.80,
    ) -> None:
        self._embed_matcher: Optional[EmbedMatcher] = embed_matcher
        self._embed_threshold = embed_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def find_candidates(
        self,
        candidate: EntityCandidate,
        existing: list[EntityCandidate],
    ) -> list[ResolutionMatch]:
        """
        Compare *candidate* against each existing candidate and return all
        matches that exceed CONFIDENCE_SUGGEST (0.60).

        Results are sorted by confidence descending.
        """
        matches: list[ResolutionMatch] = []
        for other in existing:
            if other.artifact_urn == candidate.artifact_urn:
                continue
            match = self._compare(candidate, other)
            if match is not None:
                matches.append(match)
        return sorted(matches, key=lambda m: m.confidence, reverse=True)

    def resolve_artifact(
        self,
        candidate: EntityCandidate,
        workspace_id: str,
        existing: Optional[list[EntityCandidate]] = None,
    ) -> ResolutionResult:
        """
        Resolve *candidate* and return a :class:`ResolutionResult`.

        If no existing candidates are provided (or no matches are found above
        threshold), a new singleton domain entity is created.
        """
        existing = existing or []
        matches = self.find_candidates(candidate, existing)

        # Filter to auto-resolve quality
        auto_matches = [m for m in matches if m.confidence >= CONFIDENCE_AUTO_RESOLVE]

        if auto_matches:
            # Use the highest-confidence match as the canonical domain URN
            best = auto_matches[0]
            domain_urn = best.domain_urn
            merged_urns = list({
                candidate.artifact_urn,
                best.candidate_a.artifact_urn,
                best.candidate_b.artifact_urn,
            })
            for m in auto_matches[1:]:
                merged_urns.append(m.candidate_b.artifact_urn)
            path = [m.tier for m in auto_matches]
            confidence = sum(m.confidence for m in auto_matches) / len(auto_matches)
            return ResolutionResult(
                domain_urn=domain_urn,
                merged_artifacts=list(dict.fromkeys(merged_urns)),  # dedup, order-preserve
                resolution_path=path,
                cross_source_confidence=confidence,
            )

        # No auto-resolve quality match → new singleton
        domain_urn = _derive_domain_urn(candidate, workspace_id)
        return ResolutionResult(
            domain_urn=domain_urn,
            merged_artifacts=[candidate.artifact_urn],
            resolution_path=[],
            cross_source_confidence=1.0,  # trivially certain — no cross-source merge
        )

    # ── Tier comparators ─────────────────────────────────────────────────────

    def _compare(
        self,
        a: EntityCandidate,
        b: EntityCandidate,
    ) -> Optional[ResolutionMatch]:
        """
        Walk the tier ladder and return the *first* (strongest) match above
        CONFIDENCE_SUGGEST, or None.
        """
        # Tier 1: EXPLICIT_LINK
        confidence = self._try_explicit_link(a, b)
        if confidence is not None:
            return self._make_match(a, b, ResolutionTier.EXPLICIT_LINK, confidence)

        # Tier 2: NAME_MATCH
        confidence = self._try_name_match(a, b)
        if confidence is not None:
            return self._make_match(a, b, ResolutionTier.NAME_MATCH, confidence)

        # Tier 3: SEMANTIC_EMBED (optional — skip if unavailable)
        confidence = self._try_semantic_embed(a, b)
        if confidence is not None:
            return self._make_match(a, b, ResolutionTier.SEMANTIC_EMBED, confidence)

        return None

    def _try_explicit_link(
        self,
        a: EntityCandidate,
        b: EntityCandidate,
    ) -> Optional[float]:
        """
        Return EXPLICIT_LINK confidence when both artifacts reference the same
        domain URN, or when one references the other's domain URN.
        """
        urn_a = a.explicit_domain_urn
        urn_b = b.explicit_domain_urn
        if urn_a and urn_b and urn_a == urn_b:
            return TIER_CONFIDENCE[ResolutionTier.EXPLICIT_LINK]
        if urn_a and urn_a == b.artifact_urn:
            return TIER_CONFIDENCE[ResolutionTier.EXPLICIT_LINK]
        if urn_b and urn_b == a.artifact_urn:
            return TIER_CONFIDENCE[ResolutionTier.EXPLICIT_LINK]
        return None

    def _try_name_match(
        self,
        a: EntityCandidate,
        b: EntityCandidate,
    ) -> Optional[float]:
        """Return NAME_MATCH confidence when normalized titles are equal."""
        if names_match(a.title, b.title):
            return TIER_CONFIDENCE[ResolutionTier.NAME_MATCH]
        return None

    def _try_semantic_embed(
        self,
        a: EntityCandidate,
        b: EntityCandidate,
    ) -> Optional[float]:
        """
        Return SEMANTIC_EMBED confidence when cosine similarity ≥ threshold.

        Silently returns None when sentence-transformers is unavailable.
        """
        try:
            if self._embed_matcher is None:
                self._embed_matcher = EmbedMatcher(threshold=self._embed_threshold)
            is_match, score = self._embed_matcher.matches(a.title, b.title)
            if is_match:
                # Map raw cosine score into [SEMANTIC_EMBED tier confidence range]
                # We cap at TIER_CONFIDENCE to avoid letting cosine=1.0 masquerade
                # as an explicit-link-strength signal.
                capped = min(score, TIER_CONFIDENCE[ResolutionTier.SEMANTIC_EMBED])
                if capped >= CONFIDENCE_SUGGEST:
                    return capped
        except EmbedMatcherUnavailable:
            log.debug("resolution.embed_matcher_unavailable", reason="sentence-transformers not installed")
        except Exception as exc:  # pragma: no cover
            log.warning("resolution.embed_matcher_error", error=str(exc))
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_match(
        a: EntityCandidate,
        b: EntityCandidate,
        tier: ResolutionTier,
        confidence: float,
    ) -> Optional[ResolutionMatch]:
        """Build a ResolutionMatch only if confidence clears CONFIDENCE_SUGGEST."""
        if confidence < CONFIDENCE_SUGGEST:
            return None

        # Pick domain URN: prefer explicit, then derive from 'a'
        domain_urn = (
            a.explicit_domain_urn
            or b.explicit_domain_urn
            or _derive_domain_urn(a, _workspace_from_urn(a.artifact_urn))
        )

        return ResolutionMatch(
            id=_make_match_id(a.artifact_urn, b.artifact_urn),
            candidate_a=a,
            candidate_b=b,
            tier=tier,
            confidence=confidence,
            domain_urn=domain_urn,
        )


def _workspace_from_urn(artifact_urn: str) -> str:
    """Extract workspace id from artifact URN or fall back to 'default'."""
    # source://notion/page/abc@workspace  →  workspace
    if "@" in artifact_urn:
        return artifact_urn.rsplit("@", 1)[-1]
    return "default"
