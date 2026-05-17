from companybrain.store.base import BrainStore, BrainEntity, StoreEvent, BrainEvent  # BrainEvent is compat alias
from companybrain.store.json_store import JsonFileBrainStore
from companybrain.store.fanout import FanoutBrainStore

# Heavy-dependency stores use lazy imports so unit tests (no neo4j/httpx deps
# installed) can still import the package and test the JSON + fanout stores.

def _get_postgres_store():
    from companybrain.store.postgres_consumer import PostgresBrainStore
    return PostgresBrainStore


def _get_neo4j_store():
    from companybrain.store.neo4j_consumer import Neo4jBrainStore
    return Neo4jBrainStore


# Expose classes directly for callers that have the full deps installed.
# These will ImportError at call time (not import time) if deps are missing.
try:
    from companybrain.store.postgres_consumer import PostgresBrainStore
    from companybrain.store.neo4j_consumer import Neo4jBrainStore
except ImportError:
    pass  # Not available without graph deps; tests use json_store + fanout only


__all__ = [
    "BrainStore", "BrainEntity", "StoreEvent", "BrainEvent",
    "JsonFileBrainStore", "PostgresBrainStore", "Neo4jBrainStore",
    "FanoutBrainStore",
]
