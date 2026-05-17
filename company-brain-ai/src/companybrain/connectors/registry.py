"""
B1.2 Connector registry — maps source-type strings to connector classes.

Usage:
    from companybrain.connectors.registry import ConnectorRegistry, register

    @register("slack")
    class SlackConnector(BaseConnector):
        ...

    cls = ConnectorRegistry.get("slack")   # → SlackConnector
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from companybrain.connectors.base import BaseConnector


class ConnectorRegistry:
    """Simple class-level registry; no external dependencies."""

    _registry: dict[str, Type["BaseConnector"]] = {}

    @classmethod
    def register(cls, source_type: str, connector_cls: Type["BaseConnector"]) -> None:
        cls._registry[source_type] = connector_cls

    @classmethod
    def get(cls, source_type: str) -> Type["BaseConnector"]:
        if source_type not in cls._registry:
            raise KeyError(f"No connector registered for source_type={source_type!r}")
        return cls._registry[source_type]

    @classmethod
    def list_registered(cls) -> list[str]:
        return list(cls._registry.keys())


def register(source_type: str):
    """Class decorator that registers a connector under the given source_type key."""

    def decorator(connector_cls: Type["BaseConnector"]) -> Type["BaseConnector"]:
        ConnectorRegistry.register(source_type, connector_cls)
        return connector_cls

    return decorator
