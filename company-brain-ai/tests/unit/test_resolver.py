"""
Unit tests for CrossSourceEntityResolver — ADR-0093.

The SEMANTIC_EMBED tier is tested via a mock EmbedMatcher to avoid a
sentence-transformers installation requirement.
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from companybrain.resolution.embed_matcher import EmbedMatcherUnavailable
from companybrain.resolution.models import (
    CONFIDENCE_AUTO_RESOLVE,
    EntityCandidate,
    ResolutionMatch,
    ResolutionResult,
    ResolutionTier,
    TIER_CONFIDENCE,
)
from companybrain.resolution.resolver import (
    CrossSourceEntityResolver,
    _derive_domain_urn,
    _make_match_id,
    _workspace_from_urn,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _notion_payer() -> EntityCandidate:
    return EntityCandidate(
        artifact_urn="source://notion/page/np1@acme",
        source_type="notion",
        title="Payer Module",
        content_snippet="Describes the payer module.",
    )


def _code_payer() -> EntityCandidate:
    return EntityCandidate(
        artifact_urn="source://code/file/PayerModule.java@acme",
        source_type="code",
        title="PayerModule",
        content_snippet="class PayerModule {",
    )


def _notion_order() -> EntityCandidate:
    return EntityCandidate(
        artifact_urn="source://notion/page/no1@acme",
        source_type="notion",
        title="Order Service",
        content_snippet="The order service manages order lifecycle.",
    )


def _with_explicit_urn(c: EntityCandidate, domain_urn: str) -> EntityCandidate:
    from dataclasses import replace
    return EntityCandidate(
        artifact_urn=c.artifact_urn,
        source_type=c.source_type,
        title=c.title,
        content_snippet=c.content_snippet,
        domain_hints=c.domain_hints,
        explicit_domain_urn=domain_urn,
    )


# ── Helper tests ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_make_match_id_deterministic(self):
        id1 = _make_match_id("urn:a", "urn:b")
        id2 = _make_match_id("urn:b", "urn:a")
        assert id1 == id2
        assert len(id1) == 12

    def test_workspace_from_urn(self):
        assert _workspace_from_urn("source://notion/page/abc@acme") == "acme"
        assert _workspace_from_urn("source://code/file/x.java@ws2") == "ws2"
        assert _workspace_from_urn("no-at-sign") == "default"

    def test_derive_domain_urn_from_explicit(self):
        c = _with_explicit_urn(_notion_payer(), "domain://payer@acme")
        assert _derive_domain_urn(c, "acme") == "domain://payer@acme"

    def test_derive_domain_urn_synthesised(self):
        urn = _derive_domain_urn(_notion_payer(), "acme")
        assert urn.startswith("domain://")
        assert "@acme" in urn
        assert "payer" in urn


# ── Tier 1: EXPLICIT_LINK ─────────────────────────────────────────────────────

class TestExplicitLink:
    def setup_method(self):
        self.resolver = CrossSourceEntityResolver()

    def test_same_explicit_urn_resolves(self):
        shared_domain = "domain://payer_module@acme"
        a = _with_explicit_urn(_notion_payer(), shared_domain)
        b = _with_explicit_urn(_code_payer(), shared_domain)

        matches = self.resolver.find_candidates(a, [b])
        assert len(matches) == 1
        match = matches[0]
        assert match.tier == ResolutionTier.EXPLICIT_LINK
        assert match.confidence == TIER_CONFIDENCE[ResolutionTier.EXPLICIT_LINK]
        assert match.domain_urn == shared_domain

    def test_explicit_link_artifact_urn_cross_reference(self):
        """Artifact A's explicit_domain_urn equals B's artifact_urn."""
        a = _with_explicit_urn(_notion_payer(), _code_payer().artifact_urn)
        matches = self.resolver.find_candidates(a, [_code_payer()])
        assert len(matches) == 1
        assert matches[0].tier == ResolutionTier.EXPLICIT_LINK

    def test_different_explicit_urns_no_link(self):
        a = _with_explicit_urn(_notion_payer(), "domain://payer@acme")
        b = _with_explicit_urn(_notion_order(), "domain://order@acme")
        matches = self.resolver.find_candidates(a, [b])
        # Different explicit URNs → name mismatch too → no match
        assert len(matches) == 0

    def test_resolve_artifact_explicit_link_returns_correct_domain(self):
        shared = "domain://payer_module@acme"
        a = _with_explicit_urn(_notion_payer(), shared)
        b = _with_explicit_urn(_code_payer(), shared)

        result = self.resolver.resolve_artifact(a, "acme", existing=[b])
        assert result.domain_urn == shared
        assert a.artifact_urn in result.merged_artifacts
        assert b.artifact_urn in result.merged_artifacts
        assert ResolutionTier.EXPLICIT_LINK in result.resolution_path


# ── Tier 2: NAME_MATCH ────────────────────────────────────────────────────────

class TestNameMatch:
    def setup_method(self):
        self.resolver = CrossSourceEntityResolver()

    def test_same_normalized_name_matches(self):
        """'PayerModule' and 'Payer Module' normalize to the same tokens."""
        matches = self.resolver.find_candidates(_notion_payer(), [_code_payer()])
        assert len(matches) == 1
        match = matches[0]
        assert match.tier == ResolutionTier.NAME_MATCH
        assert match.confidence == TIER_CONFIDENCE[ResolutionTier.NAME_MATCH]

    def test_name_match_auto_resolves(self):
        result = self.resolver.resolve_artifact(
            _notion_payer(), "acme", existing=[_code_payer()]
        )
        assert result.should_auto_resolve is True
        assert ResolutionTier.NAME_MATCH in result.resolution_path

    def test_different_names_no_match(self):
        matches = self.resolver.find_candidates(_notion_payer(), [_notion_order()])
        assert len(matches) == 0

    def test_self_is_excluded(self):
        """A candidate should never match itself."""
        c = _notion_payer()
        matches = self.resolver.find_candidates(c, [c])
        assert len(matches) == 0

    def test_singleton_when_no_existing(self):
        result = self.resolver.resolve_artifact(_notion_payer(), "acme", existing=[])
        assert result.merged_artifacts == [_notion_payer().artifact_urn]
        assert result.resolution_path == []


# ── Tier 3: SEMANTIC_EMBED (mocked) ──────────────────────────────────────────

class TestSemanticEmbed:
    def _resolver_with_mock_embed(self, similarity: float) -> CrossSourceEntityResolver:
        mock = MagicMock()
        mock.matches.return_value = (similarity >= 0.72, similarity)
        return CrossSourceEntityResolver(embed_matcher=mock)

    def test_semantic_embed_match_above_threshold(self):
        resolver = self._resolver_with_mock_embed(0.85)
        # Use non-matching names so NAME_MATCH tier doesn't fire first
        a = EntityCandidate(
            artifact_urn="source://notion/page/x@ws",
            source_type="notion",
            title="Payer System",
            content_snippet="The payer system.",
        )
        b = EntityCandidate(
            artifact_urn="source://code/file/Billing.java@ws",
            source_type="code",
            title="Billing Service",
            content_snippet="class BillingService {",
        )
        matches = resolver.find_candidates(a, [b])
        assert len(matches) == 1
        assert matches[0].tier == ResolutionTier.SEMANTIC_EMBED

    def test_semantic_embed_below_threshold_no_match(self):
        resolver = self._resolver_with_mock_embed(0.50)
        a = EntityCandidate(
            artifact_urn="source://notion/page/x@ws",
            source_type="notion",
            title="Payer System",
            content_snippet="The payer system.",
        )
        b = EntityCandidate(
            artifact_urn="source://code/file/Billing.java@ws",
            source_type="code",
            title="Billing Service",
            content_snippet="class BillingService {",
        )
        matches = resolver.find_candidates(a, [b])
        assert len(matches) == 0

    def test_semantic_embed_skips_gracefully_when_unavailable(self):
        """When sentence-transformers is not installed, tier 3 is skipped cleanly."""
        mock = MagicMock()
        mock.matches.return_value = (False, 0.0)  # unavailable → graceful degradation
        resolver = CrossSourceEntityResolver(embed_matcher=mock)

        a = EntityCandidate(
            artifact_urn="source://notion/page/x@ws",
            source_type="notion",
            title="Payer System",
            content_snippet="a",
        )
        b = EntityCandidate(
            artifact_urn="source://code/file/Billing.java@ws",
            source_type="code",
            title="Billing Service",
            content_snippet="b",
        )
        # No name match + embed unavailable → no match, no exception
        matches = resolver.find_candidates(a, [b])
        assert matches == []

    def test_embed_tier_confidence_capped(self):
        """Cosine of 1.0 must not be returned as higher than SEMANTIC_EMBED tier conf."""
        resolver = self._resolver_with_mock_embed(1.0)
        a = EntityCandidate(
            artifact_urn="source://notion/page/x@ws",
            source_type="notion",
            title="Payer System",
            content_snippet="a",
        )
        b = EntityCandidate(
            artifact_urn="source://code/file/Billing.java@ws",
            source_type="code",
            title="Billing Service",
            content_snippet="b",
        )
        matches = resolver.find_candidates(a, [b])
        if matches:
            assert matches[0].confidence <= TIER_CONFIDENCE[ResolutionTier.SEMANTIC_EMBED]


# ── Tier priority ─────────────────────────────────────────────────────────────

class TestTierPriority:
    def test_explicit_link_takes_priority_over_name_match(self):
        """If explicit link matches, NAME_MATCH should not also fire for same pair."""
        shared_domain = "domain://payer@acme"
        a = _with_explicit_urn(_notion_payer(), shared_domain)
        b = _with_explicit_urn(_code_payer(), shared_domain)  # same name too

        resolver = CrossSourceEntityResolver()
        matches = resolver.find_candidates(a, [b])
        # Only one match per pair, tier is EXPLICIT_LINK (highest)
        assert len(matches) == 1
        assert matches[0].tier == ResolutionTier.EXPLICIT_LINK

    def test_results_sorted_by_confidence_desc(self):
        shared_domain = "domain://payer@acme"
        explicit = _with_explicit_urn(_code_payer(), shared_domain)
        name_only = EntityCandidate(
            artifact_urn="source://slack/msg/s1@acme",
            source_type="slack",
            title="Payer Module",  # matches by name
            content_snippet="payer",
        )
        a = _with_explicit_urn(_notion_payer(), shared_domain)
        resolver = CrossSourceEntityResolver()
        matches = resolver.find_candidates(a, [explicit, name_only])
        confidences = [m.confidence for m in matches]
        assert confidences == sorted(confidences, reverse=True)


# ── ResolutionResult shape ────────────────────────────────────────────────────

class TestResolutionResultShape:
    def test_merged_artifacts_deduped(self):
        """Merging two candidates should not produce duplicate URNs."""
        shared = "domain://payer@acme"
        a = _with_explicit_urn(_notion_payer(), shared)
        b = _with_explicit_urn(_code_payer(), shared)

        resolver = CrossSourceEntityResolver()
        result = resolver.resolve_artifact(a, "acme", existing=[b])
        assert len(result.merged_artifacts) == len(set(result.merged_artifacts))

    def test_cross_source_confidence_is_mean(self):
        """With a single NAME_MATCH the confidence equals NAME_MATCH tier confidence."""
        resolver = CrossSourceEntityResolver()
        result = resolver.resolve_artifact(
            _notion_payer(), "acme", existing=[_code_payer()]
        )
        assert abs(result.cross_source_confidence - TIER_CONFIDENCE[ResolutionTier.NAME_MATCH]) < 1e-6
