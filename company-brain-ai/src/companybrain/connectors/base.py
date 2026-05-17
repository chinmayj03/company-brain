"""
ADR-0092 — Multi-Source Connector Framework: core data model and abstract interface.

Every non-code knowledge source (Notion, Slack, Salesforce, Confluence, …) implements
BaseConnector. The CodeConnector (code.py) wraps the existing extraction pipeline and
serves as a reference implementation.

Design choices:
  - SourceArtifact carries a URN using the scheme source://<type>/<kind>/<id>@<workspace_id>
    This is distinct from the brain entity URN (urn:cb:…) — conversion to domain entities
    happens downstream in ConnectorIngestionPipeline.
  - TTL classes are the four tiers from ADR-0064: permanent, operational, ephemeral, volatile.
  - Credentials live in ConnectorConfig.credentials (opaque dict). Secret decryption is the
    connector's responsibility; the framework never logs credential values.
  - Incremental sync uses an opaque cursor dict. The pipeline stores and restores it; connectors
    decide the internal shape (e.g. {"since": "2026-01-01T00:00:00Z", "page_token": "abc"}).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional


# ── TTL tiers (ADR-0064) ──────────────────────────────────────────────────────
TTL_PERMANENT   = "permanent"    # architecture decisions, API contracts — never auto-expire
TTL_OPERATIONAL = "operational"  # runbooks, incident reports — expire after 90 days
TTL_EPHEMERAL   = "ephemeral"    # sprint notes, PR descriptions — expire after 14 days
TTL_VOLATILE    = "volatile"     # meeting notes, slack messages — expire after 7 days


@dataclass
class ConnectorConfig:
    """
    Identifies a registered source and carries everything the connector needs to
    connect, authenticate, and control the sync cadence.

    ``credentials`` is an opaque dict. For local dev it may contain plaintext values;
    in production, values should be references to a secret store. The connector is
    responsible for resolving references — the framework treats this dict as a black box.
    """
    source_id: str           # UUID from workspace_sources table
    workspace_id: str
    source_type: str         # "notion" | "slack" | "salesforce" | "code" | …
    credentials: dict        # opaque; connector decrypts / resolves
    sync_config: dict        # e.g. {"poll_interval_seconds": 300, "max_artifacts": 1000}

    def get_sync_option(self, key: str, default=None):
        return self.sync_config.get(key, default)


@dataclass
class SourceArtifact:
    """
    A single addressable unit of knowledge from a source.

    ``urn`` format: source://<source_type>/<artifact_kind>/<artifact_id>@<workspace_id>
    Example: source://notion/page/abc123@ws-42

    ``content`` is normalized plain text — the connector is responsible for stripping
    HTML, markdown, or other markup before returning the artifact. This keeps the
    ingestion pipeline markup-agnostic.

    ``metadata`` carries source-specific structured data for downstream enrichment
    (e.g. Notion page properties, Slack channel info, Salesforce record type).

    ``ttl_class`` defaults to ``operational``. Connectors should override for artifacts
    whose longevity is well-known (e.g. architecture docs → ``permanent``, DMs → ``volatile``).
    """
    urn: str                          # source://notion/page/abc123@workspace-id
    title: str
    content: str                      # plain text, ready for embedding
    metadata: dict = field(default_factory=dict)
    last_modified: datetime = field(default_factory=datetime.utcnow)
    source_type: str = ""
    ttl_class: str = TTL_OPERATIONAL  # ADR-0064 tier

    def __post_init__(self) -> None:
        valid_ttls = {TTL_PERMANENT, TTL_OPERATIONAL, TTL_EPHEMERAL, TTL_VOLATILE}
        if self.ttl_class not in valid_ttls:
            raise ValueError(
                f"Invalid ttl_class {self.ttl_class!r}. Must be one of {valid_ttls}."
            )


class BaseConnector(ABC):
    """
    Abstract base for all source connectors.

    Lifecycle:
      1. Instantiate with ConnectorConfig.
      2. Call validate_credentials() — raises or returns False if auth fails.
      3. Call list_artifacts(since=cursor_dt) to stream artifacts for ingestion.
      4. Optionally call fetch_artifact(urn) to refresh a single artifact.
      5. Call get_sync_cursor() after sync to persist resumable state.

    Push (webhook) support is opt-in:
      - Override supports_webhooks() → True
      - Override handle_webhook(payload) → list[SourceArtifact]
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config

    @property
    def config(self) -> ConnectorConfig:
        return self._config

    # ── Required methods ──────────────────────────────────────────────────────

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """
        Test that the connector can reach the source with the provided credentials.

        Returns True on success. Should raise a descriptive exception (not just return
        False) so that error messages surface to the user in the UI.
        """

    @abstractmethod
    async def list_artifacts(
        self, since: Optional[datetime] = None
    ) -> AsyncIterator[SourceArtifact]:
        """
        Yield artifacts from the source.

        If ``since`` is provided, yield only artifacts modified after that timestamp
        (incremental sync). If None, yield all artifacts (full sync).

        Implementations should yield artifacts as they are fetched — do not buffer
        the full result set in memory, especially for large sources.
        """
        # Make this method a generator even in the abstract declaration so that
        # subclasses can use `yield` and still satisfy the return type.
        # This pragma is a type-checker hint; the body never executes.
        return  # pragma: no cover
        yield    # pragma: no cover  # noqa: unreachable

    @abstractmethod
    async def fetch_artifact(self, artifact_urn: str) -> SourceArtifact:
        """
        Fetch a single artifact by URN.

        Used for on-demand refresh (e.g. when a webhook signals a single document update)
        and for re-fetching artifacts that failed during a previous sync.
        """

    @abstractmethod
    async def get_sync_cursor(self) -> dict:
        """
        Return an opaque cursor that can be passed back as ``since`` on the next sync.

        The cursor is persisted by ConnectorIngestionPipeline after each successful sync.
        Connectors choose the internal shape; typical content:
          {"last_sync_ts": "2026-05-17T12:00:00Z", "page_token": "...", "etag": "..."}
        """

    # ── Optional push interface ───────────────────────────────────────────────

    def supports_webhooks(self) -> bool:
        """Return True if this connector can receive webhook push events."""
        return False

    async def handle_webhook(self, payload: dict) -> list[SourceArtifact]:
        """
        Process an inbound webhook payload and return affected artifacts.

        Only called if supports_webhooks() returns True. Default raises NotImplementedError
        so that connectors which advertise webhook support but forget to implement this
        fail loudly rather than silently dropping events.
        """
        raise NotImplementedError(
            f"{type(self).__name__} advertises webhook support but does not implement "
            "handle_webhook(). Override this method."
        )

    # ── Convenience helpers ───────────────────────────────────────────────────

    def _make_urn(self, artifact_kind: str, artifact_id: str) -> str:
        """Build a canonical source URN for this connector's source_type and workspace."""
        return (
            f"source://{self._config.source_type}/{artifact_kind}"
            f"/{artifact_id}@{self._config.workspace_id}"
        )
