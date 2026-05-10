"""
Unit tests for E2 AnnotationPass — ANNOTATES edge extraction.

Tests cover Java/Spring, Python/FastAPI, and TypeScript/NestJS inputs.
LLM provider is mocked so no real API calls are made.
"""
import json
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from companybrain.pipeline.passes.annotation_pass import AnnotationPass
from companybrain.models.entities import ExtractedEntity


def _entity(name: str, etype: str, snippet: str, file: str = "src/Foo.java") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=etype,
        name=name,
        file=file,
        repo="test-repo",
        signature="",
        last_modified_commit="abc123",
        confidence=1.0,
        code_snippet=snippet,
    )


def _mock_provider(response_json: dict):
    provider = AsyncMock()
    provider.chat_json = AsyncMock(return_value=json.dumps(response_json))
    return provider


# ── Java Spring tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_java_transactional():
    entities = [
        _entity("getCompetitors", "Function",
                "@Transactional @Cacheable(\"competitors\") public List<> getCompetitors()")
    ]
    llm_resp = {"annotations": [
        {"annotation_name": "Transactional", "entity_name": "getCompetitors",
         "confidence": 1.0, "evidence": "@Transactional"},
        {"annotation_name": "Cacheable", "entity_name": "getCompetitors",
         "confidence": 1.0, "evidence": "@Cacheable(\"competitors\")"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 2
    edge_types = {r.edge_type for r in rels}
    assert edge_types == {"ANNOTATES"}
    annotation_names = {r.from_entity for r in rels}
    assert "Transactional" in annotation_names
    assert "Cacheable" in annotation_names
    assert summary["edges_emitted"] == 2
    assert not summary["skipped_via_env"]


@pytest.mark.asyncio
async def test_annotation_pass_java_no_snippets():
    """Entities without code_snippet should be silently skipped."""
    entities = [
        _entity("SomeClass", "Class", snippet="")  # no snippet
    ]
    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider({"annotations": []})):
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    assert rels == []
    assert summary["edges_emitted"] == 0


# ── Python FastAPI tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_python_fastapi():
    entities = [
        _entity("get_competitors", "Function",
                "@router.get('/competitors')\n@login_required\ndef get_competitors():",
                file="app/routes.py")
    ]
    llm_resp = {"annotations": [
        {"annotation_name": "RouterGet", "entity_name": "get_competitors",
         "confidence": 1.0, "evidence": "@router.get"},
        {"annotation_name": "LoginRequired", "entity_name": "get_competitors",
         "confidence": 1.0, "evidence": "@login_required"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 2
    from_entities = {r.from_entity for r in rels}
    assert "RouterGet" in from_entities
    assert all(r.to_entity == "get_competitors" for r in rels)


# ── TypeScript NestJS tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_typescript_nestjs():
    entities = [
        _entity("CompetitorController", "Class",
                "@Controller('/competitors')\n@UseGuards(AuthGuard)\nexport class CompetitorController",
                file="src/competitor.controller.ts")
    ]
    llm_resp = {"annotations": [
        {"annotation_name": "Controller", "entity_name": "CompetitorController",
         "confidence": 1.0, "evidence": "@Controller"},
        {"annotation_name": "UseGuards", "entity_name": "CompetitorController",
         "confidence": 1.0, "evidence": "@UseGuards(AuthGuard)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    assert any(r.from_entity == "Controller" for r in rels)
    assert all(r.to_type == "Class" for r in rels)


# ── Environment skip flag ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_skipped_via_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_ANNOTATION_PASS", "true")
    entities = [_entity("Foo", "Function", "@Transactional public void foo()")]

    with patch("companybrain.pipeline.passes.base.get_provider") as mock_prov:
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    mock_prov.return_value.chat_json.assert_not_called()
    assert rels == []
    assert summary["skipped_via_env"] is True


# ── Low-confidence filtering ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_drops_low_confidence():
    entities = [_entity("Foo", "Function", "@Transactional public void foo()")]
    llm_resp = {"annotations": [
        {"annotation_name": "Transactional", "entity_name": "Foo",
         "confidence": 0.5, "evidence": "@Transactional"},  # below 0.7 → dropped
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = AnnotationPass()
        rels, summary = await pass_.run(entities)

    assert rels == []  # dropped by base.run() confidence filter


# ── Unknown entity names ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annotation_pass_drops_unknown_entities():
    entities = [_entity("KnownEntity", "Function", "@Transactional void known()")]
    llm_resp = {"annotations": [
        {"annotation_name": "Transactional", "entity_name": "UnknownEntity",
         "confidence": 1.0, "evidence": "@Transactional"},  # not in entity list
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = AnnotationPass()
        rels, _ = await pass_.run(entities)

    assert rels == []  # unknown entity dropped in _parse_response
