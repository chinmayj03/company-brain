"""Qdrant client + collection lifecycle.

Collection naming: brain__{workspace_slug}__{entity_type}
e.g.               brain__dev__component, brain__dev__api_contract

Multi-granularity index (ADR-0043 WS1.S2):
  brain__{slug}__t2_card   — pre-computed answer cards for hot nodes
  brain__{slug}__code      — raw code / signature text for structural queries
  brain__{slug}__business  — business context / t1_summary for semantic queries

Point IDs must be unsigned integers or UUIDs. Entity URNs are arbitrary
strings, so we derive a deterministic UUID5 from each URN and store the
original URN in the payload under the "urn" key.
"""
from __future__ import annotations
import hashlib
import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, SparseVectorParams, SparseIndexParams, Distance,
    PointStruct, SparseVector, Filter, FieldCondition, MatchValue,
)
import structlog

log = structlog.get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

_ALLOWED_TYPES = (
    "component", "screen", "api_contract", "data_model",
    "assumption", "business_context", "function_node",
)

# ── Multi-granularity index suffixes (ADR-0043 WS1.S2) ────────────────────────
# Each workspace gets three additional cross-type collections alongside the
# per-entity-type collections.  The suffix maps to what each collection indexes.
GRANULARITY_COLLECTIONS = ("t2_card", "code", "business")


def collection_name(workspace_slug: str, entity_type: str) -> str:
    return f"brain__{workspace_slug}__{entity_type}"


def granularity_collection_name(workspace_slug: str, granularity: str) -> str:
    """Return name for one of the three cross-type granularity collections.

    granularity must be one of: 't2_card', 'code', 'business'.
    """
    if granularity not in GRANULARITY_COLLECTIONS:
        raise ValueError(
            f"Unknown granularity {granularity!r}. Must be one of {GRANULARITY_COLLECTIONS}"
        )
    return f"brain__{workspace_slug}__{granularity}"


def make_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection(client: QdrantClient, workspace_slug: str,
                      entity_type: str, dense_dim: int) -> None:
    name = collection_name(workspace_slug, entity_type)
    _create_if_missing(client, name, dense_dim)


def ensure_granularity_collection(
    client: QdrantClient, workspace_slug: str, granularity: str, dense_dim: int
) -> None:
    """Ensure one of the three cross-type granularity collections exists."""
    name = granularity_collection_name(workspace_slug, granularity)
    _create_if_missing(client, name, dense_dim)


def _create_if_missing(client: QdrantClient, name: str, dense_dim: int) -> None:
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dense_dim, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))},
    )
    log.info("Qdrant collection created", name=name, dim=dense_dim)


def urn_to_point_id(urn: str) -> str:
    """Derive a deterministic UUID from a URN string for use as a Qdrant point ID."""
    return str(uuid.UUID(bytes=hashlib.md5(urn.encode()).digest()))


def upsert_point(client: QdrantClient, *, collection: str, point_id: str,
                 dense: list[float], sparse_indices: list[int], sparse_values: list[float],
                 payload: dict) -> None:
    """Upsert a point. point_id is a URN string and is converted to UUID internally."""
    qid = urn_to_point_id(point_id)
    client.upsert(
        collection_name=collection,
        points=[PointStruct(
            id=qid,
            vector={
                "dense": dense,
                "sparse": SparseVector(indices=sparse_indices, values=sparse_values),
            },
            payload=payload,
        )],
        wait=False,
    )


def delete_point(client: QdrantClient, *, collection: str, point_id: str) -> None:
    """Delete a point. point_id is a URN string and is converted to UUID internally."""
    client.delete(collection_name=collection, points_selector=[urn_to_point_id(point_id)])
