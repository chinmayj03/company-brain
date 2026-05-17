"""
Unit tests for persona router — ADR-0079 M3.

Tests:
- Explicit @persona token detection (all supported tokens)
- Caller-supplied persona param normalization
- Keyword classifier routing (PM / Dev / VP)
- Shape match: returns highest-overlap shape
- Shape match: falls through when confidence < 0.5
- Route(): full pipeline works end-to-end
- Persona inference source is correctly reported
"""
from __future__ import annotations

import pytest

from companybrain.personas.router import (
    MATCH_THRESHOLD,
    RouterResult,
    infer_persona,
    match_shape,
    route,
)
from companybrain.personas.templates import load_all_templates


@pytest.fixture(scope="module")
def shapes():
    return load_all_templates(force_reload=True)


# ── infer_persona tests ────────────────────────────────────────────────────────

class TestInferPersona:
    def test_explicit_at_pm(self):
        persona, source = infer_persona("@pm where is feature X")
        assert persona == "pm"
        assert source == "explicit"

    def test_explicit_at_dev(self):
        persona, source = infer_persona("@dev blast radius of the service")
        assert persona == "dev"
        assert source == "explicit"

    def test_explicit_at_developer(self):
        persona, source = infer_persona("@developer similar implementations")
        assert persona == "dev"
        assert source == "explicit"

    def test_explicit_at_vp(self):
        persona, source = infer_persona("@vp where are we drifting")
        assert persona == "vp_eng"
        assert source == "explicit"

    def test_explicit_at_vp_eng(self):
        persona, source = infer_persona("@vp_eng debt hotspots")
        assert persona == "vp_eng"
        assert source == "explicit"

    def test_param_pm(self):
        persona, source = infer_persona("where is the feature", persona_param="pm")
        assert persona == "pm"
        assert source == "param"

    def test_param_dev_alias(self):
        persona, source = infer_persona("some question", persona_param="developer")
        assert persona == "dev"
        assert source == "param"

    def test_param_vp_alias(self):
        persona, source = infer_persona("some question", persona_param="vp")
        assert persona == "vp_eng"
        assert source == "param"

    def test_param_unknown_falls_to_keyword(self):
        persona, source = infer_persona("where is the feature progress", persona_param="invalid_persona")
        # Falls through to keyword classifier
        assert source in ("keyword", "default")

    def test_keyword_pm_ship(self):
        persona, source = infer_persona("can we ship this feature this quarter")
        assert persona == "pm"
        assert source == "keyword"

    def test_keyword_pm_feature(self):
        persona, source = infer_persona("what's the status of the feature")
        assert persona == "pm"
        assert source == "keyword"

    def test_keyword_dev_blast_radius(self):
        persona, source = infer_persona("blast radius of changing the eligibility service")
        assert persona == "dev"
        assert source == "keyword"

    def test_keyword_dev_implementation(self):
        persona, source = infer_persona("how do we implement retry logic")
        assert persona == "dev"
        assert source == "keyword"

    def test_keyword_vp_drift(self):
        persona, source = infer_persona("where are we drifting from the architecture")
        assert persona == "vp_eng"
        assert source == "keyword"

    def test_keyword_vp_debt(self):
        persona, source = infer_persona("show me the worst tech debt")
        assert persona == "vp_eng"
        assert source == "keyword"

    def test_keyword_vp_bus_factor(self):
        persona, source = infer_persona("where is our bus factor risk")
        assert persona == "vp_eng"
        assert source == "keyword"

    def test_default_when_no_match(self):
        persona, source = infer_persona("hello world xyzzy")
        assert persona == "dev"
        assert source == "default"

    def test_explicit_takes_precedence_over_param(self):
        # @pm wins over param="dev"
        persona, source = infer_persona("@pm where is the feature", persona_param="dev")
        assert persona == "pm"
        assert source == "explicit"


# ── match_shape tests ─────────────────────────────────────────────────────────

