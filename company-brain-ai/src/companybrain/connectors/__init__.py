"""
ADR-0092 — Multi-Source Connector Framework.

Public surface:
  - ConnectorConfig, SourceArtifact, BaseConnector  (base.py)
  - ConnectorRegistry                                (registry.py)
  - ConnectorIngestionPipeline, SyncResult           (pipeline.py)

Connectors self-register via @ConnectorRegistry.register("type") at import time.
Importing this package registers the built-in connectors (code).

Usage::

    from companybrain.connectors import (
        BaseConnector,
        ConnectorConfig,
        ConnectorRegistry,
        ConnectorIngestionPipeline,
        SourceArtifact,
        SyncResult,
    )
"""
from companybrain.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    SourceArtifact,
    TTL_EPHEMERAL,
    TTL_OPERATIONAL,
    TTL_PERMANENT,
    TTL_VOLATILE,
)
from companybrain.connectors.registry import ConnectorRegistry
from companybrain.connectors.pipeline import ConnectorIngestionPipeline, SyncResult

# Import built-in connectors so they self-register.
# Add new connectors here as they are implemented.
from companybrain.connectors import code as _code_connector  # noqa: F401

__all__ = [
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorIngestionPipeline",
    "ConnectorRegistry",
    "SourceArtifact",
    "SyncResult",
    "TTL_EPHEMERAL",
    "TTL_OPERATIONAL",
    "TTL_PERMANENT",
    "TTL_VOLATILE",
]
