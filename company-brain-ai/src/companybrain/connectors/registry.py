"""
ADR-0092 — ConnectorRegistry: plugin map from source_type → BaseConnector class.

Usage (in a connector module):

    from companybrain.connectors.registry import ConnectorRegistry
    from companybrain.connectors.base import BaseConnector

    @ConnectorRegistry.register("notion")
    class NotionConnector(BaseConnector):
        ...

Usage (in pipeline / tests):

    cls = ConnectorRegistry.get("notion")       # KeyError if not registered
    connector = cls(config)

    registered = ConnectorRegistry.list_registered()  # ["code", "notion", ...]

Design note: the registry is a class-level dict so it survives across imports without
needing a singleton instance. Connectors self-register at import time, which means you
only need to import the connector module once (e.g. in connectors/__init__.py) to make
it available to the pipeline.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from companybrain.connectors.base import BaseConnector


class ConnectorRegistry:
    """Maps source_type strings to BaseConnector subclasses."""

    _registry: dict[str, type["BaseConnector"]] = {}

    @classmethod
    def register(cls, source_type: str):
        """
        Class decorator that registers a connector under the given source_type.

        Example::

            @ConnectorRegistry.register("notion")
            class NotionConnector(BaseConnector): ...

        Registering the same source_type twice raises ValueError so that
        accidental double-imports surface immediately rather than silently
        overwriting a working connector.
        """
        def decorator(connector_cls: type["BaseConnector"]) -> type["BaseConnector"]:
            if source_type in cls._registry:
                existing = cls._registry[source_type]
                if existing is not connector_cls:
                    raise ValueError(
                        f"ConnectorRegistry: source_type {source_type!r} is already "
                        f"registered as {existing.__qualname__}. Cannot register "
                        f"{connector_cls.__qualname__} under the same name."
                    )
            cls._registry[source_type] = connector_cls
            return connector_cls

        return decorator

    @classmethod
    def get(cls, source_type: str) -> type["BaseConnector"]:
        """
        Return the connector class for ``source_type``.

        Raises KeyError with a helpful message that lists registered types.
        """
        if source_type not in cls._registry:
            registered = ", ".join(sorted(cls._registry)) or "(none)"
            raise KeyError(
                f"No connector registered for source_type={source_type!r}. "
                f"Registered types: {registered}. "
                "Import the connector module to trigger self-registration."
            )
        return cls._registry[source_type]

    @classmethod
    def list_registered(cls) -> list[str]:
        """Return a sorted list of all registered source_type strings."""
        return sorted(cls._registry.keys())

    @classmethod
    def _reset(cls) -> None:
        """
        Clear all registrations. Only used in tests to ensure isolation.
        Do NOT call in production code.
        """
        cls._registry.clear()