class TestMatchShape:
    def test_blast_radius_query_matches_dev_blast_radius(self, shapes):
        shape, confidence = match_shape(
            "what breaks if I change the eligibility service",
            "dev",
            shapes,
        )
        assert shape is not None
        assert shape.id == "dev.blast_radius"
        assert confidence >= MATCH_THRESHOLD

    def test_feature_progress_query_matches_pm_feature_progress(self, shapes):
        shape, confidence = match_shape(
            "where is the prior auth feature right now",
            "pm",
            shapes,
        )
        assert shape is not None
        assert shape.id == "pm.feature_progress"
        assert confidence >= MATCH_THRESHOLD

    def test_drift_query_matches_vp_drift_trend(self, shapes):
        shape, confidence = match_shape(
            "where is the Payer area drifting from the architecture",
            "vp_eng",
            shapes,
        )
        assert shape is not None
        assert shape.id == "vp.drift_trend"
        assert confidence >= MATCH_THRESHOLD

    def test_low_confidence_returns_none(self, shapes):
        # Completely unrelated query — should not match any shape
        shape, confidence = match_shape(
            "pink elephants dance magically on rooftops",
            "dev",
            shapes,
        )
        assert shape is None
        assert confidence < MATCH_THRESHOLD

    def test_empty_persona_returns_none(self, shapes):
        shape, confidence = match_shape("any query", "nonexistent_persona", shapes)
        assert shape is None
        assert confidence == 0.0

    def test_domain_meaning_query_matches(self, shapes):
        shape, confidence = match_shape(
            "what does Payer mean in this codebase",
            "dev",
            shapes,
        )
        assert shape is not None
        assert shape.id == "dev.domain_meaning_of_entity"

    def test_debt_hotspots_matches(self, shapes):
        shape, confidence = match_shape(
            "where is our worst tech debt",
            "vp_eng",
            shapes,
        )
        assert shape is not None
        assert shape.id == "vp.debt_hotspots"

    def test_who_owns_query_matches(self, shapes):
        shape, confidence = match_shape(
            "who owns the claims processing module",
            "dev",
            shapes,
        )
        assert shape is not None
        assert shape.id == "dev.who_owns_this_area"


# ── route() integration tests ─────────────────────────────────────────────────

class TestRoute:
    def test_route_returns_router_result(self, shapes):
        result = route("blast radius of the service", shapes=shapes)
        assert isinstance(result, RouterResult)

    def test_route_pm_query_no_param(self, shapes):
        result = route("where is the prior auth feature", shapes=shapes)
        assert result.persona == "pm"
        assert result.shape is not None
        assert result.shape.id == "pm.feature_progress"
        assert not result.fell_through_to_generic

    def test_route_dev_explicit_token(self, shapes):
        result = route("@dev blast radius of changing the payer service", shapes=shapes)
        assert result.persona == "dev"
        assert result.persona_source == "explicit"

    def test_route_vp_from_param(self, shapes):
        result = route("area health summary please", persona_param="vp_eng", shapes=shapes)
        assert result.persona == "vp_eng"
        assert result.persona_source == "param"

    def test_route_fell_through_on_ambiguous_query(self, shapes):
        result = route("xyzzy plugh teleporter", shapes=shapes)
        assert result.fell_through_to_generic is True
        assert result.shape is None
        assert result.match_confidence < MATCH_THRESHOLD

    def test_route_loads_templates_automatically(self):
        # route() with no shapes arg loads from disk
        result = route("blast radius of the service")
        assert isinstance(result, RouterResult)
        # Should have found something for this dev query
        assert result.persona == "dev"

    def test_route_generic_fallback_still_returns_persona(self, shapes):
        result = route("some completely random ambiguous thing", shapes=shapes)
        assert result.persona in ("dev", "pm", "vp_eng", "cs", "cfo", "ceo")
        # Generic fallback: no shape but we still know the persona
        assert result.fell_through_to_generic is True
