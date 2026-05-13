"""
Postgres consumer — replays BrainEvents through the existing JavaGraphClient.

This is a thin shim. It exists so the orchestrator stops calling JavaGraphClient
directly and instead writes through BrainStore → events → consumer. Net effect on
the Java side: identical (still POSTs to /v1/internal/pipeline-result).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

import structlog

from companybrain.store.base import BrainStore, BrainEntity

if TYPE_CHECKING:
    from companybrain.graph.java_client import JavaGraphClient
    from companybrain.models.entities import Artifact, ExtractedEntity, ExtractedRelationship, BusinessContext

log = structlog.get_logger(__name__)


class PostgresBrainStore(BrainStore):
    """
    Wraps JavaGraphClient for write-path.

    Buffers BrainEntity writes and flushes to the Java backend on commit_run().
    Call configure() before commit_run() to attach the full pipeline metadata
    (stages_summary, intent_contexts, memory_tokens, artifacts).
    """

    def __init__(self, java_client: "JavaGraphClient"):
        self._client = java_client
        self._buffered_entities: list[ExtractedEntity] = []
        self._buffered_relationships: list[ExtractedRelationship] = []
        self._buffered_contexts: dict[str, BusinessContext] = {}
        self._pipeline_meta: dict = {}
        self._artifacts: list[Artifact] = []
        self._intent_contexts: dict = {}

    def configure(
        self,
        pipeline_meta: dict | None = None,
        artifacts: list | None = None,
        intent_contexts: dict | None = None,
    ) -> None:
        """
        Set rich pipeline metadata before commit_run().
        Preserves backward compatibility — callers that don't configure() get
        a minimal metadata dict with just run_id.
        """
        if pipeline_meta:
            self._pipeline_meta = pipeline_meta
        if artifacts:
            self._artifacts = artifacts
        if intent_contexts:
            self._intent_contexts = intent_contexts

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        self._buffered_entities.append(_to_extracted_entity(entity))
        self._buffered_relationships.extend(_to_relationships(entity))
        ctx = _to_context(entity)
        if ctx is not None:
            # Key matches what ContextSynthesizer and enrich use: repo/file::name
            ctx_key = f"{entity.repo}/{entity.file}::{entity.qualified_name}"
            self._buffered_contexts[ctx_key] = ctx

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        # Optional: implement via Java REST API. Tests should hit the JSON store.
        return None

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        # Java side has its own freshness; the JSON store is the freshness oracle.
        return False

    async def list_ids(self):
        if False: yield  # not implemented; Java is not a list source

    async def commit_run(self, run_id: str) -> None:
        if not self._buffered_entities:
            return
        meta = {**self._pipeline_meta, "run_id": run_id}
        log.info(
            "postgres_consumer.flush",
            run_id=run_id,
            entities=len(self._buffered_entities),
            relationships=len(self._buffered_relationships),
        )
        await self._client.flush(
            entities=self._buffered_entities,
            relationships=self._buffered_relationships,
            contexts=self._buffered_contexts,
            pipeline_meta=meta,
            artifacts=self._artifacts,
            intent_contexts=self._intent_contexts,
        )
        self._buffered_entities.clear()
        self._buffered_relationships.clear()
        self._buffered_contexts.clear()


def _coerce_confidence(raw: object, default: float = 0.9) -> float:
    """Normalize any confidence representation to a plain float.

    Stored entities can carry confidence in two formats (B5 schema mismatch):
      • float:  0.85  — produced by ContextAgent and most extractors
      • object: {"value": 0.85, "rationale": "..."}  — produced by an earlier
                ADR-0005 rubric variant that has since been deprecated
    Coerce both forms to float so consumers never receive a dict where they
    expect a number.
    """
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        val = raw.get("value") or raw.get("score") or raw.get("confidence")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_extracted_entity(entity: BrainEntity) -> "ExtractedEntity":
    """Translate canonical BrainEntity → existing ExtractedEntity."""
    from companybrain.models.entities import ExtractedEntity
    return ExtractedEntity(
        entity_type=entity.entity_type,
        name=entity.qualified_name,
        file=entity.file,
        repo=entity.repo,
        signature=entity.metadata.get("signature", ""),
        last_modified_commit=entity.metadata.get("last_modified_commit", ""),
        confidence=_coerce_confidence(entity.metadata.get("confidence")),
        code_snippet=entity.metadata.get("code_snippet"),
        query_text=entity.metadata.get("query_text"),
    )


def _to_relationships(entity: BrainEntity) -> "list[ExtractedRelationship]":
    """Translate BrainEntity.relationships → list of ExtractedRelationship."""
    from companybrain.models.entities import ExtractedRelationship
    out = []
    for rel in entity.relationships:
        out.append(ExtractedRelationship(
            from_entity=entity.id,
            from_type=entity.entity_type,
            edge_type=rel["edge_type"],
            to_entity=rel["target_id"],
            to_type=rel.get("target_type", "component"),
            confidence=_coerce_confidence(rel.get("confidence")),
            evidence=rel.get("evidence", rel.get("source", "brain_store")),
        ))
    return out


def _to_context(entity: BrainEntity) -> "Optional[BusinessContext]":
    """
    Extract a BusinessContext from entity.metadata["business_context"] if present.

    enrich() stores the LLM-synthesised context blob as a dict under
    entity.metadata["business_context"] before writing back to .brain/ JSON.
    rebuild-from-json replays those JSON files through write(), so we must
    lift the blob here to populate _buffered_contexts — otherwise node_context
    stays 0 after every rebuild even though the data is sitting in the JSON.
    """
    raw = (entity.metadata or {}).get("business_context")
    if not raw:
        return None
    try:
        from companybrain.models.entities import BusinessContext
        if isinstance(raw, BusinessContext):
            return raw
        # raw is a plain dict (JSON round-trip from .brain/ file)
        return BusinessContext(**{
            k: v for k, v in raw.items()
            if k in BusinessContext.__dataclass_fields__
        })
    except Exception:
        # Malformed / schema-drifted blob — skip silently rather than aborting
        # the whole rebuild.
        return None
