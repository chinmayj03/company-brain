"""
Fanout — primary store + N mirrors.

Writes go to `primary` first (JSON SOT). On success, mirrors are written
in parallel; mirror failures log but do not roll back the primary.
"""
from __future__ import annotations
import asyncio
from typing import Optional

import structlog

from companybrain.store.base import BrainStore, BrainEntity

log = structlog.get_logger(__name__)


class FanoutBrainStore(BrainStore):
    def __init__(self, primary: BrainStore, mirrors: list[BrainStore]):
        self.primary = primary
        self.mirrors = mirrors

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        await self.primary.write(entity, run_id=run_id, workspace_id=workspace_id)
        await asyncio.gather(*[
            self._safe_write(m, entity, run_id, workspace_id) for m in self.mirrors
        ])

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        return await self.primary.read(entity_id)

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        return await self.primary.is_fresh(entity_id, version_hash)

    async def list_ids(self):
        async for x in self.primary.list_ids():
            yield x

    async def commit_run(self, run_id: str) -> None:
        await self.primary.commit_run(run_id)
        await asyncio.gather(*[m.commit_run(run_id) for m in self.mirrors],
                              return_exceptions=True)

    async def _safe_write(
        self, mirror: BrainStore, entity: BrainEntity, run_id: str, workspace_id: str
    ) -> None:
        try:
            await mirror.write(entity, run_id=run_id, workspace_id=workspace_id)
        except Exception as exc:
            log.warning(
                "Mirror store write failed (non-fatal)",
                store=type(mirror).__name__,
                entity_id=entity.id,
                error=str(exc),
            )
