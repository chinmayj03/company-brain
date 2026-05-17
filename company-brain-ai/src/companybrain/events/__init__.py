"""
ADR-0090 P1 — Event-Stream Memory Substrate (M1 + M2 only).

Public API for this package:
  BrainEvent       — the frozen event-stream record (events.models)
  EventStore       — append-only event store (events.store)
  emit             — fire-and-forget helper (events.emitter)
  EntityStateCacheV1, CausalChainV2, SalienceScoreV3 — M2 views (events.views)
"""
from companybrain.events.models import BrainEvent, EVENT_TYPES
from companybrain.events.store import EventStore
from companybrain.events.emitter import emit, emit_entity_written, emit_edge_created
from companybrain.events.views import EntityStateCacheV1, CausalChainV2, SalienceScoreV3

__all__ = [
    "BrainEvent",
    "EVENT_TYPES",
    "EventStore",
    "emit",
    "emit_entity_written",
    "emit_edge_created",
    "EntityStateCacheV1",
    "CausalChainV2",
    "SalienceScoreV3",
]
