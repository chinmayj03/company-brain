"""
E5 — SchemaMigrationPass: schema entities from migration files.

Identifies DatabaseTable and DatabaseColumn entities in migration / schema
definition files regardless of framework (Flyway, Liquibase, Alembic, Prisma,
Knex, Rails, Goose, Atlas, or raw SQL DDL).

The orchestrator routes files to this pass when their path contains migration
path markers. The LLM verifies the file is actually a migration before emitting.

BRAIN_SKIP_SCHEMA_MIGRATION_PASS=true disables this pass.
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from companybrain.llm import TaskRole
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.pipeline.passes.base import ExtractionPass

_MAX_TOKENS = 2_500

# Path markers used by orchestrator to route migration files to this pass
MIGRATION_PATH_MARKERS = (
    "migrations/", "db/migrate/", "prisma/", "flyway/", "liquibase/",
    "alembic/", "knex/", "schema/", "db/schema",
)
MIGRATION_FILE_PATTERNS = (
    "V", "__", ".sql", ".prisma", "alembic", "migrate",
    "create_table", "migration",
)


def is_migration_file(file_path: str) -> bool:
    """Return True if the file path looks like a migration or schema file."""
    path_lower = file_path.lower()
    return (
        any(m in path_lower for m in MIGRATION_PATH_MARKERS)
        or any(p in path_lower for p in MIGRATION_FILE_PATTERNS)
    )


class _ColumnDef(BaseModel):
    column_name: str
    column_type: str = ""
    nullable: bool = True
    evidence: str = ""


class _TableDef(BaseModel):
    table_name: str          # lower_snake_case
    operation: str = "create"  # create | alter | drop
    columns: list[_ColumnDef] = Field(default_factory=list)
    confidence: float = 1.0
    evidence: str = ""


class _SchemaMigrationResponse(BaseModel):
    tables: list[_TableDef]


_SYSTEM_PROMPT = """\
You are a database schema analyst. The input is a database schema or migration file.

List every table CREATED or ALTERED, every COLUMN ADDED or MODIFIED, and every
INDEX or CONSTRAINT created.

Migration frameworks you may encounter (not limited to):
  SQL DDL: CREATE TABLE, ALTER TABLE, ADD COLUMN, CREATE INDEX
  Flyway: SQL migration files (V{version}__{description}.sql)
  Liquibase: XML/YAML/JSON changesets
  Alembic (Python): op.create_table(), op.add_column(), op.create_index()
  Prisma Migrate: model blocks with field definitions
  Knex Migrate: knex.schema.createTable(), table.string(), table.integer()
  Rails Migrations: create_table do |t|, add_column, add_index
  Goose: -- +goose Up/Down, CREATE TABLE
  Atlas: HCL schema definitions

RULES:
  - Normalise table_name to lower_snake_case.
  - operation: "create" for CREATE TABLE / new model; "alter" for ADD/MODIFY COLUMN.
  - For each column: provide column_name, column_type (SQL type or ORM type), nullable.
  - If the file is NOT a migration or schema definition, return {"tables": []}.
  - confidence: 1.0 for explicit DDL; 0.8 for ORM model inference.

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — Raw SQL Flyway migration:
Content: CREATE TABLE users (id BIGINT NOT NULL, email VARCHAR(255) NOT NULL, created_at TIMESTAMP);
Expected: {"tables": [
  {"table_name": "users", "operation": "create", "confidence": 1.0,
   "evidence": "CREATE TABLE users",
   "columns": [
     {"column_name": "id",         "column_type": "BIGINT",       "nullable": false},
     {"column_name": "email",      "column_type": "VARCHAR(255)",  "nullable": false},
     {"column_name": "created_at", "column_type": "TIMESTAMP",    "nullable": true}
  ]}
]}

EXAMPLE 2 — Alembic Python migration:
Content: op.create_table('orders', sa.Column('id', sa.Integer(), nullable=False), sa.Column('total', sa.Numeric(), nullable=True))
Expected: {"tables": [
  {"table_name": "orders", "operation": "create", "confidence": 1.0,
   "evidence": "op.create_table('orders')",
   "columns": [
     {"column_name": "id",    "column_type": "Integer", "nullable": false},
     {"column_name": "total", "column_type": "Numeric", "nullable": true}
  ]}
]}

EXAMPLE 3 — Prisma schema:
Content: model Product { id Int @id @default(autoincrement()) name String price Decimal }
Expected: {"tables": [
  {"table_name": "product", "operation": "create", "confidence": 1.0,
   "evidence": "model Product",
   "columns": [
     {"column_name": "id",    "column_type": "Int",     "nullable": false},
     {"column_name": "name",  "column_type": "String",  "nullable": true},
     {"column_name": "price", "column_type": "Decimal", "nullable": true}
  ]}
]}

Return ONLY valid JSON: {"tables": [...]}. No prose.
"""


class SchemaMigrationPass(ExtractionPass):
    name = "schema_migration_pass"
    role = TaskRole.FAST
    max_tokens = _MAX_TOKENS
    system_prompt = _SYSTEM_PROMPT

    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        # For migration pass we look at file content through code_snippet
        relevant = [
            e for e in entities
            if is_migration_file(e.file) and e.code_snippet
        ]
        if not relevant:
            return ""

        lines = ["Migration / schema files to analyse:"]
        for e in relevant[:10]:
            lines.append(f"\n=== {e.file} ===\n{(e.code_snippet or '')[:1500]}")
        return "\n".join(lines)

    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        data = json.loads(raw)
        resp = _SchemaMigrationResponse(**data)

        rels: list[ExtractedRelationship] = []
        for table in resp.tables:
            table_name = table.table_name
            # Emit one PERSISTS_TO edge from the first migration entity to the table
            # (used as a signal that this table exists in the schema)
            for col in table.columns:
                col_full = f"{table_name}.{col.column_name}"
                # Record as CONTAINS — table contains column
                rels.append(ExtractedRelationship(
                    from_entity=table_name,
                    from_type="DatabaseTable",
                    edge_type="CONTAINS",
                    to_entity=col_full,
                    to_type="DatabaseColumn",
                    confidence=table.confidence,
                    evidence=table.evidence or col.evidence,
                ))
        return rels
