"""
Unit tests for E5 SchemaMigrationPass — CONTAINS edge extraction from migrations.

Tests cover Flyway SQL, Alembic Python, Prisma schema, and the is_migration_file()
helper. LLM provider is mocked so no real API calls are made.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch

from companybrain.pipeline.passes.schema_migration_pass import (
    SchemaMigrationPass,
    is_migration_file,
)
from companybrain.models.entities import ExtractedEntity


def _entity(name: str, file: str, snippet: str) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type="Function",
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


# ── is_migration_file() helper ─────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("db/migrations/V1__create_users.sql", True),
    ("alembic/versions/abc_create_users.py", True),
    ("prisma/schema.prisma", True),
    ("src/repository.py", False),
    ("app/models.py", False),
    ("src/db/schema.ts", True),
    ("flyway/V2__add_column.sql", True),
    ("test/fixtures/SomeTest.java", False),
    ("knex/migrations/20240101_create_orders.js", True),
])
def test_is_migration_file(path: str, expected: bool):
    assert is_migration_file(path) == expected


# ── Flyway SQL migration ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_flyway_sql():
    sql = "CREATE TABLE users (id BIGINT NOT NULL, email VARCHAR(255) NOT NULL, created_at TIMESTAMP);"
    entities = [
        _entity("V1__create_users", "db/migrations/V1__create_users.sql", sql)
    ]
    llm_resp = {"tables": [
        {"table_name": "users", "operation": "create", "confidence": 1.0,
         "evidence": "CREATE TABLE users",
         "columns": [
             {"column_name": "id", "column_type": "BIGINT", "nullable": False},
             {"column_name": "email", "column_type": "VARCHAR(255)", "nullable": False},
             {"column_name": "created_at", "column_type": "TIMESTAMP", "nullable": True},
         ]},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = SchemaMigrationPass()
        rels, summary = await pass_.run(entities)

    assert len(rels) == 3  # one CONTAINS per column
    assert all(r.edge_type == "CONTAINS" for r in rels)
    assert all(r.from_entity == "users" for r in rels)
    assert all(r.from_type == "DatabaseTable" for r in rels)
    assert all(r.to_type == "DatabaseColumn" for r in rels)
    column_names = {r.to_entity for r in rels}
    assert "users.email" in column_names
    assert "users.id" in column_names
    assert summary["edges_emitted"] == 3


# ── Alembic Python migration ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_alembic():
    snippet = (
        "op.create_table('orders',\n"
        "    sa.Column('id', sa.Integer(), nullable=False),\n"
        "    sa.Column('total', sa.Numeric(), nullable=True)\n"
        ")"
    )
    entities = [
        _entity("abc_create_orders", "alembic/versions/abc_create_orders.py", snippet)
    ]
    llm_resp = {"tables": [
        {"table_name": "orders", "operation": "create", "confidence": 1.0,
         "evidence": "op.create_table('orders')",
         "columns": [
             {"column_name": "id", "column_type": "Integer", "nullable": False},
             {"column_name": "total", "column_type": "Numeric", "nullable": True},
         ]},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = SchemaMigrationPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 2
    to_entities = {r.to_entity for r in rels}
    assert "orders.id" in to_entities
    assert "orders.total" in to_entities


# ── Prisma schema ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_prisma():
    snippet = "model User {\n  id    Int    @id @default(autoincrement())\n  email String\n  name  String?\n}"
    entities = [
        _entity("schema", "prisma/schema.prisma", snippet)
    ]
    llm_resp = {"tables": [
        {"table_name": "user", "operation": "create", "confidence": 1.0,
         "evidence": "model User",
         "columns": [
             {"column_name": "id", "column_type": "Int", "nullable": False},
             {"column_name": "email", "column_type": "String", "nullable": False},
             {"column_name": "name", "column_type": "String", "nullable": True},
         ]},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = SchemaMigrationPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 3
    assert all(r.from_entity == "user" for r in rels)


# ── Non-migration file emits nothing ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_non_migration_file():
    entities = [
        _entity("UserService", "src/services/UserService.java",
                "public User findById(Long id) { ... }")
    ]
    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider({"tables": []})):
        pass_ = SchemaMigrationPass()
        rels, summary = await pass_.run(entities)

    assert rels == []
    assert summary["edges_emitted"] == 0


# ── Migration with ALTER TABLE ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_alter_table():
    snippet = "ALTER TABLE users ADD COLUMN phone VARCHAR(20);"
    entities = [
        _entity("V2__add_phone", "db/migrations/V2__add_phone.sql", snippet)
    ]
    llm_resp = {"tables": [
        {"table_name": "users", "operation": "alter", "confidence": 1.0,
         "evidence": "ALTER TABLE users ADD COLUMN phone",
         "columns": [
             {"column_name": "phone", "column_type": "VARCHAR(20)", "nullable": True},
         ]},
    ]}

    with patch("companybrain.pipeline.passes.base.get_provider",
               return_value=_mock_provider(llm_resp)):
        pass_ = SchemaMigrationPass()
        rels, _ = await pass_.run(entities)

    assert len(rels) == 1
    assert rels[0].to_entity == "users.phone"


# ── Env skip flag ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_migration_pass_skipped_via_env(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_SCHEMA_MIGRATION_PASS", "true")
    entities = [
        _entity("V1__create_users", "db/migrations/V1__create_users.sql",
                "CREATE TABLE users (id INT NOT NULL);")
    ]

    with patch("companybrain.pipeline.passes.base.get_provider") as mock_prov:
        pass_ = SchemaMigrationPass()
        rels, summary = await pass_.run(entities)

    mock_prov.return_value.chat_json.assert_not_called()
    assert rels == []
    assert summary["skipped_via_env"] is True
