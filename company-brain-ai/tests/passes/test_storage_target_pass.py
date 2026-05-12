"""
Unit tests for E3 StorageTargetPass — PERSISTS_TO edge extraction.

Tests cover Java jOOQ, Python SQLAlchemy, TypeScript Drizzle, Redis cache,
S3 object store, MongoDB collection, and filtering of unknown source entities.
LLM provider is mocked so no real API calls are made.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from companybrain.pipeline.passes.storage_target_pass import StorageTargetPass
from companybrain.models.entities import ExtractedEntity


def _entity(name: str, etype: str, snippet: str = "", file: str = "src/Repo.java",
            query_text: str = "") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=etype,
        name=name,
        file=file,
        repo="test-repo",
        signature="",
        last_modified_commit="abc123",
        confidence=1.0,
        code_snippet=snippet,
        query_text=query_text,
    )


def _mock_provider(response_json: dict):
    provider = AsyncMock()
    provider.chat_json = AsyncMock(return_value=json.dumps(response_json))
    return provider


# ── Java jOOQ ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_java_jooq():
    entities = [
        _entity("findByLobAndStates", "DatabaseQuery",
                snippet="dslContext.select().from(Tables.PLAN_INFO).where(...)")
    ]
    llm_resp = {"targets": [
        {"table_name": "plan_info", "target_type": "relational",
         "source_entity": "findByLobAndStates", "confidence": 1.0,
         "evidence": "Tables.PLAN_INFO"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].edge_type == "PERSISTS_TO"
    assert rels[0].to_entity == "plan_info"
    assert rels[0].to_type == "DatabaseTable"
    assert rels[0].from_entity == "findByLobAndStates"
    assert summary["edges_emitted"] == 1


# ── Python SQLAlchemy ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_python_sqlalchemy():
    entities = [
        _entity("find_all", "Function",
                snippet="session.query(User).filter(User.email == email).all()",
                file="app/repository.py"),
    ]
    llm_resp = {"targets": [
        {"table_name": "users", "target_type": "relational",
         "source_entity": "find_all", "confidence": 1.0, "evidence": "User"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].to_entity == "users"
    assert rels[0].from_entity == "find_all"


# ── TypeScript Drizzle ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_typescript_drizzle():
    entities = [
        _entity("findAll", "Function",
                snippet="db.select({ email: users.email, name: users.name }).from(users)",
                file="src/repository.ts"),
    ]
    llm_resp = {"targets": [
        {"table_name": "users", "target_type": "relational",
         "source_entity": "findAll", "confidence": 1.0, "evidence": "pgTable('users', ...)"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].to_entity == "users"
    assert rels[0].from_type == "Function"


# ── Redis cache target ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_redis_cache():
    entities = [
        _entity("getCompetitors", "Function",
                snippet='cache.get("competitors:lob"); cache.set("competitors:lob", data, ex=300)'),
    ]
    llm_resp = {"targets": [
        {"table_name": "competitors_lob", "target_type": "cache",
         "source_entity": "getCompetitors", "confidence": 0.9, "evidence": 'cache.get("competitors:lob")'},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].to_type == "DatabaseTable"  # all storage targets use DatabaseTable
    assert rels[0].confidence == 0.9


# ── Multiple targets from one entity ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_multiple_targets():
    entities = [
        _entity("syncUserData", "Function",
                snippet="session.query(User).all(); s3.put_object(Bucket='user-avatars'); redis.set('sync')"),
    ]
    llm_resp = {"targets": [
        {"table_name": "users", "target_type": "relational",
         "source_entity": "syncUserData", "confidence": 1.0, "evidence": "User"},
        {"table_name": "user_avatars", "target_type": "object_store",
         "source_entity": "syncUserData", "confidence": 1.0, "evidence": "Bucket='user-avatars'"},
        {"table_name": "sync", "target_type": "cache",
         "source_entity": "syncUserData", "confidence": 0.8, "evidence": "redis.set('sync')"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 3
    table_names = {r.to_entity for r in rels}
    assert {"users", "user_avatars", "sync"} == table_names
    assert summary["edges_emitted"] == 3


# ── Unknown source entity dropped ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_drops_unknown_source():
    entities = [_entity("KnownFunc", "Function", snippet="db.select().from(users)")]
    llm_resp = {"targets": [
        {"table_name": "users", "target_type": "relational",
         "source_entity": "GhostFunc", "confidence": 1.0, "evidence": "users"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, _ = await pass_.run(entities)

    assert rels == []


# ── Entities with no snippet or query_text skipped ────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_no_snippet_skipped():
    entities = [_entity("EmptyFunc", "Function", snippet="")]

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider({"targets": []})):
        pass_ = StorageTargetPass()
        rels, summary = await pass_.run(entities)

    assert rels == []
    assert summary["edges_emitted"] == 0


# ── Env skip flag ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_skipped_via_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_STORAGE_TARGET_PASS", "true")
    entities = [_entity("findAll", "Function", snippet="db.select().from(users)")]

    with patch("companybrain.pipeline.passes.base.get_provider") as mock_prov:
        pass_ = StorageTargetPass()
        rels, summary = await pass_.run(entities)

    mock_prov.return_value.chat_json.assert_not_called()
    assert rels == []
    assert summary["skipped_via_env"] is True


# ── Low confidence dropped ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_target_pass_drops_low_confidence():
    entities = [_entity("maybeQuery", "Function",
                        snippet="some_function() # might touch DB")]
    llm_resp = {"targets": [
        {"table_name": "mystery_table", "target_type": "relational",
         "source_entity": "maybeQuery", "confidence": 0.5, "evidence": "?"},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = StorageTargetPass()
        rels, _ = await pass_.run(entities)

    assert rels == []
