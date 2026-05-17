"""
Unit tests for ADR-0092 connector base module.

Tests:
  - ConnectorConfig construction and option accessors
  - SourceArtifact defaults, TTL validation, post-init guard
  - BaseConnector interface enforcement (abstract methods must be implemented)
  - _make_urn helper produces correct URN format
  - supports_webhooks() default is False
  - handle_webhook() default raises NotImplementedError
"""
from __future__ import annotations

import pytest
from datetime import datetime

from companybrain.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    SourceArtifact,
    TTL_EPHEMERAL,
    TTL_OPERATIONAL,
    TTL_PERMANENT,
    TTL_VOLATILE,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_config(**overrides) -> ConnectorConfig:
    defaults = dict(
        source_id="src-001",
        workspace_id="ws-test",
        source_type="test",
        credentials={"token": "secret"},
        sync_config={"poll_interval_seconds": 60, "max_artifacts": 100},
    )
    defaults.update(overrides)
    return ConnectorConfig(**defaults)


# ── ConnectorConfig ───────────────────────────────────────────────────────────

class TestConnectorConfig:
    def test_construction(self):
        cfg = _make_config()
        assert cfg.source_id == "src-001"
        assert cfg.workspace_id == "ws-test"
        assert cfg.source_type == "test"
        assert cfg.credentials == {"token": "secret"}

    def test_get_sync_option_present(self):
        cfg = _make_config()
        assert cfg.get_sync_option("poll_interval_seconds") == 60

    def test_get_sync_option_missing_with_default(self):
        cfg = _make_config()
        assert cfg.get_sync_option("nonexistent", "fallback") == "fallback"

    def test_get_sync_option_missing_no_default(self):
        cfg = _make_config()
        assert cfg.get_sync_option("nonexistent") is None


# ── SourceArtifact ────────────────────────────────────────────────────────────

class TestSourceArtifact:
    def test_minimal_construction(self):
        a = SourceArtifact(urn="source://test/doc/1@ws", title="Hello", content="World")
        assert a.urn == "source://test/doc/1@ws"
        assert a.ttl_class == TTL_OPERATIONAL  # default
        assert isinstance(a.last_modified, datetime)

    def test_all_ttl_classes_accepted(self):
        for ttl in (TTL_PERMANENT, TTL_OPERATIONAL, TTL_EPHEMERAL, TTL_VOLATILE):
            a = SourceArtifact(urn="u", title="t", content="c", ttl_class=ttl)
            assert a.ttl_class == ttl

    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError, match="Invalid ttl_class"):
            SourceArtifact(urn="u", title="t", content="c", ttl_class="unknown")

    def test_metadata_defaults_to_empty_dict(self):
        a = SourceArtifact(urn="u", title="t", content="c")
        assert a.metadata == {}

    def test_explicit_last_modified(self):
        ts = datetime(2026, 1, 15, 12, 0, 0)
        a = SourceArtifact(urn="u", title="t", content="c", last_modified=ts)
        assert a.last_modified == ts


# ── BaseConnector abstract enforcement ───────────────────────────────────────

class TestBaseConnectorAbstract:
    def test_cannot_instantiate_directly(self):
        """BaseConnector has abstract methods — direct instantiation must fail."""
        with pytest.raises(TypeError):
            BaseConnector(_make_config())  # type: ignore[abstract]

    def test_concrete_subclass_works(self):
        """A concrete subclass that implements all abstract methods can be instantiated."""
        class ConcreteConnector(BaseConnector):
            async def validate_credentials(self) -> bool:
                return True

            async def list_artifacts(self, since=None):
                return
                yield  # noqa

            async def fetch_artifact(self, urn: str) -> SourceArtifact:
                return SourceArtifact(urn=urn, title="t", content="c")

            async def get_sync_cursor(self) -> dict:
                return {}

        cfg = _make_config()
        c = ConcreteConnector(cfg)
        assert c.config is cfg

    def test_missing_one_abstract_method_raises(self):
        """Subclass missing get_sync_cursor cannot be instantiated."""
        class Incomplete(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                return; yield
            async def fetch_artifact(self, urn): ...
            # get_sync_cursor missing

        with pytest.raises(TypeError):
            Incomplete(_make_config())  # type: ignore[abstract]


# ── Default webhook behavior ──────────────────────────────────────────────────

class TestWebhookDefaults:
    def _make_concrete(self) -> BaseConnector:
        class C(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                return; yield
            async def fetch_artifact(self, urn):
                return SourceArtifact(urn=urn, title="", content="")
            async def get_sync_cursor(self): return {}

        return C(_make_config())

    def test_supports_webhooks_default_false(self):
        c = self._make_concrete()
        assert c.supports_webhooks() is False

    @pytest.mark.asyncio
    async def test_handle_webhook_default_raises(self):
        c = self._make_concrete()
        with pytest.raises(NotImplementedError, match="handle_webhook"):
            await c.handle_webhook({})


# ── _make_urn helper ──────────────────────────────────────────────────────────

class TestMakeUrn:
    def _make_concrete(self, source_type="notion", workspace_id="ws-42") -> BaseConnector:
        class C(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                return; yield
            async def fetch_artifact(self, urn):
                return SourceArtifact(urn=urn, title="", content="")
            async def get_sync_cursor(self): return {}

        return C(_make_config(source_type=source_type, workspace_id=workspace_id))

    def test_urn_format(self):
        c = self._make_concrete(source_type="notion", workspace_id="ws-42")
        urn = c._make_urn("page", "abc123")
        assert urn == "source://notion/page/abc123@ws-42"

    def test_urn_with_path_separator(self):
        c = self._make_concrete(source_type="code", workspace_id="ws-1")
        urn = c._make_urn("file", "src/main.py")
        assert urn == "source://code/file/src/main.py@ws-1"
