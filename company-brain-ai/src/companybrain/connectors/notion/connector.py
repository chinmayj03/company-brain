"""
B1.4 Notion connector — main connector implementation.

Authenticates via Notion integration token (NOTION_API_TOKEN env var).
Discovers pages via the /search API, fetches block content, emits
SourceArtifacts, and supports incremental sync via last_edited_time cursor.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from companybrain.connectors.base import BaseConnector, ConnectorConfig, SourceArtifact
from companybrain.connectors.registry import register
from companybrain.connectors.notion.api_client import NotionApiClient
from companybrain.connectors.notion.block_parser import (
    parse_page_content,
    extract_entity_mentions,
)
from companybrain.connectors.notion.sync_cursor import NotionSyncCursor


@register("notion")
class NotionConnector(BaseConnector):
    """
    Connector for Notion workspace pages and databases.

    Configuration keys (via ConnectorConfig):
      credentials.api_token  — Notion integration token (falls back to
                                NOTION_API_TOKEN env var)
      extra.state_path       — path to JSON cursor file
                                (default: .brain/notion_cursor.json)

    Env vars:
      NOTION_API_TOKEN       — integration token
      NOTION_WORKSPACE_ID    — used as the workspace_id cursor key
    """

    SOURCE_TYPE = "notion"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._token: str = (
            config.credentials.get("api_token")
            or os.environ.get("NOTION_API_TOKEN", "")
        )
        self._workspace_id: str = config.workspace_id
        self._cursor = NotionSyncCursor(
            Path(config.extra.get("state_path", ".brain/notion_cursor.json"))
        )

    # ── BaseConnector API ───────────────────────────────────────────────────

    async def validate_credentials(self) -> bool:
        """Return True if the integration token can reach the Notion API."""
        if not self._token:
            return False
        try:
            async with NotionApiClient(self._token) as client:
                await client.get("/users/me")
            return True
        except Exception:
            return False

    async def list_artifacts(self) -> AsyncIterator[SourceArtifact]:
        """
        Yield SourceArtifact stubs for all Notion pages accessible to the
        integration.

        Incremental mode: if a cursor exists, results are fetched in
        descending last_edited_time order and iteration stops when a page
        older than the cursor is encountered.
        """
        since = self._cursor.get_cursor(self._workspace_id)
        filter_body: dict = {
            "filter": {"value": "page", "property": "object"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }

        async with NotionApiClient(self._token) as client:
            async for page in client.paginate("/search", filter_body):
                edited_at = page.get("last_edited_time", "")

                # Stop early when we've reached pages older than the cursor
                if since and edited_at and edited_at <= since:
                    break

                title = _page_title(page)
                yield SourceArtifact(
                    id=page["id"],
                    source_type="notion",
                    title=title,
                    url=page.get("url", ""),
                    metadata={
                        "page_id": page["id"],
                        "last_edited_time": edited_at,
                        "created_time": page.get("created_time", ""),
                        "parent_type": page.get("parent", {}).get("type", ""),
                    },
                    ttl_class="standard",
                )

    async def fetch_artifact(self, artifact: SourceArtifact) -> SourceArtifact:
        """
        Fetch full block content for the given page artifact.

        Returns a new SourceArtifact with `content` populated and enriched
        metadata (entity_mentions, block_count, content_length).
        """
        blocks: list[dict] = []
        async with NotionApiClient(self._token) as client:
            async for block in client.paginate(f"/blocks/{artifact.id}/children"):
                blocks.append(block)

        content = parse_page_content(blocks)
        mentions = extract_entity_mentions(content)

        return SourceArtifact(
            id=artifact.id,
            source_type=artifact.source_type,
            title=artifact.title,
            url=artifact.url,
            content=content,
            metadata={
                **artifact.metadata,
                "entity_mentions": mentions,
                "block_count": len(blocks),
                "content_length": len(content),
            },
            ttl_class=artifact.ttl_class,
        )

    async def get_sync_cursor(self) -> str | None:
        """Return the stored cursor for this workspace (or None)."""
        return self._cursor.get_cursor(self._workspace_id)

    def mark_sync_complete(self) -> None:
        """Advance the cursor to now so the next run is incremental."""
        self._cursor.set_cursor(
            self._workspace_id,
            datetime.now(timezone.utc).isoformat(),
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _page_title(page: dict) -> str:
    """
    Extract the human-readable title from a Notion page object.

    Tries the most common property keys in order; falls back to the page id.
    """
    props = page.get("properties", {})
    for key in ("title", "Name", "Title"):
        if key in props:
            rt = props[key].get("title", [])
            text = "".join(t.get("plain_text", "") for t in rt)
            if text:
                return text
    # Final fallback: truncated page id
    return page.get("id", "Untitled")[:40]
