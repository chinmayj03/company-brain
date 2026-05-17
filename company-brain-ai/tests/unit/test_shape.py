"""
Unit tests for QuestionShape schema — ADR-0079 M1.

Tests:
- Shape dataclass construction and field defaults
- Shape.validate() error detection
- Template loader: loads all 15 shapes without error
- Template loader: invalid YAML raises ValueError at startup (not silently)
- Duplicate shape id raises ValueError
- get_shape() and get_shapes_for_persona() convenience queries
"""
from __future__ import annotations

import pytest
import yaml

from companybrain.personas.shape import (
    AnswerFormat,
    FallbackPolicy,
    QuestionShape,
    RefinementMeta,
    RetrievalRecipe,
    SectionSpec,
    SignalSpec,
    SparseFallback,
)
from companybrain.personas.templates import get_shape, get_shapes_for_persona, load_all_templates


# ── QuestionShape dataclass tests ─────────────────────────────────────────────

class TestQuestionShape:
    def test_minimal_valid_shape(self):
        shape = QuestionShape(
            id="dev.test_shape",
            persona="dev",
            intent="Test intent",
            intent_examples=["example query"],
        )
        assert shape.id == "dev.test_shape"
        assert shape.persona == "dev"
        assert shape.intent == "Test intent"
        assert shape.intent_examples == ["example query"]
        # Check defaults
        assert shape.evidence_budget_tokens == 4000
        assert isinstance(shape.answer_format, AnswerFormat)
        assert isinstance(shape.fallback_behavior, FallbackPolicy)
        assert isinstance(shape.refinement_metadata, RefinementMeta)

    def test_validate_missing_id(self):
        shape = QuestionShape(id="", persona="dev", intent="Test", intent_examples=["x"])
        errors = shape.validate()
        assert any("id is required" in e for e in errors)

    def test_validate_id_missing_dot(self):
        shape = QuestionShape(id="blast_radius", persona="dev", intent="Test", intent_examples=["x"])
        errors = shape.validate()
        assert any("persona>.<shape>" in e for e in errors)

    def test_validate_no_examples(self):
        shape = QuestionShape(id="dev.test", persona="dev", intent="Test", intent_examples=[])
        errors = shape.validate()
        assert any("intent_example" in e for e in errors)

    def test_validate_passes_for_well_formed(self):
        shape = QuestionShape(
            id="pm.feature_progress",
            persona="pm",
            intent="Track feature progress",
            intent_examples=["where is the feature", "status of feature X"],
        )
        assert shape.validate() == []

    def test_signal_spec_defaults(self):
        sig = SignalSpec(name="feature_entity")
        assert sig.source_views == []
        assert sig.required_confidence == 0.6
        assert sig.sparse_fallback.strategy == "generic_retrieval"

    def test_retrieval_recipe_defaults(self):
        recipe = RetrievalRecipe(strategy="hybrid_search")
        assert recipe.qdrant_index == "default"
        assert recipe.evidence_budget_tokens == 4000
        assert recipe.hints == {}

    def test_fallback_policy_defaults(self):
        fp = FallbackPolicy()
        assert fp.on_no_match == "generic_retrieval"
        assert fp.on_sparse_signals == "partial_answer"
        assert fp.min_signal_coverage == 0.3

    def test_section_spec(self):
        sec = SectionSpec(name="blast_radius", required=True, description="Blast radius")
        assert sec.name == "blast_radius"
        assert sec.required is True

    def test_sparse_fallback(self):
        fb = SparseFallback(strategy="skip_section", message="No data")
        assert fb.strategy == "skip_section"
        assert fb.message == "No data"


# ── Template loader tests ─────────────────────────────────────────────────────

