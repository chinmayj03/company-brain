"""
ADR-0092 — CodeConnector: wraps the existing code extraction pipeline as a BaseConnector.

This serves as the reference implementation that proves the BaseConnector abstraction
works end-to-end with a real knowledge source.

Implementation status: STUB (Wave B1.2)
  - validate_credentials: checks that repo_path exists and is a directory.
  - list_artifacts: uses FileWalker to enumerate source files; yields each file
    as a SourceArtifact with raw content.
  - fetch_artifact: reads a single file by URN.
  - get_sync_cursor: returns {last_sync_ts, repo_path} for incremental support.

Full integration (calling LLM extraction passes, writing BrainEntities with semantic
content) is deferred to Wave B2. The stub is intentional — it proves the interface
compiles and can be wired into ConnectorIngestionPipeline today.

Config shape (sync_config):
    {
        "repo_path": "/abs/path/to/repo",
        "branch": "main",                  # informational
        "include_globs": ["**/*.py"],       # optional future filter
    }
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import structlog

from companybrain.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    SourceArtifact,
    TTL_PERMANENT,
)
from companybrain.connectors.registry import ConnectorRegistry

log = structlog.get_logger(__name__)

# Max file size to include as content in a SourceArtifact.
# Files larger than this are still yielded but content is truncated.
_MAX_CONTENT_BYTES = 500_000  # 500 KB


@ConnectorRegistry.register("code")
class CodeConnector(BaseConnector):
    """
    Wraps the local filesystem code extraction pipeline.

    This connector reads source files from a local git repository and yields them
    as SourceArtifacts. It does NOT invoke LLM extraction — that remains in the
    existing orchestrator pipeline. The connector's job is to surface the raw
    file content so the ingestion pipeline can embed and store it.

    B2 integration note: In Wave B2, CodeConnector.list_artifacts() will drive
    the full multi-pass LLM extraction and return semantically-enriched artifacts
    (entities, relationships, business context). For now it returns raw file
    content as ``source_artifact`` entities.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._repo_path = Path(
            config.sync_config.get("repo_path", "")
        )

    async def validate_credentials(self) -> bool:
        """
        For code sources, "credentials" means filesystem access.
        Verifies that repo_path is an existing, readable directory.
        """
        if not self._repo_path:
            raise ValueError(
                "CodeConnector: sync_config.repo_path is required but not set."
            )
        if not self._repo_path.exists():
            raise FileNotFoundError(
                f"CodeConnector: repo_path does not exist: {self._repo_path}"
            )
        if not self._repo_path.is_dir():
            raise NotADirectoryError(
                f"CodeConnector: repo_path is not a directory: {self._repo_path}"
            )
        log.debug("code_connector_validated", repo_path=str(self._repo_path))
        return True

    async def list_artifacts(
        self, since: Optional[datetime] = None
    ) -> AsyncIterator[SourceArtifact]:
        """
        Walk the repository and yield one SourceArtifact per extractable source file.

        Uses companybrain.pipeline.file_walker.FileWalker for consistent file
        enumeration (respects .gitignore, size limits, skip dirs).

        If ``since`` is provided, only files with mtime > since are yielded.
        """
        from companybrain.pipeline.file_walker import FileWalker  # lazy import

        walker = FileWalker(repo_root=self._repo_path)
        for file_info in walker.walk():
            if not file_info.should_extract:
                continue

            abs_path = file_info.path
            rel_path = abs_path.relative_to(self._repo_path)

            # Incremental filter: skip files not modified since last sync
            if since is not None:
                try:
                    mtime = datetime.fromtimestamp(
                        abs_path.stat().st_mtime, tz=timezone.utc
                    ).replace(tzinfo=None)  # normalize to naive UTC
                    if mtime <= since:
                        continue
                except OSError:
                    pass  # if we can't stat it, include it

            # Read content (with size cap)
            try:
                raw = abs_path.read_bytes()
                if len(raw) > _MAX_CONTENT_BYTES:
                    log.debug(
                        "code_connector_file_truncated",
                        path=str(rel_path),
                        size=len(raw),
                    )
                    raw = raw[:_MAX_CONTENT_BYTES]
                try:
                    content = raw.decode("utf-8", errors="replace")
                except Exception:
                    content = ""
            except OSError as exc:
                log.warning("code_connector_read_error", path=str(rel_path), error=str(exc))
                continue

            # Build artifact
            try:
                mtime_ts = datetime.fromtimestamp(
                    abs_path.stat().st_mtime, tz=timezone.utc
                ).replace(tzinfo=None)
            except OSError:
                mtime_ts = datetime.utcnow()

            urn = self._make_urn(artifact_kind="file", artifact_id=str(rel_path))
            suffix = abs_path.suffix.lstrip(".")

            yield SourceArtifact(
                urn=urn,
                title=str(rel_path),
                content=content,
                metadata={
                    "repo_path": str(self._repo_path),
                    "rel_path": str(rel_path),
                    "language": suffix or "unknown",
                    "size_bytes": len(raw),
                },
                last_modified=mtime_ts,
                source_type="code",
                # Code artifacts are treated as permanent — they capture the
                # structural intent of the system at the time they were written.
                ttl_class=TTL_PERMANENT,
            )

    async def fetch_artifact(self, artifact_urn: str) -> SourceArtifact:
        """
        Re-fetch a single file by URN.

        URN format: source://code/file/<rel_path>@<workspace_id>
        """
        # Parse rel_path from URN: source://code/file/<rel_path>@<workspace_id>
        try:
            # strip prefix "source://code/file/" and suffix "@<workspace_id>"
            body = artifact_urn.split("source://code/file/", 1)[1]
            rel_path_str = body.split("@")[0]
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"CodeConnector: cannot parse artifact URN: {artifact_urn!r}"
            ) from exc

        abs_path = self._repo_path / rel_path_str
        if not abs_path.exists():
            raise FileNotFoundError(
                f"CodeConnector: file not found: {abs_path} (from URN {artifact_urn!r})"
            )

        raw = abs_path.read_bytes()
        if len(raw) > _MAX_CONTENT_BYTES:
            raw = raw[:_MAX_CONTENT_BYTES]
        content = raw.decode("utf-8", errors="replace")

        try:
            mtime_ts = datetime.fromtimestamp(
                abs_path.stat().st_mtime, tz=timezone.utc
            ).replace(tzinfo=None)
        except OSError:
            mtime_ts = datetime.utcnow()

        suffix = abs_path.suffix.lstrip(".")
        return SourceArtifact(
            urn=artifact_urn,
            title=rel_path_str,
            content=content,
            metadata={
                "repo_path": str(self._repo_path),
                "rel_path": rel_path_str,
                "language": suffix or "unknown",
                "size_bytes": len(raw),
            },
            last_modified=mtime_ts,
            source_type="code",
            ttl_class=TTL_PERMANENT,
        )

    async def get_sync_cursor(self) -> dict:
        """
        Returns a cursor with the current UTC timestamp for incremental sync.

        On the next incremental run, list_artifacts() will skip files with
        mtime ≤ last_sync_ts.
        """
        return {
            "last_sync_ts": datetime.utcnow().isoformat(),
            "repo_path": str(self._repo_path),
        }
