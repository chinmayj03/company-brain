"""
Unit tests for ADR-0092 ConnectorIngestionPipeline.

Key scenarios:
  - 10 mock artifacts processed in < 5 seconds (acceptance criterion)
  - SyncResult counts are correct (seen / stored / failed / pii)
  - Connector not registered → error in result, no raise
  - Credentials validation fails → error in result
  - Artifact store error → counted as failed, sync continues
  - PII scan rejection → counted as skipped_pii
  - Cursor is persisted after successful sync
  - full=True ignores stored cursor
  - Incremental sync (full=False) passes since datetime to list_artifacts
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    SourceArtifact,
    TTL_OPERATIONAL,
)
from companybrain.connectors.pipeline import ConnectorIngestionPipeline, SyncResult
from companybrain.connectors.registry import ConnectorRegistry
from companybrain.store.base import BrainEntity, BrainStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(source_type: str = "mock") -> ConnectorConfig:
    return ConnectorConfig(
        source_id="src-test",
        workspace_id="ws-test",
        source_type=source_type,
        credentials={},
        sync_config={},
    )


def _make_artifact(n: int) -> SourceArtifact:
    return SourceArtifact(
        urn=f"source://mock/doc/{n}@ws-test",
        title=f"Document {n}",
        content=f"Content of document {n}",
        source_type="mock",
        ttl_class=TTL_OPERATIONAL,
    )


class InMemoryStore(BrainStore):
    """Lightweight in-memory store for testing."""

    def __init__(self):
        self.written: list[BrainEntity] = []
        self.committed: list[str] = []

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        self.written.append(entity)

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        for e in self.written:
            if e.id == entity_id:
                return e
        return None

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        return False

    async def list_ids(self):
        for e in self.written:
            yield e.id

    async def commit_run(self, run_id: str) -> None:
        self.committed.append(run_id)


def _make_mock_connector_cls(
    artifacts: list[SourceArtifact],
    validate_return: bool = True,
    validate_raises: Exception | None = None,
) -> type[BaseConnector]:
    """Create a connector class that yields the given artifacts."""

    class MockConnector(BaseConnector):
        _artifacts = artifacts
        _validate_return = validate_return
        _validate_raises = validate_raises

        async def validate_credentials(self) -> bool:
            if self._validate_raises:
                raise self._validate_raises
            return self._validate_return

        async def list_artifacts(
            self, since: Optional[datetime] = None
        ) -> AsyncIterator[SourceArtifact]:
            for a in self._artifacts:
                yield a

        async def fetch_artifact(self, urn: str) -> SourceArtifact:
            for a in self._artifacts:
                if a.urn == urn:
                    return a
            raise KeyError(f"Not found: {urn}")

        async def get_sync_cursor(self) -> dict:
            return {"last_sync_ts": datetime.utcnow().isoformat()}

    return MockConnector


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_registry():
    ConnectorRegistry._reset()
    yield
    ConnectorRegistry._reset()


# ── Core pipeline tests ───────────────────────────────────────────────────────

class TestPipelineSync:
    @pytest.mark.asyncio
    async def test_10_artifacts_under_5_seconds(self):
        """Acceptance criterion: 10 artifacts processed in < 5s."""
        artifacts = [_make_artifact(i) for i in range(10)]
        ConnectorRegistry.register("mock")(_make_mock_connector_cls(artifacts))

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        config = _make_config("mock")

        t0 = time.monotonic()
        result = await pipeline.run_sync("src-test", config=config)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"Sync took {elapsed:.2f}s — exceeded 5s budget"
        assert result.artifacts_seen == 10
        assert result.artifacts_stored == 10
        assert result.artifacts_failed == 0
        assert result.success is True

    @pytest.mark.asyncio
    async def test_artifacts_written_to_store(self):
        artifacts = [_make_artifact(i) for i in range(3)]
        ConnectorRegistry.register("mock")(_make_mock_connector_cls(artifacts))

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        await pipeline.run_sync("src-test", config=_make_config("mock"))

        assert len(store.written) == 3
        urns = {e.id for e in store.written}
        assert urns == {a.urn for a in artifacts}

    @pytest.mark.asyncio
    async def test_commit_run_called(self):
        ConnectorRegistry.register("mock")(_make_mock_connector_cls([]))
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        await pipeline.run_sync("src-test", config=_make_config("mock"))
        assert len(store.committed) == 1

    @pytest.mark.asyncio
    async def test_result_has_source_info(self):
        ConnectorRegistry.register("mock")(_make_mock_connector_cls([]))
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("mock"))
        assert result.source_id == "src-test"
        assert result.source_type == "mock"

    @pytest.mark.asyncio
    async def test_duration_recorded(self):
        ConnectorRegistry.register("mock")(_make_mock_connector_cls([]))
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("mock"))
        assert result.duration_seconds >= 0.0


# ── Error handling ────────────────────────────────────────────────────────────

class TestPipelineErrors:
    @pytest.mark.asyncio
    async def test_connector_not_registered_returns_error(self):
        """Unregistered source_type → error in result, no exception propagates."""
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("unregistered"))
        assert result.success is False
        assert any("No connector registered" in e for e in result.errors)
        assert result.artifacts_stored == 0

    @pytest.mark.asyncio
    async def test_credentials_validation_returns_false(self):
        ConnectorRegistry.register("bad_creds")(
            _make_mock_connector_cls([], validate_return=False)
        )
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("bad_creds"))
        assert result.success is False
        assert any("Credentials validation" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_credentials_validation_raises(self):
        ConnectorRegistry.register("creds_err")(
            _make_mock_connector_cls(
                [], validate_raises=ConnectionRefusedError("auth failed")
            )
        )
        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("creds_err"))
        assert result.success is False
        assert any("auth failed" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_store_write_error_counted_as_failed(self):
        """If the store write fails, artifact is counted as failed and sync continues."""
        artifacts = [_make_artifact(i) for i in range(3)]
        ConnectorRegistry.register("mock")(_make_mock_connector_cls(artifacts))

        class BrokenStore(InMemoryStore):
            async def write(self, entity, *, run_id, workspace_id):
                raise RuntimeError("DB down")

        store = BrokenStore()
        pipeline = ConnectorIngestionPipeline(store=store)
        result = await pipeline.run_sync("src-test", config=_make_config("mock"))

        assert result.artifacts_seen == 3
        assert result.artifacts_stored == 0
        assert result.artifacts_failed == 3
        # Sync continues despite errors — not a hard failure
        assert result.duration_seconds >= 0.0


# ── PII scan ──────────────────────────────────────────────────────────────────

class TestPIIScan:
    @pytest.mark.asyncio
    async def test_pii_rejected_artifact_skipped(self):
        """Artifacts flagged by PII scanner are counted as skipped_pii."""
        artifacts = [_make_artifact(i) for i in range(5)]
        ConnectorRegistry.register("mock")(_make_mock_connector_cls(artifacts))

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)

        # Patch _try_pii_scan to reject all artifacts
        with patch(
            "companybrain.connectors.pipeline._try_pii_scan", return_value=False
        ):
            result = await pipeline.run_sync("src-test", config=_make_config("mock"))

        assert result.artifacts_seen == 5
        assert result.artifacts_skipped_pii == 5
        assert result.artifacts_stored == 0

    @pytest.mark.asyncio
    async def test_pii_pass_artifact_stored(self):
        """Artifacts that pass PII scan are stored normally."""
        artifacts = [_make_artifact(i) for i in range(3)]
        ConnectorRegistry.register("mock")(_make_mock_connector_cls(artifacts))

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(store=store)

        with patch(
            "companybrain.connectors.pipeline._try_pii_scan", return_value=True
        ):
            result = await pipeline.run_sync("src-test", config=_make_config("mock"))

        assert result.artifacts_stored == 3
        assert result.artifacts_skipped_pii == 0


# ── Cursor handling ───────────────────────────────────────────────────────────

class TestCursorHandling:
    @pytest.mark.asyncio
    async def test_cursor_stored_after_sync(self):
        ConnectorRegistry.register("mock")(_make_mock_connector_cls([]))

        stored_cursors: dict[str, dict] = {}

        async def cursor_store(source_id: str, cursor: dict) -> None:
            stored_cursors[source_id] = cursor

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(
            store=store,
            cursor_store=cursor_store,
        )
        result = await pipeline.run_sync("src-test", config=_make_config("mock"))

        assert "src-test" in stored_cursors
        assert "last_sync_ts" in stored_cursors["src-test"]
        assert result.cursor == stored_cursors["src-test"]

    @pytest.mark.asyncio
    async def test_full_sync_ignores_cursor(self):
        """full=True should pass since=None to list_artifacts regardless of stored cursor."""
        received_since: list[Optional[datetime]] = []

        class TrackingSince(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                received_since.append(since)
                return; yield
            async def fetch_artifact(self, urn):
                return SourceArtifact(urn=urn, title="", content="")
            async def get_sync_cursor(self): return {"last_sync_ts": "2026-01-01T00:00:00"}

        ConnectorRegistry.register("tracking")(TrackingSince)

        async def cursor_loader(source_id: str) -> dict:
            return {"last_sync_ts": "2026-01-01T00:00:00"}

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(
            store=store,
            cursor_loader=cursor_loader,
        )
        await pipeline.run_sync("src-test", full=True, config=_make_config("tracking"))

        assert received_since == [None]

    @pytest.mark.asyncio
    async def test_incremental_sync_passes_since(self):
        """full=False with a stored cursor should pass the cursor's datetime to list_artifacts."""
        received_since: list[Optional[datetime]] = []

        class TrackingSince(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                received_since.append(since)
                return; yield
            async def fetch_artifact(self, urn):
                return SourceArtifact(urn=urn, title="", content="")
            async def get_sync_cursor(self): return {}

        ConnectorRegistry.register("tracking")(TrackingSince)

        async def cursor_loader(source_id: str) -> dict:
            return {"last_sync_ts": "2026-05-01T12:00:00"}

        store = InMemoryStore()
        pipeline = ConnectorIngestionPipeline(
            store=store,
            cursor_loader=cursor_loader,
        )
        await pipeline.run_sync("src-test", full=False, config=_make_config("tracking"))

        assert len(received_since) == 1
        assert received_since[0] == datetime(2026, 5, 1, 12, 0, 0)
