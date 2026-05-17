"""
B1.4 Notion connector — import triggers self-registration via @register decorator.

Import this package to register "notion" in ConnectorRegistry:
    import companybrain.connectors.notion  # side-effect: registers the connector
"""
from companybrain.connectors.notion.connector import NotionConnector

__all__ = ["NotionConnector"]
