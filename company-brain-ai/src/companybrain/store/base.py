"""
BrainStore interface and event model.

A BrainEntity is the canonical in-memory representation of any brain entity.
A BrainEvent describes a write/upsert/delete. Stores consume events.

Storage hierarchy (read priority):
  JsonFileBrainStore  ← source of truth, always-correct
  PostgresBrainStore  ← projection, fast read for Java backend
  Neo4jBrainStore     ← projection, fast traversal
  QdrantBrainStore    ← projection, fast similarity (ADR-0015)
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, AsyncIterator, Optional


# ── Canonical entity shape ───────────────────────────────────────────────────

@dataclass
class BrainEntity:
    """
    The canonical brain entity. JSON-serialisable.

    `id` is the canonical URN per ADR-0013:
      urn:cb:{tenant}:{domain}:{repo}:{entity_type}:{qualified_name}

    Legacy ids of the form `{repo}::{entity_type}::{qualified_name}` are still
    accepted by the JSON store for backward compatibility but new code must
    produce canonical URNs via `companybrain.store.identity.to_urn()`.

    ADR-0064 additions (optional; None/empty means not yet scanned):
      ttl_class      — one of TTLClass values; stamped at ingest
      pii_findings   — list of PIIFinding dicts from the privacy scanner
      pii_scrubbed   — True once PII spans have been redacted
    """
    id: str
    entity_type: str           # component | screen | api_contract | data_model | assumption | business_context | function_node
    repo: str
    file: str                  # relative to repo root
    qualified_name: str
    t1_summary: str = ""
    t0_token: str = ""         # ~15 tok
    t1_token: str = ""         # ~100 tok
    metadata: dict = field(default_factory=dict)
    relationships: list[dict] = field(default_factory=list)  # {target_id, edge_type, confidence, source}
    version_hash: str = ""     # sha256 of the entity's structural fingerprint
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_updated_by: str = "harness/extractor"
    # ADR-0064: privacy fields (append-only; do not remove)
    ttl_class: Optional[str] = None          # TTLClass value; None = not yet classified
    pii_findings: list[dict] = field(default_factory=list)   # serialised PIIFinding dicts
    pii_scrubbed: bool = False               # True once PII has been redacted by TTL evaluator

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BrainEntity":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in valid})


# ── Events ────────────────────────────────────────────────────────────────────

@dataclass
class StoreEvent:
    """Internal store-layer event envelope (upsert/invalidate/delete).

    Not to be confused with companybrain.events.models.BrainEvent which is the
    ADR-0073 event-stream record for the event-sourced memory substrate.
    """
    kind: str                  # "upsert" | "invalidate" | "delete"
    entity: Optional[BrainEntity] = None    # set for upsert
    entity_id: Optional[str] = None         # set for invalidate / delete
    run_id: str = ""
    workspace_id: str = ""
    occurred_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# Backward-compat alias — remove after all callers are migrated.
BrainEvent = StoreEvent


# ── Interface ─────────────────────────────────────────────────────────────────

class BrainStore(ABC):
    """
    Stores can read, write, and emit events. Most implementations are
    write-through to a backing data store; the JSON store is the SOT.
    """

    @abstractmethod
    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None: ...

    @abstractmethod
    async def read(self, entity_id: str) -> Optional[BrainEntity]: ...

    @abstractmethod
    async def is_fresh(self, entity_id: str, version_hash: str) -> bool: ...

    @abstractmethod
    async def list_ids(self) -> AsyncIterator[str]: ...

    @abstractmethod
    async def commit_run(self, run_id: str) -> None:
        """Called once at end of pipeline. Stores can persist any in-memory state."""
