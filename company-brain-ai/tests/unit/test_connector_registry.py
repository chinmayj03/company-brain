"""
Unit tests for ADR-0092 ConnectorRegistry.

Tests:
  - @register decorator registers a connector class
  - get() returns the correct class
  - get() raises KeyError for unknown types
  - list_registered() returns sorted list
  - Double-registering the same class is idempotent (safe re-import)
  - Double-registering a different class raises ValueError
  - _reset() clears all registrations (test isolation)
"""
from __future__ import annotations

import pytest

from companybrain.connectors.base import BaseConnector, ConnectorConfig, SourceArtifact
from companybrain.connectors.registry import ConnectorRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(source_type: str = "mock") -> ConnectorConfig:
    return ConnectorConfig(
        source_id="src-001",
        workspace_id="ws-test",
        source_type=source_type,
        credentials={},
        sync_config={},
    )


def _make_connector_cls(name: str = "MockConnector") -> type[BaseConnector]:
    """Dynamically create a minimal concrete connector class."""
    class C(BaseConnector):
        async def validate_credentials(self): return True
        async def list_artifacts(self, since=None):
            return; yield
        async def fetch_artifact(self, urn):
            return SourceArtifact(urn=urn, title="", content="")
        async def get_sync_cursor(self): return {}

    C.__name__ = name
    C.__qualname__ = name
    return C


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure registry is clean before and after each test."""
    ConnectorRegistry._reset()
    yield
    ConnectorRegistry._reset()


# ── Registration ─────────────────────────────────────────────────────────────

class TestRegister:
    def test_register_and_get(self):
        Cls = _make_connector_cls("Alpha")
        ConnectorRegistry.register("alpha")(Cls)
        assert ConnectorRegistry.get("alpha") is Cls

    def test_register_multiple(self):
        A = _make_connector_cls("A")
        B = _make_connector_cls("B")
        ConnectorRegistry.register("type_a")(A)
        ConnectorRegistry.register("type_b")(B)
        assert ConnectorRegistry.get("type_a") is A
        assert ConnectorRegistry.get("type_b") is B

    def test_register_as_decorator(self):
        @ConnectorRegistry.register("decorated")
        class DecoratedConnector(BaseConnector):
            async def validate_credentials(self): return True
            async def list_artifacts(self, since=None):
                return; yield
            async def fetch_artifact(self, urn):
                return SourceArtifact(urn=urn, title="", content="")
            async def get_sync_cursor(self): return {}

        assert ConnectorRegistry.get("decorated") is DecoratedConnector

    def test_decorator_returns_class_unchanged(self):
        """The @register decorator must return the class itself so the module-level
        name still refers to the connector class (not a wrapper)."""
        Cls = _make_connector_cls("ReturnCheck")
        result = ConnectorRegistry.register("return_check")(Cls)
        assert result is Cls


# ── get() ─────────────────────────────────────────────────────────────────────

class TestGet:
    def test_get_unknown_raises_key_error(self):
        with pytest.raises(KeyError, match="No connector registered"):
            ConnectorRegistry.get("nonexistent")

    def test_error_message_mentions_registered_types(self):
        Cls = _make_connector_cls()
        ConnectorRegistry.register("known")(Cls)
        with pytest.raises(KeyError, match="known"):
            ConnectorRegistry.get("unknown")

    def test_error_message_when_empty(self):
        with pytest.raises(KeyError, match="\\(none\\)"):
            ConnectorRegistry.get("anything")


# ── list_registered() ─────────────────────────────────────────────────────────

class TestListRegistered:
    def test_empty(self):
        assert ConnectorRegistry.list_registered() == []

    def test_single(self):
        ConnectorRegistry.register("solo")(_make_connector_cls())
        assert ConnectorRegistry.list_registered() == ["solo"]

    def test_sorted(self):
        for name in ["zebra", "apple", "mango"]:
            ConnectorRegistry.register(name)(_make_connector_cls(name))
        assert ConnectorRegistry.list_registered() == ["apple", "mango", "zebra"]


# ── Idempotency and conflict detection ───────────────────────────────────────

class TestRegistrationConflicts:
    def test_registering_same_class_twice_is_idempotent(self):
        """Re-importing a module registers the same class again — must not raise."""
        Cls = _make_connector_cls()
        ConnectorRegistry.register("safe")(Cls)
        ConnectorRegistry.register("safe")(Cls)  # Same class — OK
        assert ConnectorRegistry.get("safe") is Cls

    def test_registering_different_class_same_type_raises(self):
        """Two different classes cannot claim the same source_type."""
        A = _make_connector_cls("A")
        B = _make_connector_cls("B")
        ConnectorRegistry.register("conflict")(A)
        with pytest.raises(ValueError, match="already registered"):
            ConnectorRegistry.register("conflict")(B)
