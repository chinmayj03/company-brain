"""
StalenessDetector — identifies Neo4j nodes that have become stale.

A node is considered stale when the file it was extracted from has changed
(source_checksum differs from the current file hash) and the node has not
already been invalidated (valid_to_commit IS NULL).

Staleness scoring:
    score = clamp(
        (commits_since_extraction / 10.0) * (1.0 - node_confidence),
        0.0, 1.0
    )

Nodes scoring above *threshold* (default 0.7) are returned by
get_stale_nodes_for_scope() as candidates for re-extraction.
"""
from __future__ import annotations

from typing import Any, Optional

import structlog

from companybrain.graph.neo4j_writer import Neo4jWriter, build_llm_urn

log = structlog.get_logger(__name__)


class StalenessDetector:
    """
    Detects stale CBNode records in Neo4j for a given workspace scope.

    Usage::

        detector = StalenessDetector(workspace_id="acme/web", neo4j_writer=writer)
        stale_urns = await detector.detect_stale_nodes("src/billing/handler.py",
                                                        current_hash="abc123")
        if await detector.refresh_needed("src/billing/handler.py", current_hash):
            ...  trigger re-extraction
    """

    def __init__(self, workspace_id: str, neo4j_writer: Neo4jWriter) -> None:
        """
        Args:
            workspace_id:  Workspace identifier (= URN scope).
            neo4j_writer:  Connected Neo4jWriter instance (shared with GraphBuilder).
        """
        self.workspace_id = workspace_id
        self._writer      = neo4j_writer

    # ── Public API ────────────────────────────────────────────────────────────

    async def detect_stale_nodes(
        self,
        file_path: str,
        current_hash: str,
    ) -> list[str]:
        """
        Return the URNs of all active nodes for *file_path* whose
        source_checksum differs from *current_hash*.

        A node is active when valid_to_commit IS NULL.

        Args:
            file_path:    Relative path to the source file.
            current_hash: MD5 hex digest of the current file content.

        Returns:
            List of stale node URN strings (may be empty).
        """
        urn_prefix = build_llm_urn(self.workspace_id, file_path)

        cypher = """
MATCH (n:CBNode)
WHERE n.id STARTS WITH $prefix
  AND n.source_checksum IS NOT NULL
  AND n.source_checksum <> $current_hash
  AND n.valid_to_commit IS NULL
RETURN n.id AS urn
"""
        rows = await self._query(
            cypher,
            prefix=urn_prefix,
            current_hash=current_hash,
            context=f"detect_stale_nodes:{file_path}",
        )
        urns = [r["urn"] for r in rows if r.get("urn")]

        if urns:
            log.info(
                "Stale nodes detected",
                file_path=file_path,
                count=len(urns),
                workspace=self.workspace_id,
            )

        return urns

    async def compute_staleness_score(self, node_urn: str) -> float:
        """
        Compute a staleness score in [0.0, 1.0] for a single node.

        Formula::

            score = clamp(
                (commits_since_extraction / 10.0) * (1.0 - node_confidence),
                0.0, 1.0
            )

        *commits_since_extraction* is derived from the node's last_modified_commit
        vs the most recently seen commit in that scope (approximated as the count
        of distinct commits stored across all nodes in the workspace that post-date
        the node's last_modified_commit).

        If the node does not exist or has no confidence/commit metadata, returns 0.0.

        Args:
            node_urn: Full URN of the node.

        Returns:
            Staleness score between 0.0 and 1.0 (inclusive).
        """
        cypher = """
MATCH (n:CBNode { id: $urn })
RETURN n.confidence           AS confidence,
       n.last_modified_commit AS last_modified_commit,
       n.valid_to_commit      AS valid_to_commit
"""
        rows = await self._query(cypher, urn=node_urn, context=f"compute_staleness:{node_urn}")
        if not rows:
            return 0.0

        record = rows[0]
        confidence: float = float(record.get("confidence") or 0.5)

        # Count how many distinct commits in this scope are "newer".
        # We use a proxy: total active nodes in scope that share a last_modified_commit
        # that is lexicographically greater than this node's (commit SHAs are not
        # directly orderable, but the pipeline stores them newest-first by ingestion
        # order, so we approximate with a count of distinct values).
        last_commit: str = record.get("last_modified_commit") or ""

        if last_commit:
            count_cypher = """
MATCH (n:CBNode)
WHERE n.scope = $scope
  AND n.valid_to_commit IS NULL
  AND n.last_modified_commit IS NOT NULL
  AND n.last_modified_commit <> $commit
RETURN count(DISTINCT n.last_modified_commit) AS c
"""
            count_rows = await self._query(
                count_cypher,
                scope=self.workspace_id,
                commit=last_commit,
                context=f"compute_staleness_count:{node_urn}",
            )
            commits_since: int = int(count_rows[0]["c"]) if count_rows else 0
        else:
            commits_since = 0

        raw_score = (commits_since / 10.0) * (1.0 - confidence)
        return max(0.0, min(1.0, raw_score))

    async def get_stale_nodes_for_scope(
        self,
        scope: Optional[str] = None,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Return node records for all active nodes in *scope* whose staleness
        score exceeds *threshold*.

        This is an O(nodes) operation — use sparingly in background jobs, not
        on hot request paths.

        Args:
            scope:     Workspace scope to query.  Defaults to self.workspace_id.
            threshold: Staleness score threshold (default 0.7).  Nodes scoring
                       strictly above this value are returned.

        Returns:
            List of dicts with keys: urn, confidence, last_modified_commit,
            file, staleness_score.
        """
        scope = scope or self.workspace_id

        cypher = """
MATCH (n:CBNode)
WHERE n.scope = $scope
  AND n.valid_to_commit IS NULL
  AND n.source_checksum IS NOT NULL
RETURN n.id                   AS urn,
       n.confidence           AS confidence,
       n.last_modified_commit AS last_modified_commit,
       n.file                 AS file,
       n.source_checksum      AS source_checksum
"""
        rows = await self._query(cypher, scope=scope, context="get_stale_nodes_for_scope")
        if not rows:
            return []

        # Compute a bulk staleness score using the maximum known commit index.
        # Approach: collect all distinct last_modified_commit values and rank
        # them by first-seen order (position in the list acts as a proxy for age).
        distinct_commits: list[str] = []
        seen: set[str] = set()
        for row in rows:
            c = row.get("last_modified_commit") or ""
            if c and c not in seen:
                seen.add(c)
                distinct_commits.append(c)

        commit_rank: dict[str, int] = {c: i for i, c in enumerate(distinct_commits)}
        total_commits = max(len(distinct_commits), 1)

        stale: list[dict[str, Any]] = []
        for row in rows:
            confidence  = float(row.get("confidence") or 0.5)
            last_commit = row.get("last_modified_commit") or ""
            rank        = commit_rank.get(last_commit, 0)
            # Commits earlier in the list are "older" → higher staleness
            commits_since = total_commits - rank - 1
            raw_score     = (commits_since / 10.0) * (1.0 - confidence)
            score         = max(0.0, min(1.0, raw_score))

            if score > threshold:
                stale.append({
                    "urn":                 row.get("urn"),
                    "confidence":          confidence,
                    "last_modified_commit": last_commit,
                    "file":                row.get("file"),
                    "source_checksum":     row.get("source_checksum"),
                    "staleness_score":     round(score, 4),
                })

        log.info(
            "Staleness scan complete",
            scope=scope,
            total_nodes=len(rows),
            stale_above_threshold=len(stale),
            threshold=threshold,
        )
        return stale

    async def refresh_needed(self, file_path: str, current_hash: str) -> bool:
        """
        Return True if any active nodes for *file_path* have a stale checksum.

        This is a lightweight EXISTS check — cheaper than detect_stale_nodes()
        when the caller only needs a boolean.

        Args:
            file_path:    Relative path to the source file.
            current_hash: MD5 hex digest of the current file content.

        Returns:
            True if at least one stale node exists; False otherwise.
        """
        urn_prefix = build_llm_urn(self.workspace_id, file_path)

        cypher = """
MATCH (n:CBNode)
WHERE n.id STARTS WITH $prefix
  AND n.source_checksum IS NOT NULL
  AND n.source_checksum <> $current_hash
  AND n.valid_to_commit IS NULL
RETURN count(n) AS c
LIMIT 1
"""
        rows = await self._query(
            cypher,
            prefix=urn_prefix,
            current_hash=current_hash,
            context=f"refresh_needed:{file_path}",
        )
        return bool(rows and int(rows[0].get("c", 0)) > 0)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _query(
        self,
        cypher: str,
        context: str = "",
        **params: Any,
    ) -> list[dict[str, Any]]:
        """
        Execute a read Cypher query via the shared Neo4jWriter session.

        Errors are logged and swallowed — returns an empty list on failure.
        """
        if self._writer._driver is None:
            log.warning(
                "Neo4j not connected — skipping staleness query",
                context=context,
                workspace=self.workspace_id,
            )
            return []

        try:
            async with self._writer._session() as session:
                result = await session.run(cypher, **params)
                records = await result.data()
                return records or []
        except Exception as exc:
            log.error(
                "StalenessDetector query failed",
                error=str(exc),
                error_type=type(exc).__name__,
                context=context,
                workspace=self.workspace_id,
            )
            return []
