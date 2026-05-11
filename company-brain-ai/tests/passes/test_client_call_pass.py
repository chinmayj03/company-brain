"""
Unit tests for E6 ClientCallPass — CALLS_ENDPOINT edge extraction.

Tests cover React/axios, Python/requests, TypeScript/fetch, gRPC stubs,
URL normalisation, and filtering. LLM provider is mocked.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from companybrain.pipeline.passes.client_call_pass import ClientCallPass
from companybrain.models.entities import ExtractedEntity


def _entity(name: str, etype: str, snippet: str, file: str = "src/client.ts") -> ExtractedEntity:
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


# ── React / axios ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_react_axios():
    entities = [
        _entity("CompetitorTable", "FrontendComponent",
                "axios.get('/api/v1/competitors?lob='+lob)")
    ]
    llm_resp = {"calls": [
        {"caller_entity": "CompetitorTable", "http_method": "GET",
         "url_pattern": "/api/v1/competitors", "confidence": 0.9,
         "evidence": "axios.get('/api/v1/competitors?lob='+lob)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].edge_type == "CALLS_ENDPOINT"
    assert rels[0].from_entity == "CompetitorTable"
    assert rels[0].to_entity == "GET /api/v1/competitors"
    assert rels[0].to_type == "ApiEndpoint"
    assert summary["edges_emitted"] == 1


# ── Python requests with URL param normalisation ───────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_python_requests_url_normalisation():
    entities = [
        _entity("fetch_user", "Function",
                "response = requests.get(f'{BASE_URL}/users/{user_id}')",
                file="app/client.py")
    ]
    llm_resp = {"calls": [
        {"caller_entity": "fetch_user", "http_method": "GET",
         "url_pattern": "/users/:id", "confidence": 0.9,
         "evidence": "requests.get(f'{BASE_URL}/users/{user_id}')"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, _ = await pass_.run(entities)

    assert rels[0].to_entity == "GET /users/:id"
    assert rels[0].from_entity == "fetch_user"


# ── TypeScript fetch POST ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_typescript_fetch_post():
    entities = [
        _entity("createOrder", "Function",
                "await fetch(`/api/orders/${customerId}`, {method:'POST', body: JSON.stringify(data)})")
    ]
    llm_resp = {"calls": [
        {"caller_entity": "createOrder", "http_method": "POST",
         "url_pattern": "/api/orders/:id", "confidence": 1.0,
         "evidence": "fetch('/api/orders/${customerId}', {method:'POST'})"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, _ = await pass_.run(entities)

    assert rels[0].to_entity == "POST /api/orders/:id"
    assert rels[0].from_type == "Function"


# ── Multiple calls from one entity ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_multiple_calls():
    entities = [
        _entity("Dashboard", "FrontendComponent",
                "axios.get('/api/users'); axios.get('/api/stats'); axios.post('/api/events', data)")
    ]
    llm_resp = {"calls": [
        {"caller_entity": "Dashboard", "http_method": "GET",
         "url_pattern": "/api/users", "confidence": 1.0, "evidence": "axios.get('/api/users')"},
        {"caller_entity": "Dashboard", "http_method": "GET",
         "url_pattern": "/api/stats", "confidence": 1.0, "evidence": "axios.get('/api/stats')"},
        {"caller_entity": "Dashboard", "http_method": "POST",
         "url_pattern": "/api/events", "confidence": 1.0, "evidence": "axios.post('/api/events')"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 3
    assert summary["edges_emitted"] == 3
    endpoints = {r.to_entity for r in rels}
    assert "GET /api/users" in endpoints
    assert "POST /api/events" in endpoints


# ── gRPC stub call ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_grpc_stub():
    entities = [
        _entity("UserGrpcClient", "Class",
                "stub.GetUser(GetUserRequest(id=user_id))")
    ]
    llm_resp = {"calls": [
        {"caller_entity": "UserGrpcClient", "http_method": "GET",
         "url_pattern": "/user.UserService/GetUser", "confidence": 0.9,
         "evidence": "stub.GetUser(...)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].to_entity == "GET /user.UserService/GetUser"


# ── Unknown caller entity dropped ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_drops_unknown_caller():
    entities = [_entity("RealComponent", "FrontendComponent",
                        "axios.get('/api/users')")]
    llm_resp = {"calls": [
        {"caller_entity": "GhostComponent", "http_method": "GET",
         "url_pattern": "/api/users", "confidence": 1.0, "evidence": "axios.get(...)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, _ = await pass_.run(entities)

    assert rels == []


# ── No HTTP keywords → _build_user_message returns empty → no LLM call ────────

@pytest.mark.asyncio
async def test_client_call_pass_no_http_keywords():
    entities = [
        _entity("pureCalc", "Function", "return a + b * c")
    ]
    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider({"calls": []})):
        pass_ = ClientCallPass()
        rels, summary = await pass_.run(entities)

    assert rels == []
    assert summary["edges_emitted"] == 0


# ── Env skip flag ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_skipped_via_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_CLIENT_CALL_PASS", "true")
    entities = [_entity("Widget", "FrontendComponent", "fetch('/api/data')")]

    with patch("companybrain.pipeline.passes.base.get_provider") as mock_prov:
        pass_ = ClientCallPass()
        rels, summary = await pass_.run(entities)

    mock_prov.return_value.chat_json.assert_not_called()
    assert rels == []
    assert summary["skipped_via_env"] is True


# ── Low confidence dropped ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_call_pass_drops_low_confidence():
    entities = [_entity("maybeCallsApi", "Function",
                        "const url = buildUrl(); fetch(url)")]
    llm_resp = {"calls": [
        {"caller_entity": "maybeCallsApi", "http_method": "GET",
         "url_pattern": "/unknown", "confidence": 0.4, "evidence": "fetch(url)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = ClientCallPass()
        rels, _ = await pass_.run(entities)

    assert rels == []
