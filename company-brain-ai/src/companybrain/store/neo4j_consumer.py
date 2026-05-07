"""
Neo4j consumer — wraps the existing graph/neo4j_writer.py.

Keeps the dual-write semantics that exist today; just hidden behind the
BrainStore contract.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

import structlog

from companybrain.store.base import BrainStore, BrainEntity
from companybrain.store.postgres_consumer import _to_extracted_entity, _to_relationships

if TYPE_CHECKING:
    from companybrain.graph.neo4j_writer import Neo4jWriter
    from companybrain.models.entities import ExtractedEntity, ExtractedRelationship

log = structlog.get_logger(__name__)


class Neo4jBrainStore(BrainStore):
    def __init__(self, writer: "Neo4jWriter", workspace_id: str = ""):
        self._writer = writer
        # workspace_id is already embedded in Neo4jWriter; kept here for the
        # ADR contract so callers can pass it explicitly without breakage.
        self._buf_e: list[ExtractedEntity] = []
        self._buf_r: list[ExtractedRelationship] = []

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        self._buf_e.append(_to_extracted_entity(entity))
        self._buf_r.extend(_to_relationships(entity))

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        return None

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        return False

    async def list_ids(self):
        if False: yield

    async def commit_run(self, run_id: str) -> None:
        if not self._buf_e:
            return
        log.info(
            "neo4j_consumer.flush",
            run_id=run_id,
            entities=len(self._buf_e),
            relationships=len(self._buf_r),
        )
        await self._writer.upsert_entities(self._buf_e)
        await self._writer.upsert_relationships(self._buf_r)
        self._buf_e.clear()
        self._buf_r.clear()
