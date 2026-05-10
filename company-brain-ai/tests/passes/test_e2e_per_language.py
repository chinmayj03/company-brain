"""
ADR-0042 acceptance tests — per-language fixture validation.

For each fixture (java_jpa, python_sqlalchemy, typescript_drizzle) asserts that
running the full extraction-pass suite produces the minimum required edges:

  ✓ ≥ 1 ANNOTATES edge on the framework controller/decorator
  ✓ ≥ 1 PERSISTS_TO edge to the 'users' table
  ✓ ≥ 1 CONTAINS edge (DatabaseTable → users.email DatabaseColumn)
  ✓ All edge types are language-agnostic (no language-specific branching asserted)

LLM responses are pre-canned per fixture so no real API calls are made.
The fixture file content is read from disk to validate _build_user_message paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.passes.annotation_pass import AnnotationPass
from companybrain.pipeline.passes.storage_target_pass import StorageTargetPass
from companybrain.pipeline.passes.schema_migration_pass import SchemaMigrationPass

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _make_entity(
    name: str, etype: str, file: str, snippet: str = "", query_text: str = "", repo: str = "test-repo"
) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=etype,
        name=name,
        file=file,
        repo=repo,
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


def _read_fixture(rel_path: str) -> str:
    return (FIXTURES_DIR / rel_path).read_text()


# ──────────────────────────────────────────────────────────────────────────────
# Java Spring Boot / JPA fixture
# ──────────────────────────────────────────────────────────────────────────────

class TestJavaJpaFixture:
    """Acceptance tests for the java_jpa fixture."""

    @pytest.fixture
    def java_entities(self) -> list[ExtractedEntity]:
        base = "java_jpa/src/main/java/com/example"
        return [
            _make_entity(
                "CompetitorController", "Class",
                file=f"{base}/CompetitorController.java",
                snippet=_read_fixture(f"{base}/CompetitorController.java"),
            ),
            _make_entity(
                "CompetitorService", "Class",
                file=f"{base}/CompetitorService.java",
                snippet=_read_fixture(f"{base}/CompetitorService.java"),
            ),
            _make_entity(
                "getCompetitors", "Function",
                file=f"{base}/CompetitorService.java",
                snippet="@Transactional(readOnly = true)\n@Cacheable(\"competitors\")\npublic List<CompetitorDto> getCompetitors(String lob) { ... }",
            ),
            _make_entity(
                "CompetitorRepository", "Class",
                file=f"{base}/CompetitorRepository.java",
                snippet=_read_fixture(f"{base}/CompetitorRepository.java"),
            ),
            _make_entity(
                "findByLob", "DatabaseQuery",
                file=f"{base}/CompetitorRepository.java",
                query_text="SELECT * FROM users WHERE lob = ?",
            ),
            _make_entity(
                "Competitor", "Class",
                file=f"{base}/Competitor.java",
                snippet=_read_fixture(f"{base}/Competitor.java"),
            ),
        ]

    @pytest.mark.asyncio
    async def test_annotates_edges_on_controller(self, java_entities):
        llm_resp = {"annotations": [
            {"annotation_name": "RestController", "entity_name": "CompetitorController",
             "confidence": 1.0, "evidence": "@RestController"},
            {"annotation_name": "RequestMapping", "entity_name": "CompetitorController",
             "confidence": 1.0, "evidence": '@RequestMapping("/api/v1")'},
            {"annotation_name": "Transactional", "entity_name": "getCompetitors",
             "confidence": 1.0, "evidence": "@Transactional(readOnly = true)"},
            {"annotation_name": "Cacheable", "entity_name": "getCompetitors",
             "confidence": 1.0, "evidence": '@Cacheable("competitors")'},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, summary = await AnnotationPass().run(java_entities)

        assert any(r.edge_type == "ANNOTATES" for r in rels), "Expected ≥1 ANNOTATES edge"
        assert any(
            r.from_entity in ("RestController", "RequestMapping") and r.to_entity == "CompetitorController"
            for r in rels
        ), "Expected ANNOTATES edge targeting CompetitorController"
        assert summary["edges_emitted"] >= 1

    @pytest.mark.asyncio
    async def test_persists_to_users_table(self, java_entities):
        llm_resp = {"targets": [
            {"table_name": "users", "target_type": "relational",
             "source_entity": "findByLob", "confidence": 1.0, "evidence": "SELECT * FROM users"},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await StorageTargetPass().run(java_entities)

        assert any(r.edge_type == "PERSISTS_TO" and r.to_entity == "users" for r in rels), \
            "Expected PERSISTS_TO users edge"

    @pytest.mark.asyncio
    async def test_contains_users_email_column(self, java_entities):
        # Simulate a migration entity extracted from a Flyway SQL file
        migration_entities = java_entities + [
            _make_entity(
                "V1__create_users", "Function",
                file="db/migrations/V1__create_users.sql",
                snippet="CREATE TABLE users (id BIGINT NOT NULL, email VARCHAR(255) NOT NULL);",
            )
        ]
        llm_resp = {"tables": [
            {"table_name": "users", "operation": "create", "confidence": 1.0,
             "evidence": "CREATE TABLE users",
             "columns": [
                 {"column_name": "id", "column_type": "BIGINT", "nullable": False},
                 {"column_name": "email", "column_type": "VARCHAR(255)", "nullable": False},
             ]},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await SchemaMigrationPass().run(migration_entities)

        assert any(r.to_entity == "users.email" for r in rels), \
            "Expected CONTAINS edge to users.email"


# ──────────────────────────────────────────────────────────────────────────────
# Python FastAPI / SQLAlchemy fixture
# ──────────────────────────────────────────────────────────────────────────────

class TestPythonSqlAlchemyFixture:
    """Acceptance tests for the python_sqlalchemy fixture."""

    @pytest.fixture
    def python_entities(self) -> list[ExtractedEntity]:
        base = "python_sqlalchemy/app"
        return [
            _make_entity(
                "get_user", "ApiEndpoint",
                file=f"{base}/main.py",
                snippet='@app.get("/api/v1/users/{user_id}")\ndef get_user(user_id: int, db: Session = Depends(get_db)):\n    repo = UserRepository(db)\n    return repo.find_by_id(user_id)',
            ),
            _make_entity(
                "list_users", "ApiEndpoint",
                file=f"{base}/main.py",
                snippet='@app.get("/api/v1/users")\ndef list_users(db: Session = Depends(get_db)):\n    repo = UserRepository(db)\n    return repo.find_all()',
            ),
            _make_entity(
                "UserRepository", "Class",
                file=f"{base}/repository.py",
                snippet=_read_fixture(f"{base}/repository.py"),
            ),
            _make_entity(
                "find_all", "Function",
                file=f"{base}/repository.py",
                snippet="def find_all(self):\n    return self.db.query(User.email, User.name).all()",
            ),
            _make_entity(
                "User", "Class",
                file=f"{base}/models.py",
                snippet=_read_fixture(f"{base}/models.py"),
            ),
        ]

    @pytest.mark.asyncio
    async def test_annotates_edges_on_routes(self, python_entities):
        llm_resp = {"annotations": [
            {"annotation_name": "AppGet", "entity_name": "get_user",
             "confidence": 1.0, "evidence": '@app.get("/api/v1/users/{user_id}")'},
            {"annotation_name": "AppGet", "entity_name": "list_users",
             "confidence": 1.0, "evidence": '@app.get("/api/v1/users")'},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await AnnotationPass().run(python_entities)

        assert any(r.edge_type == "ANNOTATES" for r in rels), "Expected ≥1 ANNOTATES edge"
        assert any(r.to_entity in ("get_user", "list_users") for r in rels)

    @pytest.mark.asyncio
    async def test_persists_to_users_table(self, python_entities):
        llm_resp = {"targets": [
            {"table_name": "users", "target_type": "relational",
             "source_entity": "find_all", "confidence": 1.0,
             "evidence": "__tablename__ = 'users'"},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await StorageTargetPass().run(python_entities)

        assert any(r.edge_type == "PERSISTS_TO" and r.to_entity == "users" for r in rels), \
            "Expected PERSISTS_TO users edge"

    @pytest.mark.asyncio
    async def test_contains_users_email_column(self, python_entities):
        migration_entities = python_entities + [
            _make_entity(
                "abc_create_users", "Function",
                file="alembic/versions/abc_create_users.py",
                snippet=(
                    "op.create_table('users',\n"
                    "    sa.Column('id', sa.Integer(), nullable=False),\n"
                    "    sa.Column('email', sa.String(), nullable=False),\n"
                    "    sa.Column('name', sa.String(), nullable=True),\n"
                    ")"
                ),
            )
        ]
        llm_resp = {"tables": [
            {"table_name": "users", "operation": "create", "confidence": 1.0,
             "evidence": "op.create_table('users')",
             "columns": [
                 {"column_name": "id", "column_type": "Integer", "nullable": False},
                 {"column_name": "email", "column_type": "String", "nullable": False},
                 {"column_name": "name", "column_type": "String", "nullable": True},
             ]},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await SchemaMigrationPass().run(migration_entities)

        assert any(r.to_entity == "users.email" for r in rels), \
            "Expected CONTAINS edge to users.email"


# ──────────────────────────────────────────────────────────────────────────────
# TypeScript Next.js / Drizzle fixture
# ──────────────────────────────────────────────────────────────────────────────

class TestTypescriptDrizzleFixture:
    """Acceptance tests for the typescript_drizzle fixture."""

    @pytest.fixture
    def ts_entities(self) -> list[ExtractedEntity]:
        return [
            _make_entity(
                "GET", "ApiEndpoint",
                file="src/app/api/users/route.ts",
                snippet=_read_fixture("typescript_drizzle/src/app/api/users/route.ts"),
            ),
            _make_entity(
                "POST", "ApiEndpoint",
                file="src/app/api/users/route.ts",
                snippet="export async function POST(request: Request) {\n  const body = await request.json();\n  return NextResponse.json({ ok: true });\n}",
            ),
            _make_entity(
                "UserRepository", "Class",
                file="src/repository.ts",
                snippet=_read_fixture("typescript_drizzle/src/repository.ts"),
            ),
            _make_entity(
                "findAll", "Function",
                file="src/repository.ts",
                snippet="async findAll() {\n  return db.select({ email: users.email, name: users.name }).from(users);\n}",
            ),
            _make_entity(
                "users", "Class",
                file="src/db/schema.ts",
                snippet=_read_fixture("typescript_drizzle/src/db/schema.ts"),
            ),
        ]

    @pytest.mark.asyncio
    async def test_annotates_edges_on_route_handler(self, ts_entities):
        # Next.js route handlers use export function naming as convention, not decorators;
        # but the class-based exports in the fixture still yield annotation-like edges.
        llm_resp = {"annotations": [
            {"annotation_name": "NextRouteHandler", "entity_name": "GET",
             "confidence": 0.9, "evidence": "export async function GET(request: Request)"},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await AnnotationPass().run(ts_entities)

        assert any(r.edge_type == "ANNOTATES" for r in rels), "Expected ≥1 ANNOTATES edge"

    @pytest.mark.asyncio
    async def test_persists_to_users_table(self, ts_entities):
        llm_resp = {"targets": [
            {"table_name": "users", "target_type": "relational",
             "source_entity": "findAll", "confidence": 1.0,
             "evidence": "pgTable('users', ...)"},
        ]}

        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await StorageTargetPass().run(ts_entities)

        assert any(r.edge_type == "PERSISTS_TO" and r.to_entity == "users" for r in rels), \
            "Expected PERSISTS_TO users edge"

    @pytest.mark.asyncio
    async def test_contains_users_email_column(self, ts_entities):
        schema_entities = ts_entities + [
            _make_entity(
                "schema", "Function",
                file="src/db/schema.ts",
                snippet=_read_fixture("typescript_drizzle/src/db/schema.ts"),
            )
        ]
        llm_resp = {"tables": [
            {"table_name": "users", "operation": "create", "confidence": 1.0,
             "evidence": "pgTable('users', ...)",
             "columns": [
                 {"column_name": "id", "column_type": "serial", "nullable": False},
                 {"column_name": "email", "column_type": "text", "nullable": False},
                 {"column_name": "name", "column_type": "text", "nullable": True},
                 {"column_name": "is_active", "column_type": "boolean", "nullable": True},
             ]},
        ]}

        # schema.ts is matched by is_migration_file() via 'schema/' path marker
        with patch("companybrain.pipeline.passes.base.get_provider",
                   return_value=_mock_provider(llm_resp)):
            rels, _ = await SchemaMigrationPass().run(schema_entities)

        assert any(r.to_entity == "users.email" for r in rels), \
            "Expected CONTAINS edge to users.email"


# ──────────────────────────────────────────────────────────────────────────────
# Cross-fixture: edge types are language-agnostic
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_fixtures_produce_same_edge_types():
    """
    Edge types emitted by the passes must be identical across all three fixtures.
    This verifies there is no per-language branching that could drift.
    """
    expected_edge_types = {"ANNOTATES", "PERSISTS_TO", "CONTAINS"}

    fixtures_edge_types: dict[str, set[str]] = {}
    for fixture_name in ("java", "python", "ts"):
        if fixture_name == "java":
            entities = [
                _make_entity("SomeController", "Class",
                             "Fixture.java", "@RestController public class SomeController {}"),
                _make_entity("someQuery", "DatabaseQuery",
                             "Fixture.java", query_text="SELECT * FROM items"),
                _make_entity("V1__create", "Function",
                             "db/migrations/V1__create.sql",
                             "CREATE TABLE items (id INT, label VARCHAR(100))"),
            ]
        elif fixture_name == "python":
            entities = [
                _make_entity("list_items", "ApiEndpoint",
                             "app/main.py", '@app.get("/items")\ndef list_items(): ...'),
                _make_entity("ItemRepo", "Class",
                             "app/repo.py", 'class ItemRepo:\n    __tablename__ = "items"'),
                _make_entity("abc_items", "Function",
                             "alembic/versions/abc_items.py",
                             "op.create_table('items', sa.Column('id', sa.Integer()))"),
            ]
        else:
            entities = [
                _make_entity("GET", "ApiEndpoint",
                             "src/app/api/items/route.ts",
                             "export async function GET(req: Request) {}"),
                _make_entity("itemRepo", "Function",
                             "src/repo.ts",
                             "db.select({ label: items.label }).from(items)"),
                _make_entity("schema", "Function",
                             "src/db/schema.ts",
                             "export const items = pgTable('items', { id: serial('id'), label: text('label') })"),
            ]

        annotation_resp = {"annotations": [
            {"annotation_name": "RouteAnnotation", "entity_name": entities[0].name,
             "confidence": 0.9, "evidence": "annotation"},
        ]}
        storage_resp = {"targets": [
            {"table_name": "items", "target_type": "relational",
             "source_entity": entities[1].name, "confidence": 1.0, "evidence": "items"},
        ]}
        migration_resp = {"tables": [
            {"table_name": "items", "operation": "create", "confidence": 1.0,
             "evidence": "CREATE TABLE items",
             "columns": [
                 {"column_name": "id", "column_type": "INT", "nullable": False},
                 {"column_name": "label", "column_type": "VARCHAR", "nullable": True},
             ]},
        ]}

        fixture_rels: list = []
        for pass_cls, resp in [
            (AnnotationPass, annotation_resp),
            (StorageTargetPass, storage_resp),
            (SchemaMigrationPass, migration_resp),
        ]:
            with patch("companybrain.pipeline.passes.base.get_provider",
                       return_value=_mock_provider(resp)):
                rels, _ = await pass_cls().run(entities)
                fixture_rels.extend(rels)

        fixtures_edge_types[fixture_name] = {r.edge_type for r in fixture_rels}

    # All fixtures must produce the same set of edge types
    edge_type_sets = list(fixtures_edge_types.values())
    assert all(s == edge_type_sets[0] for s in edge_type_sets), (
        f"Edge types differ across fixtures: {fixtures_edge_types}"
    )
    assert expected_edge_types == edge_type_sets[0]
