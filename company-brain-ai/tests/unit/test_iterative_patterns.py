"""Unit tests for ADR-0061 iterative-exploration modules.

The acceptance file in tests/acceptance/test_e1_through_e7.py exercises the
full /query + MCP wiring with stubbed I/O. This unit file covers the pure
helpers in each module so failures point at a specific implementation
detail rather than the orchestrator wiring.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from companybrain.agents.exploration_agent import (
    ExplorationResult, _glob_files, _grep_code, _read_file, should_fire,
    LOW_CONFIDENCE_THRESHOLD,
)
from companybrain.api.routes.clarification import (
    BUCKET_API_FIELD, BUCKET_DB_COLUMN, BUCKET_JSON_KEY,
    ClarificationResponse, _extract_candidate_token,
    detect_ambiguity, interpretation_hint,
)
from companybrain.api.routes.query_reread import (
    LOW_FIDELITY_THRESHOLD, _build_reread_user_message, _parse_or_keep,
    identify_shaky_citations,
)
from companybrain.extractors.diagram_extractor import (
    DiagramExtractor, _parse_svg, _parse_vision_json,
)
from companybrain.mcp.tools import diff_since as diff_since_mod
from companybrain.models.entities import Diagram, DiagramComponent
from companybrain.models.query_response import (
    Citation, Confidence, QueryResponse,
)


# ── E1: ExplorationAgent helpers ─────────────────────────────────────────────

def test_should_fire_low_confidence():
    assert should_fire(initial_confidence=0.3, zone_tokens_used=4000)


def test_should_fire_sparse_context():
    """Even high-confidence answers fire when context was sparse."""
    assert should_fire(initial_confidence=0.95, zone_tokens_used=10)


def test_should_fire_skips_confident_dense():
    assert not should_fire(initial_confidence=0.9, zone_tokens_used=4000)


def test_should_fire_threshold_boundary():
    assert not should_fire(LOW_CONFIDENCE_THRESHOLD, zone_tokens_used=2000)


def test_glob_files_skips_node_modules(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("y = 2\n")
    found = _glob_files(str(tmp_path), "**/*.py")
    assert "src/a.py" in found
    assert all("node_modules" not in p for p in found)


def test_read_file_truncates(tmp_path: Path):
    big = "x" * 20_000
    (tmp_path / "big.py").write_text(big)
    out = _read_file("big.py", repo_root=str(tmp_path), max_chars=500)
    assert len(out) < 1_000
    assert "truncated" in out


def test_exploration_result_is_serialisable():
    """Acceptance tests print the result; asdict must work for telemetry."""
    r = ExplorationResult(text="hi", steps=2)
    blob = asdict(r)
    assert blob["text"] == "hi"
    assert blob["steps"] == 2


# ── E2: query_reread ─────────────────────────────────────────────────────────

def _resp_with(citations: list[tuple[str, float]]) -> QueryResponse:
    return QueryResponse(
        summary="prior",
        confidence=Confidence(level="medium", rationale="t"),
        affected_entities=[
            Citation(urn=u, name=n, why_relevant="x", confidence=c)
            for u, n, c in [(f"urn:cb:x:{i}", f"name{i}", conf) for i, conf in enumerate(c for _, c in citations)]
        ],
    )


def test_identify_shaky_citations_returns_below_threshold():
    resp = QueryResponse(
        summary="...",
        confidence=Confidence(level="low", rationale="t"),
        affected_entities=[
            Citation(urn="urn:a", name="A", why_relevant="x", confidence=0.9),
            Citation(urn="urn:b", name="B", why_relevant="x", confidence=0.5),
            Citation(urn="urn:c", name="C", why_relevant="x", confidence=0.4),
        ],
    )
    shaky = identify_shaky_citations(resp, threshold=LOW_FIDELITY_THRESHOLD)
    assert {s.urn for s in shaky} == {"urn:b", "urn:c"}


def test_build_reread_user_message_includes_excerpts():
    from companybrain.api.routes.query_reread import _Excerpt
    ex = _Excerpt(urn="urn:x", name="MyClass", file_path="/repo/x.py",
                  body="def foo():\n    return 1\n")
    prior = QueryResponse(summary="prior",
                          confidence=Confidence(level="low", rationale="t"))
    msg = _build_reread_user_message("what is foo?", [ex], prior)
    assert "/repo/x.py" in msg
    assert "MyClass" in msg
    assert "def foo" in msg


def test_parse_or_keep_falls_back_on_garbage():
    prior = QueryResponse(summary="prior",
                          confidence=Confidence(level="low", rationale="t"))
    merged = _parse_or_keep("not JSON at all", prior)
    assert merged.summary == "not JSON at all"
    assert merged.confidence.level == "medium"


# ── E5: clarification detector ───────────────────────────────────────────────

def _seed_clarification_brain(root: Path, term: str) -> Path:
    """Make a fake .brain/ that has the term in both DB and API entity types."""
    brain = root / ".brain"
    (brain / "DatabaseColumn").mkdir(parents=True)
    (brain / "DatabaseColumn" / "lob.json").write_text(json.dumps({
        "qualified_name": f"plan_info.{term}",
        "name": term,
    }))
    (brain / "OpenAPISchema").mkdir(parents=True)
    (brain / "OpenAPISchema" / f"{term}.json").write_text(json.dumps({
        "qualified_name": f"PlanRequest.{term}", "name": term,
    }))
    return brain


def test_extract_candidate_token_prefers_quoted():
    assert _extract_candidate_token("rename the `lob` column") == "lob"


def test_extract_candidate_token_falls_back_to_identifier():
    assert _extract_candidate_token("rename the lob column") == "lob"


def test_extract_candidate_token_ignores_stopwords():
    assert _extract_candidate_token("rename the column") == ""


def test_detect_ambiguity_fires_on_multi_bucket(tmp_path: Path):
    _seed_clarification_brain(tmp_path, "lob")
    out = detect_ambiguity("rename the lob column", repo_path=str(tmp_path))
    assert out.ambiguous is True
    assert out.term == "lob"
    # Must include >= 2 interpretations + a "both" option
    ids = {opt["id"] for opt in (out.interpretations or [])}
    assert BUCKET_DB_COLUMN in ids
    assert BUCKET_API_FIELD in ids
    assert "both" in ids


def test_detect_ambiguity_skips_when_single_bucket(tmp_path: Path):
    brain = tmp_path / ".brain"
    (brain / "DatabaseColumn").mkdir(parents=True)
    (brain / "DatabaseColumn" / "lob.json").write_text(json.dumps({
        "qualified_name": "plan_info.lob", "name": "lob",
    }))
    out = detect_ambiguity("rename the lob column", repo_path=str(tmp_path))
    assert out.ambiguous is False


def test_detect_ambiguity_skips_without_verb():
    """No 'rename / change / what' verb → not ambiguous."""
    out = detect_ambiguity("the lob column is nice", repo_path="/tmp")
    assert out.ambiguous is False


def test_interpretation_hint_renders():
    h = interpretation_hint(BUCKET_DB_COLUMN, term="lob")
    assert "database column" in h.lower()
    assert "lob" in h


def test_interpretation_hint_handles_both():
    h = interpretation_hint("both", term="lob")
    assert "BOTH" in h


# ── E4: diff_since helpers ───────────────────────────────────────────────────

def test_load_entities_reads_brain_subdirs(tmp_path: Path):
    brain = tmp_path / ".brain"
    (brain / "component").mkdir(parents=True)
    (brain / "component" / "x.json").write_text(json.dumps({
        "id": "urn:cb:x", "qualified_name": "x", "file": "src/x.py",
    }))
    idx = diff_since_mod._load_entities(tmp_path)
    assert "src/x.py" in idx
    assert idx["src/x.py"][0]["urn"] == "urn:cb:x"


def test_seven_days_ago_is_iso_date():
    out = diff_since_mod._seven_days_ago()
    # YYYY-MM-DD
    assert len(out) == 10 and out[4] == "-" and out[7] == "-"


# ── E7: diagram extractor ────────────────────────────────────────────────────

def test_diagram_extractor_supports_docs_png():
    de = DiagramExtractor()
    assert de.supports(Path("docs/architecture.png"))
    assert de.supports(Path("docs/diagrams/flow.svg"))


def test_diagram_extractor_rejects_outside_docs():
    de = DiagramExtractor()
    assert not de.supports(Path("logo.png"))
    assert not de.supports(Path("src/main.png"))


def test_diagram_extractor_rejects_non_image():
    de = DiagramExtractor()
    assert not de.supports(Path("docs/readme.md"))


def test_diagram_extractor_svg_fallback(tmp_path: Path):
    """No vision API → SVG <text> parse still gives labels."""
    docs = tmp_path / "docs"
    docs.mkdir()
    svg_path = docs / "arch.svg"
    svg_path.write_text(
        "<svg>"
        "<title>Architecture</title>"
        "<text>API</text><text>DB</text><text>Queue</text>"
        "</svg>"
    )
    de = DiagramExtractor()
    batch = de.extract(svg_path, svg_path.read_text(), repo="demo")
    assert len(batch.diagrams) == 1
    d = batch.diagrams[0]
    names = {c.name for c in d.components}
    assert {"API", "DB", "Queue"}.issubset(names)


def test_parse_svg_returns_none_for_non_svg():
    assert _parse_svg("hello world") is None


def test_parse_vision_json_strips_code_fence():
    out = _parse_vision_json('```json\n{"title":"x","components":[]}\n```')
    assert out is not None
    assert out["title"] == "x"


def test_parse_vision_json_handles_garbage():
    assert _parse_vision_json("not json") is None


def test_diagram_entity_external_id():
    d = Diagram(repo="demo", file_path="docs/x.png", title="x")
    assert d.external_id == "diagram::demo::docs/x.png"


def test_diagram_carries_components():
    d = Diagram(repo="demo", file_path="docs/x.png", title="x",
                components=[DiagramComponent(name="API")])
    assert d.components[0].name == "API"


# ── E6: cross_repo_similarity guards ─────────────────────────────────────────

def test_find_similar_unknown_granularity():
    from companybrain.retrieval.cross_repo_similarity import find_similar
    with pytest.raises(ValueError):
        find_similar(own_workspace_slug="dev",
                     seeds=[{"urn": "urn:x", "name": "x", "text": "x"}],
                     granularity="not-a-real-granularity")


def test_find_similar_empty_seeds_short_circuits():
    from companybrain.retrieval.cross_repo_similarity import find_similar
    assert find_similar(own_workspace_slug="dev", seeds=[]) == []
