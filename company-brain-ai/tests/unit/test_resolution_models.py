"""
Unit tests for resolution data models — ADR-0093.
"""
import pytest

from companybrain.resolution.models import (
    CONFIDENCE_AUTO_RESOLVE,
    CONFIDENCE_SUGGEST,
    TIER_CONFIDENCE,
    EntityCandidate,
    ResolutionMatch,
    ResolutionResult,
    ResolutionTier,
)


# ── EntityCandidate ───────────────────────────────────────────────────────────

class TestEntityCandidate:
    def test_basic_construction(self):
        c = EntityCandidate(
            artifact_urn="source://notion/page/abc123@acme",
            source_type="notion",
            title="Payer Module",
            content_snippet="The payer module handles billing.",
        )
        assert c.artifact_urn == "source://notion/page/abc123@acme"
        assert c.source_type == "notion"
        assert c.title == "Payer Module"
        assert c.domain_hints == []
        assert c.explicit_domain_urn is None

    def test_with_explicit_domain_urn(self):
        c = EntityCandidate(
            artifact_urn="source://code/file/PayerModule.java@acme",
            source_type="code",
            title="PayerModule",
            content_snippet="class PayerModule {",
            domain_hints=["payer", "billing"],
            explicit_domain_urn="domain://payer_module@acme",
        )
        assert c.explicit_domain_urn == "domain://payer_module@acme"
        assert c.domain_hints == ["payer", "billing"]

    def test_domain_hints_defaults_to_empty_list(self):
        c = EntityCandidate(
            artifact_urn="source://slack/message/m1@ws",
            source_type="slack",
            title="Payer discussion",
            content_snippet="We need to fix the payer module.",
        )
        assert isinstance(c.domain_hints, list)
        assert len(c.domain_hints) == 0


# ── ResolutionTier & TIER_CONFIDENCE ─────────────────────────────────────────

class TestResolutionTier:
    def test_all_tiers_have_confidence(self):
        for tier in ResolutionTier:
            assert tier in TIER_CONFIDENCE
            assert 0.0 < TIER_CONFIDENCE[tier] <= 1.0

    def test_human_confirmed_is_1(self):
        assert TIER_CONFIDENCE[ResolutionTier.HUMAN_CONFIRMED] == 1.0

    def test_explicit_link_above_auto_resolve(self):
        assert TIER_CONFIDENCE[ResolutionTier.EXPLICIT_LINK] >= CONFIDENCE_AUTO_RESOLVE

    def test_name_match_above_auto_resolve(self):
        assert TIER_CONFIDENCE[ResolutionTier.NAME_MATCH] >= CONFIDENCE_AUTO_RESOLVE

    def test_semantic_embed_above_suggest(self):
        assert TIER_CONFIDENCE[ResolutionTier.SEMANTIC_EMBED] >= CONFIDENCE_SUGGEST

    def test_tier_values_are_strings(self):
        assert ResolutionTier.EXPLICIT_LINK.value == "explicit_link"
        assert ResolutionTier.NAME_MATCH.value == "name_match"
        assert ResolutionTier.SEMANTIC_EMBED.value == "semantic_embed"
        assert ResolutionTier.HUMAN_CONFIRMED.value == "human_confirmed"


# ── ResolutionMatch ───────────────────────────────────────────────────────────

class TestResolutionMatch:
    def _make_pair(self):
        a = EntityCandidate(
            artifact_urn="source://notion/page/p1@ws",
            source_type="notion",
            title="Payer Module",
            content_snippet="Notion page about payer module.",
        )
        b = EntityCandidate(
            artifact_urn="source://code/file/PayerModule.java@ws",
            source_type="code",
            title="PayerModule",
            content_snippet="class PayerModule {",
        )
        return a, b

    def test_construction(self):
        a, b = self._make_pair()
        match = ResolutionMatch(
            id="abc123def456",
            candidate_a=a,
            candidate_b=b,
            tier=ResolutionTier.NAME_MATCH,
            confidence=0.82,
            domain_urn="domain://payer_module@ws",
        )
        assert match.id == "abc123def456"
        assert match.tier == ResolutionTier.NAME_MATCH
        assert match.confidence == 0.82
        assert match.domain_urn == "domain://payer_module@ws"
        assert match.status == "pending"

    def test_default_status_is_pending(self):
        a, b = self._make_pair()
        match = ResolutionMatch(
            id="x",
            candidate_a=a,
            candidate_b=b,
            tier=ResolutionTier.EXPLICIT_LINK,
            confidence=0.95,
            domain_urn="domain://payer_module@ws",
        )
        assert match.status == "pending"

    def test_status_can_be_set(self):
        a, b = self._make_pair()
        match = ResolutionMatch(
            id="x",
            candidate_a=a,
            candidate_b=b,
            tier=ResolutionTier.HUMAN_CONFIRMED,
            confidence=1.0,
            domain_urn="domain://payer_module@ws",
            status="confirmed",
        )
        assert match.status == "confirmed"


# ── ResolutionResult ──────────────────────────────────────────────────────────

class TestResolutionResult:
    def test_should_auto_resolve_high_confidence(self):
        result = ResolutionResult(
            domain_urn="domain://payer_module@ws",
            merged_artifacts=["source://notion/page/p1@ws", "source://code/file/PM.java@ws"],
            resolution_path=[ResolutionTier.NAME_MATCH],
            cross_source_confidence=0.82,
        )
        assert result.should_auto_resolve is True
        assert result.should_suggest is False

    def test_should_suggest_mid_confidence(self):
        result = ResolutionResult(
            domain_urn="domain://payer_module@ws",
            merged_artifacts=["source://notion/page/p1@ws"],
            resolution_path=[ResolutionTier.SEMANTIC_EMBED],
            cross_source_confidence=0.70,
        )
        assert result.should_auto_resolve is False
        assert result.should_suggest is True

    def test_below_suggest_threshold(self):
        result = ResolutionResult(
            domain_urn="domain://payer_module@ws",
            merged_artifacts=["source://notion/page/p1@ws"],
            resolution_path=[],
            cross_source_confidence=0.50,
        )
        assert result.should_auto_resolve is False
        assert result.should_suggest is False

    def test_at_exactly_auto_resolve_threshold(self):
        result = ResolutionResult(
            domain_urn="domain://x@ws",
            merged_artifacts=[],
            resolution_path=[],
            cross_source_confidence=CONFIDENCE_AUTO_RESOLVE,
        )
        assert result.should_auto_resolve is True

    def test_at_exactly_suggest_threshold(self):
        result = ResolutionResult(
            domain_urn="domain://x@ws",
            merged_artifacts=[],
            resolution_path=[],
            cross_source_confidence=CONFIDENCE_SUGGEST,
        )
        assert result.should_suggest is True
        assert result.should_auto_resolve is False