class TestTemplateLoader:
    def test_load_all_templates_returns_15_shapes(self):
        shapes = load_all_templates(force_reload=True)
        assert len(shapes) == 15, (
            f"Expected 15 shapes, got {len(shapes)}: {list(shapes.keys())}"
        )

    def test_all_expected_shape_ids_present(self):
        shapes = load_all_templates()
        expected_ids = {
            # Developer
            "dev.blast_radius",
            "dev.similar_implementations",
            "dev.domain_meaning_of_entity",
            "dev.why_was_this_decided",
            "dev.who_owns_this_area",
            # PM
            "pm.feature_progress",
            "pm.entities_affected_by_feature",
            "pm.open_decisions_for_feature",
            "pm.roadmap_status",
            "pm.customer_promise_lookup",
            # VP Eng
            "vp.drift_trend",
            "vp.debt_hotspots",
            "vp.area_health_summary",
            "vp.bus_factor_per_area",
            "vp.recent_changes_to_area",
        }
        missing = expected_ids - set(shapes.keys())
        assert not missing, f"Missing shape IDs: {missing}"

    def test_all_shapes_have_valid_personas(self):
        shapes = load_all_templates()
        valid_personas = {"dev", "pm", "cs", "vp_eng", "cfo", "ceo"}
        for shape_id, shape in shapes.items():
            assert shape.persona in valid_personas, (
                f"{shape_id}: invalid persona {shape.persona!r}"
            )

    def test_all_shapes_have_intent_examples(self):
        shapes = load_all_templates()
        for shape_id, shape in shapes.items():
            assert len(shape.intent_examples) >= 3, (
                f"{shape_id}: only {len(shape.intent_examples)} intent_examples"
            )

    def test_all_shapes_pass_validation(self):
        shapes = load_all_templates()
        for shape_id, shape in shapes.items():
            errors = shape.validate()
            assert not errors, f"{shape_id}: validation errors: {errors}"

    def test_all_shapes_have_answer_sections(self):
        shapes = load_all_templates()
        for shape_id, shape in shapes.items():
            assert len(shape.answer_format.sections) >= 1, (
                f"{shape_id}: no answer sections defined"
            )

    def test_all_shapes_have_citations_section(self):
        shapes = load_all_templates()
        for shape_id, shape in shapes.items():
            section_names = [s.name for s in shape.answer_format.sections]
            assert "citations" in section_names, (
                f"{shape_id}: missing required 'citations' section"
            )

    def test_get_shape_returns_correct_shape(self):
        shape = get_shape("dev.blast_radius")
        assert shape is not None
        assert shape.id == "dev.blast_radius"
        assert shape.persona == "dev"

    def test_get_shape_returns_none_for_unknown(self):
        shape = get_shape("unknown.nonexistent")
        assert shape is None

    def test_get_shapes_for_persona_dev(self):
        dev_shapes = get_shapes_for_persona("dev")
        assert len(dev_shapes) == 5
        for s in dev_shapes:
            assert s.persona == "dev"

    def test_get_shapes_for_persona_pm(self):
        pm_shapes = get_shapes_for_persona("pm")
        assert len(pm_shapes) == 5
        for s in pm_shapes:
            assert s.persona == "pm"

    def test_get_shapes_for_persona_vp_eng(self):
        vp_shapes = get_shapes_for_persona("vp_eng")
        assert len(vp_shapes) == 5
        for s in vp_shapes:
            assert s.persona == "vp_eng"

    def test_loader_raises_on_invalid_yaml(self, tmp_path, monkeypatch):
        """A YAML file with invalid shape data raises ValueError at load time."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("shapes:\n  - id: ''\n    persona: dev\n    intent: ''\n    intent_examples: []\n")

        from companybrain.personas import templates as tmod
        orig_dir = tmod._TEMPLATES_DIR
        monkeypatch.setattr(tmod, "_TEMPLATES_DIR", tmp_path)
        monkeypatch.setattr(tmod, "_SHAPE_CACHE", None)

        with pytest.raises(ValueError, match="validation error"):
            tmod.load_all_templates(force_reload=True)

        # Restore
        monkeypatch.setattr(tmod, "_TEMPLATES_DIR", orig_dir)
        monkeypatch.setattr(tmod, "_SHAPE_CACHE", None)

    def test_loader_raises_on_duplicate_ids(self, tmp_path, monkeypatch):
        """Duplicate shape IDs raise ValueError."""
        dup_yaml = tmp_path / "dup.yaml"
        dup_yaml.write_text(yaml.dump({
            "shapes": [
                {
                    "id": "dev.dup_shape",
                    "persona": "dev",
                    "intent": "First",
                    "intent_examples": ["example one"],
                },
                {
                    "id": "dev.dup_shape",
                    "persona": "dev",
                    "intent": "Second",
                    "intent_examples": ["example two"],
                },
            ]
        }))

        from companybrain.personas import templates as tmod
        orig_dir = tmod._TEMPLATES_DIR
        monkeypatch.setattr(tmod, "_TEMPLATES_DIR", tmp_path)
        monkeypatch.setattr(tmod, "_SHAPE_CACHE", None)

        with pytest.raises(ValueError, match="duplicate"):
            tmod.load_all_templates(force_reload=True)

        monkeypatch.setattr(tmod, "_TEMPLATES_DIR", orig_dir)
        monkeypatch.setattr(tmod, "_SHAPE_CACHE", None)
