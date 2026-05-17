"""
Template loader — reads YAML files from this package directory and validates
every shape against QuestionShape at startup. Invalid shapes fail loudly.

Usage:
    from companybrain.personas.templates import load_all_templates, get_shape

    shapes = load_all_templates()          # returns dict[str, QuestionShape]
    shape  = get_shape("dev.blast_radius") # returns QuestionShape or None
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

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

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent

# Module-level cache: loaded once on first call.
_SHAPE_CACHE: dict[str, QuestionShape] | None = None


def load_all_templates(force_reload: bool = False) -> dict[str, QuestionShape]:
    """
    Load all YAML template files and return a mapping of shape_id → QuestionShape.

    Validation errors raise ValueError immediately so misconfigured shapes are
    caught at startup rather than silently returning wrong answers at query time.
    """
    global _SHAPE_CACHE
    if _SHAPE_CACHE is not None and not force_reload:
        return _SHAPE_CACHE

    shapes: dict[str, QuestionShape] = {}
    yaml_files = sorted(_TEMPLATES_DIR.glob("*.yaml"))

    if not yaml_files:
        log.warning("[persona-templates] No YAML files found in %s", _TEMPLATES_DIR)
        _SHAPE_CACHE = shapes
        return shapes

    errors: list[str] = []
    for yaml_path in yaml_files:
        try:
            doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{yaml_path.name}: YAML parse error — {exc}")
            continue

        if not isinstance(doc, dict) or "shapes" not in doc:
            errors.append(f"{yaml_path.name}: missing top-level 'shapes' key")
            continue

        for raw in doc.get("shapes", []) or []:
            try:
                shape = _parse_shape(raw)
            except Exception as exc:
                errors.append(f"{yaml_path.name}: failed to parse shape {raw.get('id', '?')} — {exc}")
                continue

            validation_errors = shape.validate()
            if validation_errors:
                errors.extend(
                    [f"{yaml_path.name}: {err}" for err in validation_errors]
                )
                continue

            if shape.id in shapes:
                errors.append(
                    f"{yaml_path.name}: duplicate shape id {shape.id!r} "
                    f"(already defined)"
                )
                continue

            shapes[shape.id] = shape
            log.debug("[persona-templates] Loaded shape: %s", shape.id)

    if errors:
        msg = (
            f"[persona-templates] {len(errors)} validation error(s) in template files:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
        raise ValueError(msg)

    log.info("[persona-templates] Loaded %d shapes from %d files", len(shapes), len(yaml_files))
    _SHAPE_CACHE = shapes
    return shapes


def get_shape(shape_id: str) -> Optional[QuestionShape]:
    """Return a loaded QuestionShape by id, or None if not found."""
    return load_all_templates().get(shape_id)


def get_shapes_for_persona(persona: str) -> list[QuestionShape]:
    """Return all shapes for a given persona."""
    return [s for s in load_all_templates().values() if s.persona == persona]


# ── Private parser ────────────────────────────────────────────────────────────


def _parse_shape(raw: dict) -> QuestionShape:
    """Convert a raw dict (from YAML) into a typed QuestionShape."""

    def _parse_fallback(d: dict | None) -> SparseFallback:
        if not d:
            return SparseFallback(strategy="generic_retrieval")
        return SparseFallback(
            strategy=d.get("strategy", "generic_retrieval"),
            message=d.get("message", ""),
        )

    def _parse_signal(d: dict) -> SignalSpec:
        return SignalSpec(
            name=d["name"],
            source_views=d.get("source_views", []),
            required_confidence=float(d.get("required_confidence", 0.6)),
            sparse_fallback=_parse_fallback(d.get("sparse_fallback")),
        )

    def _parse_retrieval(d: dict | None) -> RetrievalRecipe:
        if not d:
            return RetrievalRecipe(strategy="hybrid_search")
        return RetrievalRecipe(
            strategy=d.get("strategy", "hybrid_search"),
            hints=d.get("hints", {}),
            qdrant_index=d.get("qdrant_index", "default"),
            evidence_budget_tokens=int(d.get("evidence_budget_tokens", 4000)),
        )

    def _parse_section(d: dict) -> SectionSpec:
        return SectionSpec(
            name=d["name"],
            required=bool(d.get("required", True)),
            description=d.get("description", ""),
        )

    def _parse_answer_format(d: dict | None) -> AnswerFormat:
        if not d:
            return AnswerFormat()
        sections = [_parse_section(s) for s in (d.get("sections") or [])]
        return AnswerFormat(
            sections=sections,
            citation_min=int(d.get("citation_min", 1)),
            citation_max=int(d.get("citation_max", 4)),
            citation_style=d.get("citation_style", "section_level"),
            chart_types=d.get("chart_types", []),
        )

    def _parse_fallback_policy(d: dict | None) -> FallbackPolicy:
        if not d:
            return FallbackPolicy()
        return FallbackPolicy(
            on_no_match=d.get("on_no_match", "generic_retrieval"),
            on_sparse_signals=d.get("on_sparse_signals", "partial_answer"),
            min_signal_coverage=float(d.get("min_signal_coverage", 0.3)),
        )

    return QuestionShape(
        id=raw["id"],
        persona=raw["persona"],
        intent=raw.get("intent", ""),
        intent_examples=raw.get("intent_examples", []),
        required_signals=[_parse_signal(s) for s in (raw.get("required_signals") or [])],
        optional_signals=[_parse_signal(s) for s in (raw.get("optional_signals") or [])],
        retrieval_recipe=_parse_retrieval(raw.get("retrieval_recipe")),
        answer_format=_parse_answer_format(raw.get("answer_format")),
        evidence_budget_tokens=int(raw.get("evidence_budget_tokens", 4000)),
        fallback_behavior=_parse_fallback_policy(raw.get("fallback_behavior")),
        refinement_metadata=RefinementMeta(),
    )
