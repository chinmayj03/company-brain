"""
ADR-0092 — ConnectorIngestionPipeline: orchestrates source → brain storage.

Pipeline stages for each artifact:
  1. Load connector config from source registry (or in-memory config store)
  2. Instantiate connector via ConnectorRegistry
  3. Stream artifacts via connector.list_artifacts(since)
  4. For each artifact:
     a. PII scan  — if companybrain.privacy is available; else skip with warning
     b. Store SourceArtifact in brain store (as BrainEntity)
     c. Stub: domain entity resolution deferred to B2
  5. Update sync cursor in config store
  6. Return SyncResult

Configuration injection:
  Callers pass either a ConnectorConfig directly (for testing / CLI use) or a
  source_id which the pipeline resolves via ``config_loader``. The default
  config_loader is a no-op that raises — production deployments wire in a loader
  that reads from the workspace_sources DB table.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

import structlog

from companybrain.connectors.base import BaseConnector, ConnectorConfig, SourceArtifact
from companybrain.connectors.registry import ConnectorRegistry
from companybrain.store.base import BrainEntity, BrainStore

log = structlog.get_logger(__name__)

# Type aliases
ConfigLoader = Callable[[str], Awaitable[ConnectorConfig]]
CursorStore  = Callable[[str, dict], Awaitable[None]]
CursorLoader = Callable[[str], Awaitable[Optional[dict]]]


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    """Summary of a completed sync run."""
    source_id: str
    source_type: str
    artifacts_seen: int = 0
    artifacts_stored: int = 0
    artifacts_skipped_pii: int = 0
    artifacts_failed: int = 0
    duration_seconds: float = 0.0
    cursor: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    full_sync: bool = False

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ── Default no-op helpers (replaced in production) ────────────────────────────

async def _default_config_loader(source_id: str) -> ConnectorConfig:
    raise NotImplementedError(
        f"ConnectorIngestionPipeline has no config_loader configured. "
        f"Cannot resolve source_id={source_id!r}. "
        "Pass config_loader= when constructing the pipeline."
    )


async def _default_cursor_loader(source_id: str) -> Optional[dict]:
    return None  # No cursor → full sync


async def _default_cursor_store(source_id: str, cursor: dict) -> None:
    pass  # Silently discard; tests replace this


# ── PII scan integration (optional) ──────────────────────────────────────────

def _try_pii_scan(artifact: SourceArtifact) -> bool:
    """
    Returns True if the artifact is clean (no PII), False if PII was detected.

    Attempts to import companybrain.privacy.detector; if unavailable (e.g. early
    in the V2 rollout), logs a debug warning and returns True (pass-through).
    """
    try:
        from companybrain.privacy.detector import scan_text  # type: ignore[import]
        result = scan_text(artifact.content)
        return not result.has_pii
    except ImportError:
        log.debug(
            "pii_scan_skipped",
            reason="companybrain.privacy not available",
            artifact_urn=artifact.urn,
        )
        return True
    except Exception as exc:
        log.warning("pii_scan_error", artifact_urn=artifact.urn, error=str(exc))
        return True  # Fail open — don't block ingestion on scanner error


# ── Artifact → BrainEntity conversion ─────────────────────────────────────────

def _artifact_to_brain_entity(artifact: SourceArtifact, workspace_id: str) -> BrainEntity:
    """
    Convert a SourceArtifact to a BrainEntity for storage.

    In B1.2 this is a direct mapping — the artifact URN becomes the entity id.
    Domain entity resolution (linking to urn:cb:… canonical entities) is deferred
    to B2 per the ADR-0091 progressive resolution model.
    """
    metadata = {
        **artifact.metadata,
        "source_urn": artifact.urn,
        "source_type": artifact.source_type,
        "ttl_class": artifact.ttl_class,
        "last_modified": artifact.last_modified.isoformat(),
        "domain_resolved": False,  # B2 will flip this to True
    }
    return BrainEntity(
        id=artifact.urn,
        entity_type="source_artifact",
        repo=artifact.source_type,
        file=artifact.urn,
        qualified_name=artifact.urn,
        t1_summary=artifact.title,
        t0_token=artifact.title[:100],
        t1_token=artifact.content[:500],
        metadata=metadata,
        last_updated=artifact.last_modified.isoformat() + "Z",
        last_updated_by=f"connector/{artifact.source_type}",
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ConnectorIngestionPipeline:
    """
    Orchestrates the full sync cycle for one registered source.

    Parameters
    ----------
    store:
        BrainStore to write SourceArtifacts into. Required.
    config_loader:
        Async callable ``(source_id: str) -> ConnectorConfig``. When omitted,
        callers must pass ``config=`` directly to ``run_sync()``.
    cursor_loader:
        Async callable ``(source_id: str) -> Optional[dict]``. Returns the last
        persisted sync cursor, or None for a full sync.
    cursor_store:
        Async callable ``(source_id: str, cursor: dict) -> None``. Called after
        a successful sync to persist the new cursor.
    run_id:
        Optional run identifier for the brain store. Defaults to a timestamp.
    """

    def __init__(
        self,
        store: BrainStore,
        config_loader: ConfigLoader = _default_config_loader,
        cursor_loader: CursorLoader = _default_cursor_loader,
        cursor_store: CursorStore = _default_cursor_store,
        run_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._config_loader = config_loader
        self._cursor_loader = cursor_loader
        self._cursor_store = cursor_store
        self._run_id = run_id

    async def run_sync(
        self,
        source_id: str,
        full: bool = False,
        config: Optional[ConnectorConfig] = None,
    ) -> SyncResult:
        """
        Run a sync for the given source.

        Parameters
        ----------
        source_id:
            The source's UUID from the source registry.
        full:
            If True, ignore any stored cursor and run a full sync.
            If False (default), load cursor and do an incremental sync.
        config:
            Override the config loader. Useful in tests and CLI usage where
            we have a config object already.
        """
        t_start = time.monotonic()
        run_id = self._run_id or f"sync-{source_id}-{int(t_start)}"

        # 1. Resolve config
        if config is None:
            config = await self._config_loader(source_id)

        result = SyncResult(
            source_id=source_id,
            source_type=config.source_type,
            full_sync=full,
        )

        log.info(
            "connector_sync_start",
            source_id=source_id,
            source_type=config.source_type,
            full=full,
        )

        # 2. Resolve cursor (for incremental sync)
        since: Optional[datetime] = None
        if not full:
            cursor = await self._cursor_loader(source_id)
            if cursor and "last_sync_ts" in cursor:
                try:
                    since = datetime.fromisoformat(cursor["last_sync_ts"])
                except (ValueError, TypeError):
                    log.warning("cursor_parse_error", cursor=cursor)

        # 3. Instantiate connector
        try:
            connector_cls = ConnectorRegistry.get(config.source_type)
        except KeyError as exc:
            result.errors.append(str(exc))
            result.duration_seconds = time.monotonic() - t_start
            log.error("connector_not_found", source_type=config.source_type)
            return result

        connector: BaseConnector = connector_cls(config)

        # 4. Validate credentials
        try:
            valid = await connector.validate_credentials()
            if not valid:
                result.errors.append(
                    f"Credentials validation returned False for {config.source_type}"
                )
                result.duration_seconds = time.monotonic() - t_start
                return result
        except Exception as exc:
            result.errors.append(f"Credentials validation failed: {exc}")
            result.duration_seconds = time.monotonic() - t_start
            log.error("credentials_validation_error", error=str(exc))
            return result

        # 5. Stream and process artifacts
        try:
            async for artifact in connector.list_artifacts(since=since):
                result.artifacts_seen += 1

                # 5a. PII scan
                if not _try_pii_scan(artifact):
                    log.info(
                        "artifact_skipped_pii",
                        urn=artifact.urn,
                    )
                    result.artifacts_skipped_pii += 1
                    continue

                # 5b. Store in brain
                try:
                    entity = _artifact_to_brain_entity(artifact, config.workspace_id)
                    await self._store.write(
                        entity, run_id=run_id, workspace_id=config.workspace_id
                    )
                    result.artifacts_stored += 1
                    log.debug("artifact_stored", urn=artifact.urn)
                except Exception as exc:
                    result.artifacts_failed += 1
                    result.errors.append(f"Failed to store {artifact.urn}: {exc}")
                    log.warning(
                        "artifact_store_error",
                        urn=artifact.urn,
                        error=str(exc),
                    )

        except Exception as exc:
            result.errors.append(f"Artifact stream error: {exc}")
            log.error("artifact_stream_error", error=str(exc))

        # 6. Commit run
        try:
            await self._store.commit_run(run_id)
        except Exception as exc:
            result.errors.append(f"commit_run failed: {exc}")
            log.warning("commit_run_error", error=str(exc))

        # 7. Persist cursor
        try:
            new_cursor = await connector.get_sync_cursor()
            await self._cursor_store(source_id, new_cursor)
            result.cursor = new_cursor
        except Exception as exc:
            log.warning("cursor_persist_error", error=str(exc))

        result.duration_seconds = time.monotonic() - t_start
        log.info(
            "connector_sync_complete",
            source_id=source_id,
            artifacts_seen=result.artifacts_seen,
            artifacts_stored=result.artifacts_stored,
            duration_seconds=round(result.duration_seconds, 2),
            success=result.success,
        )
        return result
