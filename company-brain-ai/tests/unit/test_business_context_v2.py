"""
ADR-0060 — unit/regression tests for BusinessContext v2.

Covers:
  • dataclass shape (7 new fields + schema_version)
  • few-shot library size budget + structural invariants
  • prompt assembly determinism and rubric coverage
  • v2 payload-to-BusinessContext builder (happy + silent-default paths)
  • v1 → v2 migration (scan, inplace, idempotent re-run)
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, fields
from pathlib import Path

import pytest

from companybrain.models.entities import BusinessContext, ExtractedEntity
from companybrain.pipeline.business_context_v2_prompt import (
    ANTI_PATTERN_CATALOG,
    HARD_RULES_RUBRIC,
    build_system_prompt,
)
from companybrain.pipeline.context_synthesizer import _bc_from_v2_payload
from companybrain.pipeline.few_shot_library import (
    EXAMPLES,
    render_for_prompt,
    serialised_size,
)
from companybrain.cli_helpers.upgrade_business_context import upgrade_business_context


# ── BusinessContext dataclass shape ─────────────────────────────────────────


def test_business_context_has_seven_new_v2_fields():
    """ADR-0060 D1 — the seven typed engineering-rigour fields must exist."""
    field_names = {f.name for f in fields(BusinessContext)}
    for required in (
        "is_idempotent",
        "null_handling",
        "transaction_mode",
        "anti_patterns",
        "engineering_notes",
        "performance_class",
        "security_class",
    ):
        assert required in field_names, f"missing v2 field: {required}"


def test_schema_version_defaults_to_one_for_back_compat():
    """A bare BusinessContext (only required fields) reads as v1 so old
    data paths can't be silently re-tagged."""
    bc = BusinessContext(
        entity_external_id="r/f::X",
        purpose="",
        history_summary="",
        invariants=[],
        change_risk="LOW",
        change_risk_reason="",
        source_confidence="low",
    )
    assert bc.schema_version == 1
    assert bc.is_idempotent is None
    assert bc.null_handling == {}
    assert bc.anti_patterns == []


# ── Few-shot library invariants ─────────────────────────────────────────────


def test_few_shot_library_fits_cache_budget():
    """ADR-0060 §D2 — total serialised library MUST stay below 6 KB so it
    fits inside a single prompt-cache breakpoint."""
    assert serialised_size() < 6_000
    assert len(render_for_prompt().encode("utf-8")) < 6_000


def test_few_shot_library_has_thirty_examples():
    assert len(EXAMPLES) == 30


def test_every_example_has_input_and_output_keys():
    for idx, ex in enumerate(EXAMPLES):
        assert set(ex.keys()) == {"i", "o"}, f"example {idx} keys: {ex.keys()}"
        assert isinstance(ex["i"], str) and ex["i"], f"example {idx} input empty"
        assert isinstance(ex["o"], dict) and ex["o"], f"example {idx} output empty"


def test_every_example_only_uses_known_v2_fields():
    allowed = {
        "is_idempotent",
        "null_handling",
        "transaction_mode",
        "anti_patterns",
        "engineering_notes",
        "performance_class",
        "security_class",
    }
    for idx, ex in enumerate(EXAMPLES):
        unknown = set(ex["o"]) - allowed
        assert not unknown, f"example {idx} uses unknown v2 fields: {unknown}"


def test_every_anti_pattern_used_is_in_catalog():
    """The rubric promises a closed catalog of named anti-patterns. The
    few-shot examples MUST only use names from that catalog or the model
    will learn to invent tags."""
    seen: set[str] = set()
    for ex in EXAMPLES:
        for ap in ex["o"].get("anti_patterns", []) or []:
            seen.add(ap)
    unknown = seen - set(ANTI_PATTERN_CATALOG)
    assert not unknown, f"anti-patterns not in catalog: {unknown}"


def test_render_for_prompt_is_jsonl_compatible():
    """Each line MUST be a stand-alone JSON object so the model can scan
    the block as a lookup table."""
    rendered = render_for_prompt()
    lines = rendered.strip().splitlines()
    assert len(lines) == len(EXAMPLES)
    for line in lines:
        json.loads(line)  # raises on malformed line


# ── Prompt assembly ─────────────────────────────────────────────────────────


def test_build_system_prompt_is_deterministic():
    """Cache hits depend on byte-identical system prompts across calls."""
    p1 = build_system_prompt()
    p2 = build_system_prompt()
    assert p1 == p2


def test_prompt_rubric_lists_every_anti_pattern():
    for ap in ANTI_PATTERN_CATALOG:
        assert ap in HARD_RULES_RUBRIC, f"rubric missing anti-pattern: {ap}"


def test_prompt_mentions_schema_version_two():
    prompt = build_system_prompt()
    assert "schema_version" in prompt
    assert '"schema_version": 2' in prompt or "schema_version=2" in prompt


def test_prompt_contains_all_seven_v2_field_names():
    prompt = build_system_prompt()
    for fld in (
        "is_idempotent",
        "null_handling",
        "transaction_mode",
        "anti_patterns",
        "engineering_notes",
        "performance_class",
        "security_class",
    ):
        assert fld in prompt, f"prompt missing field rubric: {fld}"


