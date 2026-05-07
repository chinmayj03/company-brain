# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/incremental.py — find_dependents()
#
# Key changes from the original:
#   - Queries our Postgres edges table (IMPORTS_FROM edges) instead of SQLite.
#   - Accepts an async psycopg connection or a sync psycopg2 cursor — caller
#     decides which is appropriate for the runtime context.
#   - Hop cap configurable (default 3, matching CRG's default).
#   - Returns Path objects relative to repo_root for consistency with changes.py.
"""ADR-006 §6: Reverse-import BFS expansion.

Given a dirty set of changed files, walk the import graph backwards to find
all files that (transitively) depend on them — up to MAX_HOPS hops.  These
dependents also need to be re-parsed because their call graph may have changed
even if their own source bytes haven't.

Design note: this query runs against the structural edges already in Postgres
(IMPORTS_FROM edges from a prior index run).  On the very first index run the
edges table is empty, so find_dependents() returns an empty set and the
indexer correctly falls back to indexing the full dirty set only.

Usage::

    from companybrain.structural.dependents import find_dependents

    # Synchronous (used in backfill / CLI scripts)
    import psycopg2
    conn = psycopg2.connect(db_url)
    extra = find_dependents(conn, workspace_id, dirty_files, repo_root)

    # The full affected set to re-parse:
    affected = dirty_files | extra
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# How many import hops to expand.  3 matches CRG's default.
# Raising this increases correctness at the cost of re-parsing more files.
DEFAULT_MAX_HOPS: int = 3


def find_dependents(
    conn: Any,               # psycopg2 connection
    workspace_id: str,
    dirty_files: set[Path],  # repo-relative paths from changes.py
    repo_root: str | Path,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> set[Path]:
    """Return the set of files that import (transitively) from *dirty_files*.

    Uses BFS over IMPORTS_FROM edges stored in Postgres.  Only IMPORTS_FROM
    edges are traversed — CALLS edges are not expanded here because method-level
    call graph expansion is handled by the blast-radius CTE at query time.

    Files already in *dirty_files* are excluded from the returned set (they
    will be re-parsed regardless).

    Args:
        conn:         psycopg2 connection (sync).
        workspace_id: UUID string for the workspace.
        dirty_files:  Repo-relative file paths that changed (from changes.py).
        repo_root:    Absolute path to repo root (used to resolve full paths).
        max_hops:     Maximum number of import hops to expand.

    Returns:
        Set of repo-relative Paths for additional files that need re-parsing.
        Does NOT include the original dirty_files themselves.
    """
    if not dirty_files:
        return set()

    repo_root = Path(repo_root).resolve()
    dirty_str = {str(p) for p in dirty_files}

    # BFS state
    frontier: deque[str] = deque(dirty_str)
    visited: set[str] = set(dirty_str)
    extra: set[str] = set()
    hops_remaining = max_hops

    cursor = conn.cursor()

    while frontier and hops_remaining > 0:
        hops_remaining -= 1
        batch = list(frontier)
        frontier.clear()

        # Find all files that have an IMPORTS_FROM edge pointing AT anything
        # in the current frontier batch.
        # The target of an IMPORTS_FROM edge is the file being imported.
        # We want: files whose qualified_name starts with any dirty file prefix.
        #
        # qualified_name format: "path/to/file.py::ClassName.method"
        # file_path column stores: "path/to/file.py"
        #
        # We match on the source node's file_path being in our visited set,
        # then collect the importing node's file_path.
        placeholders = ",".join(["%s"] * len(batch))
        cursor.execute(f"""
            SELECT DISTINCT n_src.file_path
            FROM edges e
            JOIN nodes n_tgt ON n_tgt.id = e.target_id
            JOIN nodes n_src ON n_src.id = e.source_id
            WHERE e.workspace_id = %s::UUID
              AND e.edge_type = 'IMPORTS_FROM'
              AND e.is_pruned = false
              AND n_tgt.file_path IN ({placeholders})
              AND n_src.file_path IS NOT NULL
        """, [workspace_id] + batch)

        rows = cursor.fetchall()
        for (importer_file,) in rows:
            if importer_file and importer_file not in visited:
                visited.add(importer_file)
                extra.add(importer_file)
                frontier.append(importer_file)

    cursor.close()

    # Convert back to Path objects, filter to files that actually exist on disk
    result: set[Path] = set()
    for file_str in extra:
        p = Path(file_str)
        full = repo_root / p
        if full.exists():
            result.add(p)
        else:
            log.debug("Dependent file no longer exists on disk, skipping: %s", file_str)

    if result:
        log.info(
            "find_dependents: %d dirty files → %d additional dependents (max_hops=%d)",
            len(dirty_files), len(result), max_hops,
        )
    else:
        log.debug(
            "find_dependents: no additional dependents found for %d dirty files",
            len(dirty_files),
        )

    return result


def find_dependents_from_qualified_names(
    conn: Any,
    workspace_id: str,
    dirty_qualified_names: set[str],
    repo_root: str | Path,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> set[Path]:
    """Variant that accepts qualified names instead of file paths.

    Useful when the caller knows which specific nodes changed (e.g. after
    a rename or method signature change) rather than which files changed.

    Expands to file_paths of all files that transitively import the given nodes.
    """
    if not dirty_qualified_names:
        return set()

    repo_root = Path(repo_root).resolve()

    cursor = conn.cursor()

    # Resolve qualified names to file_paths first
    placeholders = ",".join(["%s"] * len(dirty_qualified_names))
    cursor.execute(f"""
        SELECT DISTINCT file_path
        FROM nodes
        WHERE workspace_id = %s::UUID
          AND qualified_name IN ({placeholders})
          AND file_path IS NOT NULL
    """, [workspace_id] + list(dirty_qualified_names))

    dirty_files = {Path(row[0]) for row in cursor.fetchall() if row[0]}
    cursor.close()

    if not dirty_files:
        return set()

    return find_dependents(conn, workspace_id, dirty_files, repo_root, max_hops)
