"""
Connector framework — B1.2 base classes and registry.

Connectors bring non-code sources (Notion, Confluence, Jira, etc.)
into the brain pipeline as SourceArtifacts.
"""
from companybrain.connectors.base import BaseConnector, ConnectorConfig, SourceArtifact
from companybrain.connectors.registry import ConnectorRegistry, register

__all__ = [
    "BaseConnector",
    "ConnectorConfig",
    "SourceArtifact",
    "ConnectorRegistry",
    "register",
]