# ── v2 payload → BusinessContext builder ────────────────────────────────────


def _entity() -> ExtractedEntity:
    return ExtractedEntity(
        entity_type="Function",
        name="getPayerCompetitors",
        file="src/Repo.java",
        repo="network-iq",
        signature="List<X> getPayerCompetitors(String base, Request req)",
        last_modified_commit="abc1234",
        confidence=0.9,
    )


def test_v2_payload_round_trip_populates_all_seven_fields():
    payload = {
        "purpose": "Returns competitors for a payer/LOB combo.",
        "history_summary": "Refactored to jOOQ in Jan 2024.",
        "invariants": ["base must be non-empty"],
        "change_risk": "HIGH",
        "change_risk_reason": "consumed by 3 dashboards",
        "is_idempotent": True,
        "null_handling": {"base": "throws", "req": "tolerates"},
        "transaction_mode": "read_only",
        "anti_patterns": ["potential_n_plus_1"],
        "engineering_notes": ["LATERAL unnest outer col"],
        "performance_class": "O(n log n)",
        "security_class": "authenticated",
    }
    bc = _bc_from_v2_payload(_entity(), payload, {"has_rich_pr": True})
    assert bc.schema_version == 2
    assert bc.is_idempotent is True
    assert bc.null_handling == {"base": "throws", "req": "tolerates"}
    assert bc.transaction_mode == "read_only"
    assert bc.anti_patterns == ["potential_n_plus_1"]
    assert bc.engineering_notes == ["LATERAL unnest outer col"]
    assert bc.performance_class == "O(n log n)"
    assert bc.security_class == "authenticated"
    assert bc.source_confidence == "medium"


def test_v2_payload_silent_defaults_dont_crash():
    """A payload missing every v2 key still yields a valid v2-tagged record."""
    bc = _bc_from_v2_payload(_entity(), {}, {})
    assert bc.schema_version == 2
    assert bc.is_idempotent is None
    assert bc.null_handling == {}
    assert bc.transaction_mode is None
    assert bc.anti_patterns == []
    assert bc.performance_class is None
    assert bc.security_class is None
    # And it must asdict() cleanly so the JSON store can round-trip it.
    d = asdict(bc)
    assert d["schema_version"] == 2
    assert json.loads(json.dumps(d))  # JSON round-trip ok


def test_v2_payload_coerces_non_string_null_handling_values():
    """Defensive: the model occasionally returns booleans or numbers for
    null_handling modes. We must coerce to strings so the dataclass is
    JSON-safe."""
    bc = _bc_from_v2_payload(
        _entity(), {"null_handling": {"x": True, "y": 0}}, {}
    )
    assert bc.null_handling == {"x": "True", "y": "0"}


# ── v1 → v2 migration ───────────────────────────────────────────────────────


def _write_brain(tmp: Path) -> None:
    (tmp / "business_context").mkdir(parents=True)
    (tmp / "business_context" / "v1.json").write_text(json.dumps({
        "id": "A",
        "metadata": {"business_context": {"purpose": "old", "invariants": []}},
    }))
    (tmp / "business_context" / "v2.json").write_text(json.dumps({
        "id": "B",
        "metadata": {"business_context": {"schema_version": 2, "purpose": "new"}},
    }))
    (tmp / "business_context" / "no-bc.json").write_text(json.dumps({"id": "C"}))


def test_migration_scan_counts_v1_and_v2_without_writes(tmp_path):
    _write_brain(tmp_path)
    before = (tmp_path / "business_context" / "v1.json").read_text()
    report = upgrade_business_context(tmp_path, mode="scan")
    assert report.v1_files == 1
    assert report.v2_files == 1
    assert report.migrated_files == 0
    # Scan must NOT mutate anything.
    assert (tmp_path / "business_context" / "v1.json").read_text() == before


def test_migration_inplace_bumps_v1_to_v2_with_default_fields(tmp_path):
    _write_brain(tmp_path)
    report = upgrade_business_context(tmp_path, mode="inplace")
    assert report.migrated_files == 1
    bc = json.loads((tmp_path / "business_context" / "v1.json").read_text())[
        "metadata"
    ]["business_context"]
    assert bc["schema_version"] == 2
    assert bc["is_idempotent"] is None
    assert bc["null_handling"] == {}
    assert bc["anti_patterns"] == []
    assert bc["purpose"] == "old"  # data preserved


def test_migration_inplace_is_idempotent(tmp_path):
    _write_brain(tmp_path)
    upgrade_business_context(tmp_path, mode="inplace")
    again = upgrade_business_context(tmp_path, mode="inplace")
    assert again.migrated_files == 0
    assert again.v1_files == 0


def test_migration_skips_malformed_json(tmp_path):
    (tmp_path / "business_context").mkdir()
    (tmp_path / "business_context" / "broken.json").write_text("{not json")
    report = upgrade_business_context(tmp_path, mode="scan")
    assert report.skipped_files == [
        str(tmp_path / "business_context" / "broken.json")
    ]
