"""
E3 — StorageTargetPass: DatabaseTable entity extraction from storage references.

Identifies all persistent-storage targets in code regardless of how they are
referenced — ORM model classes, generated table constants, raw SQL, schema
definitions, collection names, cache keys, bucket names, queue topics.

BRAIN_SKIP_STORAGE_TARGET_PASS=true disables this pass.
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel

from companybrain.llm import TaskRole
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.pipeline.passes.base import ExtractionPass

_MAX_TOKENS = 1_500


class _StorageTarget(BaseModel):
    table_name: str        # lower_snake_case canonical name
    target_type: str = "relational"  # relational | nosql | cache | object_store | queue
    source_entity: str     # entity that references this target
    confidence: float = 1.0
    evidence: str = ""


class _StorageResponse(BaseModel):
    targets: list[_StorageTarget]


_SYSTEM_PROMPT = """\
You are a code analyst specialising in data persistence and storage access patterns.

For each entity in the input, list every persistent-storage target it references.
A storage target is ANY of:
  Relational tables:
    - jOOQ TABLE constants (Tables.PLAN_INFO, Tables.COMPETITORS)
    - Hibernate @Table(name="..."), @Entity with class name
    - SQLAlchemy __tablename__, mapped_column(), Base.metadata.tables
    - Prisma model blocks, Drizzle pgTable(), mysqlTable(), sqliteTable()
    - Knex schema .createTable(), raw table name in knex('table_name')
    - TypeORM @Entity(), @Table()
    - ActiveRecord model class name → snake_case table name
    - Raw SQL table names in FROM / JOIN / INSERT INTO / UPDATE / CREATE TABLE
  NoSQL collections: MongoDB .collection(), DynamoDB Table, Firestore .collection()
  Caches: Redis key patterns, @Cacheable("cacheName"), cache.get("key")
  Object stores: S3 bucket names (bucket_name=, Bucket=, putObject key prefix)
  Message queues / topics: Kafka topic names, SQS queue names, RabbitMQ exchange/routing key

NORMALISE the target name to lower_snake_case.
  Examples: PLAN_INFO → plan_info, CompetitorModel → competitor_model,
            "users" → users, pgTable("user_profiles") → user_profiles

Emit ONE target per referenced storage target.
target_type must be one of: relational | nosql | cache | object_store | queue

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — Java jOOQ repository:
Entity: findByLobAndStates [DatabaseQuery], snippet: dslContext.select().from(Tables.PLAN_INFO).where(...)
Expected: {"targets": [
  {"table_name": "plan_info", "target_type": "relational", "source_entity": "findByLobAndStates", "confidence": 1.0, "evidence": "Tables.PLAN_INFO"}
]}

EXAMPLE 2 — Python SQLAlchemy model:
Entity: UserRepository [Class], snippet: class User(Base):\\n    __tablename__ = 'users'\\n    email = mapped_column(String)
Expected: {"targets": [
  {"table_name": "users", "target_type": "relational", "source_entity": "UserRepository", "confidence": 1.0, "evidence": "__tablename__ = 'users'"}
]}

EXAMPLE 3 — TypeScript Drizzle schema:
Entity: createUser [Function], snippet: const users = pgTable('users', { email: text('email') })\\nawait db.insert(users).values(...)
Expected: {"targets": [
  {"table_name": "users", "target_type": "relational", "source_entity": "createUser", "confidence": 1.0, "evidence": "pgTable('users', ...)"}
]}

Return ONLY valid JSON: {"targets": [...]}. No prose.
"""


class StorageTargetPass(ExtractionPass):
    name = "storage_target_pass"
    role = TaskRole.FAST
    max_tokens = _MAX_TOKENS
    system_prompt = _SYSTEM_PROMPT

    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        relevant = [
            e for e in entities
            if e.entity_type in (
                "Function", "DatabaseQuery", "Class", "ApiEndpoint"
            ) and (e.code_snippet or e.query_text)
        ]
        if not relevant:
            return ""

        lines = [f"Entities ({len(relevant)} total — find storage targets for each):"]
        for e in relevant[:30]:
            snippet = e.query_text or e.code_snippet or ""
            lines.append(f"\n[{e.entity_type}] {e.name}\nSnippet: {snippet[:400]}")
        return "\n".join(lines)

    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        data = json.loads(raw)
        resp = _StorageResponse(**data)

        entity_names = {e.name for e in entities}
        rels: list[ExtractedRelationship] = []
        for tgt in resp.targets:
            if tgt.source_entity not in entity_names:
                continue
            # Emit PERSISTS_TO edge from source entity → DatabaseTable
            rels.append(ExtractedRelationship(
                from_entity=tgt.source_entity,
                from_type=next(
                    (e.entity_type for e in entities if e.name == tgt.source_entity),
                    "Function",
                ),
                edge_type="PERSISTS_TO",
                to_entity=tgt.table_name,
                to_type="DatabaseTable",
                confidence=tgt.confidence,
                evidence=tgt.evidence,
            ))
        return rels
