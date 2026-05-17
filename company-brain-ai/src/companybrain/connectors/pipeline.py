"""
B1.2 Connector ingestion pipeline — orchestrates list → fetch → emit.

Usage:
    pipeline = ConnectorIngestionPipeline(connector)
    async for entity in pipeline.run():
        await store.write(entity, run_id=run_id, workspace_id=workspace_id)
    pipeline.connector.mark_sync_complete()
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from companybrain.connectors.base import BaseConnector, SourceArtifact

logger = logging.getLogger(__name__)


class ConnectorIngestionPipeline:
    """
    Thin orchestration layer that drives a connector through its full lifecycle.

    The pipeline:
      1. Calls `list_artifacts()` to get stubs.
      2. Calls `fetch_artifact()` for each stub to hydrate content.
      3. Yields each hydrated SourceArtifact to the caller for store ingestion.

    Error handling: fetch failures are logged and skipped; list failures propagate.
    """

    def __init__(self, connector: BaseConnector) -> None:
        self.connector = connector

    async def run(self) -> AsyncIterator[SourceArtifact]:
        """Yield fully-hydrated SourceArtifacts, one per accessible item."""
        fetched = 0
        skipped = 0
        async for stub in self.connector.list_artifacts():
            try:
                artifact = await self.connector.fetch_artifact(stub)
                fetched += 1
                yield artifact
            except Exception as exc:
                skipped += 1
                logger.warning(
                    "ConnectorIngestionPipeline: skipping artifact id=%s error=%s",
                    stub.id,
                    exc,
                )
        logger.info(
            "ConnectorIngestionPipeline: done source_type=%s fetched=%d skipped=%d",
            self.connector.SOURCE_TYPE,
            fetched,
            skipped,
        )
