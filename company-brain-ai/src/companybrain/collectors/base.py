"""
Collector protocol — ADR-005: Artifact-Centric Knowledge Pipeline.

A Collector's ONLY contract is:
    emit an AsyncIterator (or list) of Artifacts

Collectors:
  - Do NOT call the LLM
  - Do NOT write to the graph DB
  - Do NOT know about nodes, edges, or contexts
  - Accept a workspace_id and optional since-datetime for incremental collection

This decouples ingestion (many sources, APIs, file systems) from extraction
(one consistent LLM pipeline that consumes Artifact objects regardless of origin).

Current implementations:
    git_collector.py    — source_file, commit, pr artifacts from git repos
    code_tracer.py      — source_file artifacts via static call tracing (wraps CodeTracer)

Planned implementations (not yet built):
    zendesk_collector   — ticket artifacts from Zendesk
    slack_collector     — slack_thread artifacts from Slack
    confluence_collector— doc_page artifacts from Confluence
    annotation_collector— annotation artifacts from the VS Code extension / web UI
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol, runtime_checkable
from uuid import UUID

from companybrain.models.entities import Artifact


@runtime_checkable
class Collector(Protocol):
    """
    Protocol that every collector must satisfy.

    Implementations are plain classes — no base class inheritance required.
    The `kind` class attribute declares which artifact kind(s) this collector
    produces (informational; a collector may emit multiple kinds).
    """

    #: Primary artifact kind produced by this collector.
    kind: str

    async def collect(
        self,
        workspace_id: UUID,
        since: datetime | None = None,
    ) -> list[Artifact]:
        """
        Collect artifacts for the given workspace.

        Args:
            workspace_id:  Tenant scope (used for logging / metadata only;
                           ArtifactWriterService enforces workspace isolation).
            since:         If provided, only return artifacts that have changed
                           since this datetime (incremental collection).
                           If None, collect everything (full refresh).

        Returns:
            A list of Artifact objects.  May be empty if nothing changed.
            Order is not significant — the pipeline will deduplicate by
            (workspace_id, kind, external_id) at write time.
        """
        ...
