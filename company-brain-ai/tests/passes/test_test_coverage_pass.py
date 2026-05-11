"""
Unit tests for E7 TestCoveragePass — TESTED_BY edge extraction.

Tests cover JUnit, pytest, Jest, naming-convention confidence tiers, and
filtering of unknown entity names. LLM provider is mocked.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from companybrain.pipeline.passes.test_coverage_pass import TestCoveragePass, _is_test_entity
from companybrain.models.entities import ExtractedEntity


def _entity(name: str, etype: str, snippet: str = "",
            file: str = "src/Foo.java") -> ExtractedEntity:
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


# ── _is_test_entity() helper ───────────────────────────────────────────────────

@pytest.mark.parametrize("name,file,expected", [
    ("test_get_competitors", "app/test_service.py", True),
    ("getCompetitorsTest", "src/ServiceTest.java", True),
    ("CompetitorSpec", "spec/competitor_spec.rb", True),
    ("getCompetitors", "src/CompetitorService.java", False),
    ("UserRepository", "src/repository.py", False),
    ("should_return_404", "tests/test_api.py", True),
])
def test_is_test_entity(name: str, file: str, expected: bool):
    e = _entity(name, "Function", file=file)
    assert _is_test_entity(e) == expected


# ── JUnit — explicit call (confidence 1.0) ────────────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_junit_explicit_call():
    entities = [
        _entity("getPayerCompetitors", "Function",
                "public List<> getPayerCompetitors(String lob) { ... }"),
        _entity("getCompetitors_returnsFilteredList", "Function",
                "@Test void getCompetitors_returnsFilteredList() { service.getPayerCompetitors(lob, types); }",
                file="src/test/CompetitorServiceTest.java"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "getPayerCompetitors",
         "test_entity": "getCompetitors_returnsFilteredList",
         "confidence": 1.0, "evidence": "service.getPayerCompetitors(lob, types)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].edge_type == "TESTED_BY"
    assert rels[0].from_entity == "getPayerCompetitors"
    assert rels[0].to_entity == "getCompetitors_returnsFilteredList"
    assert rels[0].confidence == 1.0
    assert summary["edges_emitted"] == 1


# ── pytest — naming convention (confidence 0.85) ──────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_pytest_naming_convention():
    entities = [
        _entity("get_competitors", "Function",
                "def get_competitors(lob): ...", file="app/service.py"),
        _entity("test_get_competitors", "Function",
                "def test_get_competitors(): ...", file="tests/test_service.py"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "get_competitors",
         "test_entity": "test_get_competitors",
         "confidence": 0.85, "evidence": "test_competitor_service.py naming convention"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].confidence == 0.85
    assert rels[0].from_entity == "get_competitors"


# ── Jest — renders component (confidence 1.0) ─────────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_jest_render():
    entities = [
        _entity("CompetitorTable", "FrontendComponent",
                "export const CompetitorTable = ({ data }) => <table>...</table>"),
        _entity("renders_competitor_table", "Function",
                "it('renders competitor table', () => { render(<CompetitorTable />); })",
                file="src/__tests__/CompetitorTable.test.tsx"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "CompetitorTable",
         "test_entity": "renders_competitor_table",
         "confidence": 1.0, "evidence": "render(<CompetitorTable />)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].from_type == "FrontendComponent"
    assert rels[0].to_type == "Function"


# ── No test entities → empty message → no LLM call ───────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_no_test_entities():
    entities = [
        _entity("getCompetitors", "Function", "public List<> getCompetitors() { ... }"),
        _entity("UserRepository", "Class", "public class UserRepository { ... }"),
    ]
    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider({"edges": []})):
        pass_ = TestCoveragePass()
        rels, summary = await pass_.run(entities)

    assert rels == []
    assert summary["edges_emitted"] == 0


# ── Unknown entity names dropped ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_drops_unknown_entities():
    entities = [
        _entity("realProd", "Function", "def realProd(): ..."),
        _entity("test_real_prod", "Function", "def test_real_prod(): ...",
                file="tests/test_real.py"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "ghostProd",  # not in entity list
         "test_entity": "test_real_prod",
         "confidence": 1.0, "evidence": "ghostProd()"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, _ = await pass_.run(entities)

    assert rels == []


# ── Low confidence (0.5) below threshold dropped ─────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_drops_low_confidence():
    entities = [
        _entity("ambiguousProd", "Function", "def ambiguousProd(): ..."),
        _entity("test_something", "Function",
                "def test_something(): # maybe tests ambiguousProd?",
                file="tests/test_misc.py"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "ambiguousProd",
         "test_entity": "test_something",
         "confidence": 0.5, "evidence": "co-location guess"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, _ = await pass_.run(entities)

    assert rels == []


# ── Multiple test → prod pairs ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_multiple_edges():
    entities = [
        _entity("createUser", "Function", "def createUser(data): ...", file="app/service.py"),
        _entity("getUser", "Function", "def getUser(id): ...", file="app/service.py"),
        _entity("test_create_user", "Function", "def test_create_user(): service.createUser(...)",
                file="tests/test_service.py"),
        _entity("test_get_user", "Function", "def test_get_user(): service.getUser(...)",
                file="tests/test_service.py"),
    ]
    llm_resp = {"edges": [
        {"production_entity": "createUser", "test_entity": "test_create_user",
         "confidence": 1.0, "evidence": "service.createUser(...)"},
        {"production_entity": "getUser", "test_entity": "test_get_user",
         "confidence": 1.0, "evidence": "service.getUser(...)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = TestCoveragePass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 2
    assert summary["edges_emitted"] == 2
    assert all(r.edge_type == "TESTED_BY" for r in rels)


# ── Env skip flag ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_test_coverage_pass_skipped_via_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_TEST_COVERAGE_PASS", "true")
    entities = [
        _entity("myFunc", "Function", "def myFunc(): ..."),
        _entity("test_my_func", "Function", "def test_my_func(): ...",
                file="tests/test_module.py"),
    ]

    with patch("companybrain.pipeline.passes.base.get_provider") as mock_prov:
        pass_ = TestCoveragePass()
        rels, summary = await pass_.run(entities)

    mock_prov.return_value.chat_json.assert_not_called()
    assert rels == []
    assert summary["skipped_via_env"] is True
