"""
B1.2 Connector framework — base classes.

BaseConnector is the ABC that every source connector must implement.
ConnectorConfig holds auth credentials and workspace info.
SourceArtifact is the unit of data passed from connectors to the ingestion pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ConnectorConfig:
    """Configuration passed to a connector at construction time."""

    workspace_id: str
    credentials: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceArtifact:
    """
    A single fetchable unit of content from an external source.

    `id` is the source-native identifier (e.g. Notion page UUID).
    `content` is populated by `fetch_artifact()`; it is None after `list_artifacts()`.
    """

    id: str
    source_type: str          # "notion" | "confluence" | "jira" | ...
    title: str
    url: str
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    ttl_class: str = "standard"   # "standard" | "ephemeral" | "permanent"


class BaseConnector(ABC):
    """
    Abstract base for all source connectors.

    Lifecycle:
      1. `validate_credentials()` — confirm the integration token works.
      2. `list_artifacts()` — yield SourceArtifact stubs (no content yet).
      3. `fetch_artifact(artifact)` — return artifact with `content` populated.
      4. `mark_sync_complete()` — update the cursor after all artifacts processed.
    """

    SOURCE_TYPE: str = ""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Return True if the connector can reach the source API."""
        ...

    @abstractmethod
    async def list_artifacts(self) -> AsyncIterator[SourceArtifact]:
        """
        Yield SourceArtifact stubs for all accessible items.
        Implementations should honour the incremental cursor when present.
        """
        ...

    @abstractmethod
    async def fetch_artifact(self, artifact: SourceArtifact) -> SourceArtifact:
        """Fetch full content for the given artifact stub and return an enriched copy."""
        ...

    @abstractmethod
    async def get_sync_cursor(self) -> str | None:
        """Return the last-sync timestamp (ISO 8601) or None for a full sync."""
        ...

    @abstractmethod
    def mark_sync_complete(self) -> None:
        """Persist the sync cursor so the next run can be incremental."""
        ...
