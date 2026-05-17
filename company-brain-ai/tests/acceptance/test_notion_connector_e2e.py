"""
Acceptance tests for B1.4 Notion connector (no live API).

All Notion API calls are intercepted via unittest.mock; no network traffic.

Coverage:
  - ConnectorRegistry.get("notion") returns NotionConnector
  - validate_credentials() with mocked API call
  - list_artifacts() yields SourceArtifact for each mocked page
  - fetch_artifact() returns artifact with content populated
  - artifact_to_brain_entities() returns BrainEntity list
  - Incremental sync: second run with cursor stops at old pages
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Trigger connector registration
import companybrain.connectors.notion  # noqa: F401

from companybrain.connectors.base import ConnectorConfig, SourceArtifact
from companybrain.connectors.notion.connector import NotionConnector
from companybrain.connectors.notion.evidence_emitter import artifact_to_brain_entities
from companybrain.connectors.registry import ConnectorRegistry
from companybrain.store.base import BrainEntity


# ── Fixtures ─────────────────────────────────────────────────────────────────

FAKE_TOKEN = "secret_test_token_abc123"

MOCK_PAGE_1 = {
    "object": "page",
    "id": "page-uuid-0001",
    "url": "https://notion.so/page-uuid-0001",
    "last_edited_time": "2026-05-10T12:00:00.000Z",
    "created_time": "2026-04-01T08:00:00.000Z",
    "parent": {"type": "workspace", "workspace": True},
    "properties": {
        "title": {
            "title": [{"plain_text": "Onboarding Guide"}]
        }
    },
}

MOCK_PAGE_2 = {
    "object": "page",
    "id": "page-uuid-0002",
    "url": "https://notion.so/page-uuid-0002",
    "last_edited_time": "2026-03-01T09:00:00.000Z",
    "created_time": "2026-02-01T08:00:00.000Z",
    "parent": {"type": "database_id", "database_id": "db-001"},
    "properties": {
        "Name": {
            "title": [{"plain_text": "PriorAuth Workflow"}]
        }
    },
}

MOCK_BLOCKS_PAGE_1 = [
    {
        "type": "heading_1",
        "heading_1": {"rich_text": [{"plain_text": "Welcome"}]},
    },
    {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"plain_text": "This guide covers ClaimProcessor setup."}]
        },
    },
]


def _make_config(tmp_path: Path) -> ConnectorConfig:
    return ConnectorConfig(
        workspace_id="ws-test-001",
        credentials={"api_token": FAKE_TOKEN},
        extra={"state_path": str(tmp_path / "notion_cursor.json")},
    )


async def _async_gen(items):
    """Helper: yield items from a list as an async generator."""
    for item in items:
        yield item


# ── Registry ─────────────────────────────────────────────────────────────────

class TestNotionConnectorRegistry:
    def test_connector_registered_as_notion(self):
        cls = ConnectorRegistry.get("notion")
        assert cls is NotionConnector

    def test_notion_in_registered_list(self):
        assert "notion" in ConnectorRegistry.list_registered()


# ── validate_credentials ─────────────────────────────────────────────────────

class TestValidateCredentials:
    @pytest.mark.asyncio
    async def test_returns_true_on_successful_api_call(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.get = AsyncMock(return_value={"object": "user"})
            MockClient.return_value = instance

            result = await connector.validate_credentials()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.get = AsyncMock(side_effect=Exception("401 Unauthorized"))
            MockClient.return_value = instance

            result = await connector.validate_credentials()

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_token(self, tmp_path: Path):
        config = ConnectorConfig(
            workspace_id="ws-test",
            credentials={},
            extra={"state_path": str(tmp_path / "cursor.json")},
        )
        connector = NotionConnector(config)
        result = await connector.validate_credentials()
        assert result is False


# ── list_artifacts ────────────────────────────────────────────────────────────

class TestListArtifacts:
    @pytest.mark.asyncio
    async def test_yields_source_artifact_for_each_page(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen([MOCK_PAGE_1, MOCK_PAGE_2])
            )
            MockClient.return_value = instance

            artifacts = []
            async for art in connector.list_artifacts():
                artifacts.append(art)

        assert len(artifacts) == 2
        assert all(isinstance(a, SourceArtifact) for a in artifacts)

    @pytest.mark.asyncio
    async def test_artifact_has_correct_fields(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen([MOCK_PAGE_1])
            )
            MockClient.return_value = instance

            artifacts = []
            async for art in connector.list_artifacts():
                artifacts.append(art)

        art = artifacts[0]
        assert art.id == "page-uuid-0001"
        assert art.source_type == "notion"
        assert art.title == "Onboarding Guide"
        assert art.url == "https://notion.so/page-uuid-0001"
        assert art.content is None  # content not fetched yet
        assert art.metadata["last_edited_time"] == "2026-05-10T12:00:00.000Z"

    @pytest.mark.asyncio
    async def test_incremental_sync_stops_at_cursor(self, tmp_path: Path):
        """Pages edited before cursor should be skipped (early exit)."""
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        # Set cursor between page 1 (newer) and page 2 (older)
        connector._cursor.set_cursor("ws-test-001", "2026-04-01T00:00:00+00:00")

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            # Pages sorted descending by last_edited_time (Notion API behaviour)
            instance.paginate = MagicMock(
                return_value=_async_gen([MOCK_PAGE_1, MOCK_PAGE_2])
            )
            MockClient.return_value = instance

            artifacts = []
            async for art in connector.list_artifacts():
                artifacts.append(art)

        # PAGE_1 edited 2026-05-10 > cursor 2026-04-01 → included
        # PAGE_2 edited 2026-03-01 < cursor 2026-04-01 → skipped
        assert len(artifacts) == 1
        assert artifacts[0].id == "page-uuid-0001"


# ── fetch_artifact ────────────────────────────────────────────────────────────

class TestFetchArtifact:
    @pytest.mark.asyncio
    async def test_returns_artifact_with_content(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        stub = SourceArtifact(
            id="page-uuid-0001",
            source_type="notion",
            title="Onboarding Guide",
            url="https://notion.so/page-uuid-0001",
            metadata={"last_edited_time": "2026-05-10T12:00:00.000Z"},
        )

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen(MOCK_BLOCKS_PAGE_1)
            )
            MockClient.return_value = instance

            result = await connector.fetch_artifact(stub)

        assert result.content is not None
        assert len(result.content) > 0
        assert "Welcome" in result.content
        assert "ClaimProcessor" in result.content

    @pytest.mark.asyncio
    async def test_fetch_populates_metadata(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        stub = SourceArtifact(
            id="page-uuid-0001",
            source_type="notion",
            title="Onboarding Guide",
            url="https://notion.so/page-uuid-0001",
            metadata={},
        )

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen(MOCK_BLOCKS_PAGE_1)
            )
            MockClient.return_value = instance

            result = await connector.fetch_artifact(stub)

        assert "block_count" in result.metadata
        assert result.metadata["block_count"] == len(MOCK_BLOCKS_PAGE_1)
        assert "content_length" in result.metadata
        assert "entity_mentions" in result.metadata

    @pytest.mark.asyncio
    async def test_entity_mentions_extracted(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        stub = SourceArtifact(
            id="page-uuid-0001",
            source_type="notion",
            title="Onboarding Guide",
            url="https://notion.so/page-uuid-0001",
            metadata={},
        )

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen(MOCK_BLOCKS_PAGE_1)
            )
            MockClient.return_value = instance

            result = await connector.fetch_artifact(stub)

        mentions = result.metadata.get("entity_mentions", [])
        # ClaimProcessor appears in the block text
        assert "ClaimProcessor" in mentions

    @pytest.mark.asyncio
    async def test_preserves_original_metadata(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        stub = SourceArtifact(
            id="page-uuid-0001",
            source_type="notion",
            title="Onboarding Guide",
            url="https://notion.so/page-uuid-0001",
            metadata={"last_edited_time": "2026-05-10T12:00:00.000Z"},
        )

        with patch(
            "companybrain.connectors.notion.connector.NotionApiClient"
        ) as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.paginate = MagicMock(
                return_value=_async_gen(MOCK_BLOCKS_PAGE_1)
            )
            MockClient.return_value = instance

            result = await connector.fetch_artifact(stub)

        assert result.metadata["last_edited_time"] == "2026-05-10T12:00:00.000Z"


# ── evidence_emitter ──────────────────────────────────────────────────────────

class TestArtifactToBrainEntities:
    def _hydrated_artifact(self) -> SourceArtifact:
        return SourceArtifact(
            id="page-uuid-0001",
            source_type="notion",
            title="Onboarding Guide",
            url="https://notion.so/page-uuid-0001",
            content="# Welcome\n\nThis guide covers ClaimProcessor setup.",
            metadata={
                "last_edited_time": "2026-05-10T12:00:00.000Z",
                "entity_mentions": ["ClaimProcessor", "Welcome"],
                "block_count": 2,
            },
        )

    def test_returns_list_of_brain_entities(self):
        entities = artifact_to_brain_entities(self._hydrated_artifact())
        assert isinstance(entities, list)
        assert len(entities) == 1
        assert isinstance(entities[0], BrainEntity)

    def test_entity_id_prefixed_with_notion(self):
        entity = artifact_to_brain_entities(self._hydrated_artifact())[0]
        assert entity.id == "notion:page-uuid-0001"

    def test_entity_type_is_notion_page(self):
        entity = artifact_to_brain_entities(self._hydrated_artifact())[0]
        assert entity.entity_type == "notion_page"

    def test_qualified_name_is_title(self):
        entity = artifact_to_brain_entities(self._hydrated_artifact())[0]
        assert entity.qualified_name == "Onboarding Guide"

    def test_t1_summary_is_truncated_content(self):
        artifact = self._hydrated_artifact()
        entity = artifact_to_brain_entities(artifact)[0]
        assert entity.t1_summary == artifact.content[:500]

    def test_metadata_contains_entity_mentions(self):
        entity = artifact_to_brain_entities(self._hydrated_artifact())[0]
        assert "ClaimProcessor" in entity.metadata.get("entity_mentions", [])

    def test_empty_content_returns_empty_list(self):
        artifact = SourceArtifact(
            id="page-uuid-0002",
            source_type="notion",
            title="Empty",
            url="https://notion.so/page-uuid-0002",
            content=None,
            metadata={},
        )
        assert artifact_to_brain_entities(artifact) == []

    def test_empty_string_content_returns_empty_list(self):
        artifact = SourceArtifact(
            id="page-uuid-0003",
            source_type="notion",
            title="Empty",
            url="https://notion.so/page-uuid-0003",
            content="",
            metadata={},
        )
        assert artifact_to_brain_entities(artifact) == []


# ── sync cursor round-trip via connector ─────────────────────────────────────

class TestSyncCursorViaConnector:
    @pytest.mark.asyncio
    async def test_get_sync_cursor_returns_none_initially(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)
        assert await connector.get_sync_cursor() is None

    @pytest.mark.asyncio
    async def test_mark_sync_complete_advances_cursor(self, tmp_path: Path):
        config = _make_config(tmp_path)
        connector = NotionConnector(config)

        assert await connector.get_sync_cursor() is None
        connector.mark_sync_complete()
        cursor = await connector.get_sync_cursor()

        assert cursor is not None
        assert "T" in cursor  # ISO timestamp
