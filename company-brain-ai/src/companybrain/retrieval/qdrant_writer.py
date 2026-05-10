"""ADR-0049 C5: Qdrant upsert with version_hash skip-on-unchanged.

Today every pipeline run re-embeds and re-upserts every entity even when the
source hasn't changed.  QdrantEntityWriter.upsert_entity checks whether the
stored version_hash matches before recomputing the embedding.

Usage (from QdrantBrainStore or any future writer):
    writer = QdrantEntityWriter(qdrant_client, collection, embedder)
    await writer.upsert_entity(brain_entity, version_hash=entity.body_hash)
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)


class QdrantEntityWriter:
    """Async entity upsert helper with version_hash idempotency guard."""

    def __init__(self, client: Any, collection: str, embedder: Any) -> None:
        self._client = client
        self._collection = collection
        self._embedder = embedder

    async def upsert_entity(
        self,
        entity: Any,
        version_hash: str,
        payload: Optional[dict] = None,
    ) -> bool:
        """Upsert entity into Qdrant; skip if version_hash is unchanged.

        Returns True if the upsert was performed, False if skipped.
        """
        urn = getattr(entity, "id", None) or getattr(entity, "urn", "")
        point_id = _urn_to_point_id(urn)

        try:
            existing = await self._client.retrieve(
                collection_name=self._collection,
                ids=[point_id],
                with_payload=["version_hash"],
            )
            if existing and existing[0].payload.get("version_hash") == version_hash:
                log.debug("qdrant.skip_unchanged", urn=urn, version_hash=version_hash)
                return False
        except Exception:
            pass  # Qdrant unavailable or collection missing — fall through to upsert

        text = getattr(entity, "t1_summary", "") or getattr(entity, "qualified_name", "") or urn
        try:
            embedding = self._embedder.embed(text)
        except Exception as exc:
            log.warning("qdrant_writer.embed_failed", urn=urn, error=str(exc))
            return False

        merged_payload = {
            "urn": urn,
            "entity_type": getattr(entity, "entity_type", ""),
            "qualified_name": getattr(entity, "qualified_name", ""),
            "t1_summary": text,
            "version_hash": version_hash,
        }
        if payload:
            merged_payload.update(payload)

        try:
            from qdrant_client.models import PointStruct  # type: ignore
            await self._client.upsert(
                collection_name=self._collection,
                points=[PointStruct(id=point_id, vector=embedding, payload=merged_payload)],
            )
            log.debug("qdrant.upserted", urn=urn, version_hash=version_hash)
            return True
        except Exception as exc:
            log.warning("qdrant_writer.upsert_failed", urn=urn, error=str(exc))
            return False


def _urn_to_point_id(urn: str) -> int:
    """Stable int ID from a URN string (same algorithm as urn_to_int in qdrant_client)."""
    import hashlib
    return int(hashlib.sha256(urn.encode()).hexdigest()[:16], 16) % (2 ** 63)
