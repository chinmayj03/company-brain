"""
Unit tests for the QueryResponse schema and the parse/render helpers.
"""
from __future__ import annotations

import json

import pytest

from companybrain.models.query_response import (
    CallChainStep,
    Citation,
    Confidence,
    QueryResponse,
    RiskAssessment,
    SqlBlock,
)
from companybrain.api.responses.markdown_renderer import render_to_markdown
from companybrain.api.routes.query import _parse_llm_response, _strip_uncited


# ── Schema construction ───────────────────────────────────────────────────────

def _make_response(**overrides) -> QueryResponse:
    defaults = dict(
        summary="A test summary.",
        confidence=Confidence(level="high", rationale="all nodes >= 0.9"),
    )
    defaults.update(overrides)
    return QueryResponse(**defaults)


def test_minimal_response():
    r = _make_response()
    assert r.summary == "A test summary."
    assert r.confidence.level == "high"
    assert r.call_chain == []
    assert r.sql_quotes == []
    assert r.affected_entities == []
    assert r.change_risk is None
    assert r.caveats == []
    assert r.follow_up_questions == []


def test_full_response():
    r = QueryResponse(
        summary="lob is used in 4 places [urn:cb:dev:code:repo:data_model:plan_info.lob].",
        call_chain=[
            CallChainStep(ord=1, urn="urn:cb:dev:code:repo:api_contract:getPayerCompetitors",
                          name="getPayerCompetitors", role="controller", edge_in=None,
                          annotations=["GetMapping"], one_liner="GET /v1/payers"),
            CallChainStep(ord=2, urn="urn:cb:dev:code:repo:component:CompSvc",
                          name="CompetitivenessService", role="service", edge_in="CALLS",
                          annotations=[], one_liner="delegates to repo"),
        ],
        sql_quotes=[
            SqlBlock(source_urn="urn:cb:dev:code:repo:component:CompRepo",
                     language="jooq", body="SELECT LOB FROM PLAN_INFO"),
        ],
        affected_entities=[
            Citation(urn="urn:cb:dev:code:repo:data_model:plan_info.lob",
                     name="plan_info.lob", why_relevant="column being renamed", confidence=1.0),
        ],
        change_risk=RiskAssessment(
            level="high",
            reason="lob is referenced in 4 methods",
            blast_radius_count=4,
            sample_affected=[],
        ),
        confidence=Confidence(level="high", rationale="confidence >= 0.9 on all nodes"),
        caveats=["migration file not in graph"],
        follow_up_questions=["What tests cover lob?"],
    )
    assert len(r.call_chain) == 2
    assert r.sql_quotes[0].language == "jooq"
    assert r.change_risk.blast_radius_count == 4


def test_call_chain_step_roles():
    valid_roles = [
        "entry", "controller", "service", "repository",
        "query", "external", "frontend", "test", "other"
    ]
    for role in valid_roles:
        step = CallChainStep(ord=1, urn="urn:cb:x", name="X",
                             role=role, one_liner="desc")
        assert step.role == role


def test_sql_block_languages():
    for lang in ["sql", "jpql", "jooq", "cypher", "mongo", "other"]:
        block = SqlBlock(source_urn="urn:cb:x", language=lang, body="SELECT 1")
        assert block.language == lang


# ── Backward-compat aliases ───────────────────────────────────────────────────

def test_answer_alias():
    r = _make_response(summary="hello world")
    assert r.answer == "hello world"


def test_sources_from_affected_entities():
    r = _make_response(
        affected_entities=[
            Citation(urn="urn:cb:x", name="Foo", why_relevant="reason", confidence=0.9)
        ]
    )
    assert r.sources == [{"urn": "urn:cb:x", "name": "Foo"}]


# ── JSON round-trip ───────────────────────────────────────────────────────────

def test_json_round_trip():
    r = _make_response(
        affected_entities=[
            Citation(urn="urn:cb:x", name="Foo", why_relevant="y", confidence=0.8)
        ]
    )
    dumped = r.model_dump_json()
    loaded = QueryResponse.model_validate_json(dumped)
    assert loaded.summary == r.summary
    assert loaded.affected_entities[0].urn == "urn:cb:x"


# ── _parse_llm_response ───────────────────────────────────────────────────────

def test_parse_valid_json():
    payload = {
        "summary": "Test answer.",
        "confidence": {"level": "high", "rationale": "all good"},
    }
    r = _parse_llm_response(json.dumps(payload), context="some context")
    assert r.summary == "Test answer."
    assert r.confidence.level == "high"


def test_parse_json_with_markdown_fences():
    payload = {"summary": "Fenced.", "confidence": {"level": "medium", "rationale": "ok"}}
    wrapped = f"```json\n{json.dumps(payload)}\n```"
    r = _parse_llm_response(wrapped, context="ctx")
    assert r.summary == "Fenced."


def test_parse_fallback_on_invalid_json():
    r = _parse_llm_response("This is not JSON at all.", context="some ctx")
    assert isinstance(r.summary, str)
    assert r.confidence.level in ("low", "medium")


def test_parse_fallback_confidence_low_without_context():
    r = _parse_llm_response("Not JSON", context=None)
    assert r.confidence.level == "low"


# ── _strip_uncited ────────────────────────────────────────────────────────────

def test_strip_uncited_keeps_cited_sentences():
    text = "lob is read by getPayerCompetitors [urn:cb:dev:code:repo:component:getPayerCompetitors]. Good."
    result = _strip_uncited(text)
    assert "lob" in result


def test_strip_uncited_drops_uncited_code_tokens():
    text = "The PaymentService calls StripeClient. This is fine."
    result = _strip_uncited(text)
    # PaymentService / StripeClient are code-shaped camelCase without URN — should be dropped
    assert "PaymentService" not in result


# ── Markdown renderer ─────────────────────────────────────────────────────────

def test_render_to_markdown_sets_raw_markdown():
    r = _make_response(summary="Test.")
    md = render_to_markdown(r)
    assert r.raw_markdown == md
    assert "## Summary" in md
    assert "Test." in md


def test_render_call_chain():
    r = _make_response(
        call_chain=[
            CallChainStep(ord=1, urn="urn:cb:x", name="Entry", role="entry",
                          edge_in=None, annotations=["GetMapping"], one_liner="desc"),
            CallChainStep(ord=2, urn="urn:cb:y", name="Service", role="service",
                          edge_in="CALLS", annotations=[], one_liner="impl"),
        ]
    )
    md = render_to_markdown(r)
    assert "## Call Chain" in md
    assert "Entry" in md
    assert "CALLS" in md


def test_render_sql_block():
    r = _make_response(
        sql_quotes=[
            SqlBlock(source_urn="urn:cb:x", language="sql", body="SELECT 1")
        ]
    )
    md = render_to_markdown(r)
    assert "## SQL" in md
    assert "SELECT 1" in md


def test_render_change_risk():
    r = _make_response(
        change_risk=RiskAssessment(
            level="high", reason="many callers", blast_radius_count=10, sample_affected=[]
        )
    )
    md = render_to_markdown(r)
    assert "Change Risk" in md
    assert "HIGH" in md
    assert "10" in md


def test_render_confidence_badge():
    r = _make_response(
        confidence=Confidence(level="low", rationale="sparse graph")
    )
    md = render_to_markdown(r)
    assert "LOW" in md
