"""
GraphBuilder — writes pipeline results to PostgreSQL (primary store) and
optionally to Neo4j (dual-write path).

Uses SQLAlchemy async sessions for upserts into the graph tables:
  - nodes: entities extracted by the pipeline
  - edges: relationships between entities
  - node_context: business context synthesised by LLM Pass 3

When a Neo4jWriter is supplied the same data is mirrored to Neo4j using the
CB URN identity scheme.  The Neo4j write is purely additive — any error in
that path is logged and swallowed so that Postgres writes are never affected.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

import structlog
from sqlalchemy import text

from companybrain.db import get_session
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship, BusinessContext

log = structlog.get_logger(__name__)


class GraphBuilder:
    """
    Writes pipeline results to the PostgreSQL graph tables.
    All writes are scoped to a workspace_id.

    If *neo4j_writer* is provided, every write is also mirrored to Neo4j.
    The Neo4j path never raises — a failure there is logged and skipped.
    """

    def __init__(
        self,
        workspace_id: str,
        neo4j_writer: Optional[Neo4jWriter] = None,
    ) -> None:
        """
        Args:
            workspace_id:  Workspace identifier used as the Postgres RLS scope
                           and the URN scope segment in Neo4j.
            neo4j_writer:  Optional connected Neo4jWriter.  When supplied,
                           entities, relationships and contexts are dual-written
                           to Neo4j after the Postgres write succeeds.
        """
        self.workspace_id = workspace_id
        self.neo4j_writer = neo4j_writer

    async def upsert_entities(self, entities: list[ExtractedEntity]) -> None:
        """
        Insert or update entity nodes.
        Conflict key: (workspace_id, node_type, external_id).
        """
        if not entities:
            return

        async with get_session() as session:
            await session.execute(
                text(f"SET LOCAL app.workspace_id = '{self.workspace_id}'")
            )

            for entity in entities:
                metadata = json.dumps({
                    "file": entity.file,
                    "repo": entity.repo,
                    "signature": entity.signature,
                    "confidence": entity.confidence,
                    "first_appeared_commit": entity.first_appeared_commit,
                    "last_modified_commit": entity.last_modified_commit,
                })
                await session.execute(
                    text("""
                        INSERT INTO nodes (id, workspace_id, node_type, external_id, name, metadata, created_at, updated_at)
                        VALUES (:id, :workspace_id, :node_type, :external_id, :name, :metadata::jsonb, NOW(), NOW())
                        ON CONFLICT (workspace_id, node_type, external_id)
                        DO UPDATE SET
                            name = EXCLUDED.name,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW()
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "workspace_id": self.workspace_id,
                        "node_type": entity.entity_type,
                        "external_id": entity.external_id,
                        "name": entity.name,
                        "metadata": metadata,
                    }
                )

            await session.commit()
            log.info("Upserted entities", count=len(entities), workspace=self.workspace_id)

        # ── Neo4j dual-write ──────────────────────────────────────────────────
        if self.neo4j_writer:
            await self.neo4j_writer.upsert_entities(entities)

    async def upsert_relationships(self, relationships: list[ExtractedRelationship]) -> None:
        """
        Insert or update edges between entities.
        Resolves source/target node IDs by external_id before inserting.
        """
        if not relationships:
            return

        async with get_session() as session:
            await session.execute(
                text(f"SET LOCAL app.workspace_id = '{self.workspace_id}'")
            )

            # Build a cache of external_id -> node id
            node_id_cache: dict[str, Optional[str]] = {}

            async def resolve_node_id(external_id: str) -> Optional[str]:
                if external_id in node_id_cache:
                    return node_id_cache[external_id]
                result = await session.execute(
                    text("SELECT id FROM nodes WHERE workspace_id = :wid AND external_id = :eid LIMIT 1"),
                    {"wid": self.workspace_id, "eid": external_id}
                )
                row = result.fetchone()
                node_id = str(row.id) if row else None
                node_id_cache[external_id] = node_id
                return node_id

            inserted = 0
            for rel in relationships:
                source_id = await resolve_node_id(rel.from_entity)
                target_id = await resolve_node_id(rel.to_entity)

                if not source_id or not target_id:
                    log.debug(
                        "Skipping relationship — node not found",
                        from_entity=rel.from_entity,
                        to_entity=rel.to_entity,
                    )
                    continue

                await session.execute(
                    text("""
                        INSERT INTO edges (id, workspace_id, source_id, target_id, edge_type, confidence, last_seen, is_pruned, metadata)
                        VALUES (:id, :workspace_id, :source_id, :target_id, :edge_type, :confidence, NOW(), false, :metadata::jsonb)
                        ON CONFLICT (workspace_id, source_id, target_id, edge_type)
                        DO UPDATE SET
                            last_seen = NOW(),
                            confidence = GREATEST(edges.confidence, EXCLUDED.confidence)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "workspace_id": self.workspace_id,
                        "source_id": source_id,
                        "target_id": target_id,
                        "edge_type": rel.edge_type,
                        "confidence": rel.confidence,
                        "metadata": json.dumps({"evidence": rel.evidence}),
                    }
                )
                inserted += 1

            await session.commit()
            log.info("Upserted relationships", inserted=inserted, total=len(relationships), workspace=self.workspace_id)

        # ── Neo4j dual-write ──────────────────────────────────────────────────
        if self.neo4j_writer:
            await self.neo4j_writer.upsert_relationships(relationships)

    async def upsert_contexts(self, contexts: dict[str, BusinessContext]) -> None:
        """
        Insert or update business context records for each entity.
        Looks up the node by external_id, then upserts into node_context.
        """
        if not contexts:
            return

        async with get_session() as session:
            await session.execute(
                text(f"SET LOCAL app.workspace_id = '{self.workspace_id}'")
            )

            inserted = 0
            for external_id, context in contexts.items():
                # Resolve the node id
                result = await session.execute(
                    text("SELECT id FROM nodes WHERE workspace_id = :wid AND external_id = :eid LIMIT 1"),
                    {"wid": self.workspace_id, "eid": external_id}
                )
                row = result.fetchone()
                if not row:
                    log.debug("Skipping context — node not found", external_id=external_id)
                    continue

                node_id = str(row.id)
                body = json.dumps(context.__dict__).encode()
                metadata = json.dumps({
                    "change_risk": context.change_risk,
                    "source_confidence": context.source_confidence,
                    "owner_team": context.owner_team,
                })

                await session.execute(
                    text("""
                        INSERT INTO node_context (id, workspace_id, node_id, context_type, title, body, confidence, metadata, created_at, updated_at)
                        VALUES (:id, :workspace_id, :node_id, :context_type, :title, :body, :confidence, :metadata::jsonb, NOW(), NOW())
                        ON CONFLICT (workspace_id, node_id, context_type)
                        DO UPDATE SET
                            title = EXCLUDED.title,
                            body = EXCLUDED.body,
                            confidence = EXCLUDED.confidence,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW()
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "workspace_id": self.workspace_id,
                        "node_id": node_id,
                        "context_type": "llm_synthesis",
                        "title": context.purpose[:255] if context.purpose else "",
                        "body": body,
                        "confidence": {"high": 1.0, "medium": 0.7, "low": 0.4}.get(
                            context.source_confidence, 0.5
                        ),
                        "metadata": metadata,
                    }
                )
                inserted += 1

            await session.commit()
            log.info("Upserted contexts", inserted=inserted, total=len(contexts), workspace=self.workspace_id)

        # ── Neo4j dual-write ──────────────────────────────────────────────────
        if self.neo4j_writer:
            for external_id, context in contexts.items():
                await self.neo4j_writer.upsert_context(external_id, context)
