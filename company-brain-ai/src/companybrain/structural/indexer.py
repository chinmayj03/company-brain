# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/incremental.py — incremental_update()
#
# Key changes from the original:
#   - Targets Postgres (nodes + edges tables) instead of SQLite.
#   - Uses ProcessPoolExecutor for parallel parsing (same as CRG).
#   - Hash-diff check queries the DB (file_hash column on nodes table).
#   - Integrated with our risk scorer (companybrain/structural/risk.py).
#   - Accepts workspace_id for multi-tenant isolation.
#   - run_full_index() added for first-time indexing and backfill.
"""ADR-006 §7: Hash-diff incremental structural indexer.

The indexer is the main entry point for keeping the structural layer current.
It coordinates:

  1. changes.py   — which files changed (git-diff or full scan)
  2. dependents.py — which additional files need re-parsing (reverse BFS)
  3. parser.py    — parse the affected set (parallel)
  4. risk.py      — compute risk scores for parsed nodes
  5. Postgres     — upsert nodes, edges, risk data

Key performance property: files whose SHA-256 hash has not changed since the
last index run are skipped entirely.  On a 500-file repo after a one-line
change, this means parsing 1–5 files instead of 500.  This is what makes
incremental indexing sub-5-seconds.

Usage::

    from companybrain.structural.indexer import StructuralIndexer

    indexer = StructuralIndexer(db_url="postgresql://...", workspace_id="uuid")

    # Incremental: detect what changed since last HEAD and re-index it
    result = indexer.run_incremental("/path/to/repo")

    # Full: re-index everything (first run or forced refresh)
    result = indexer.run_full_index("/path/to/repo")

    print(result.files_parsed, result.files_skipped, result.duration_ms)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.structural.changes import (
    full_scan,
    get_changed_files,
    get_changed_files_since,
    current_head_sha,
)
from companybrain.structural.dependents import find_dependents
from companybrain.structural.parser import parse_file, NodeInfo, EdgeInfo
from companybrain.structural.risk import compute_risk_score

log = structlog.get_logger(__name__)

# Max parallel parse workers. Matches CRG's default.
# CPU-bound work — don't exceed cpu_count.
_DEFAULT_WORKERS: int = 4

# Batch size for Postgres upserts (nodes and edges).
_UPSERT_BATCH: int = 500


# ── Result model ──────────────────────────────────────────────────────────────

@dataclass
class IndexResult:
    """Summary of one indexer run."""
    workspace_id: str
    repo_root: str
    mode: str                       # 'incremental' | 'full'
    files_parsed: int = 0
    files_skipped: int = 0          # hash unchanged — skipped
    files_failed: int = 0           # parse error
    nodes_upserted: int = 0
    edges_upserted: int = 0
    duration_ms: float = 0.0
    last_sha: Optional[str] = None  # HEAD SHA at end of run
    errors: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return self.files_parsed + self.files_skipped + self.files_failed


# ── Indexer ───────────────────────────────────────────────────────────────────

class StructuralIndexer:
    """
    Hash-diff incremental indexer for the structural layer.

    One instance per pipeline run. Not thread-safe — create a new instance
    per concurrent workspace if needed.
    """

    def __init__(
        self,
        db_url: str,
        workspace_id: str,
        max_workers: int = _DEFAULT_WORKERS,
        max_hops: int = 3,
    ):
        self._db_url = db_url
        self._workspace_id = workspace_id
        self._max_workers = max_workers
        self._max_hops = max_hops

    # ── Public API ────────────────────────────────────────────────────────────

    def run_incremental(
        self,
        repo_root: str | Path,
        since_sha: Optional[str] = None,
    ) -> IndexResult:
        """Re-index only files that changed since the last run.

        Args:
            repo_root:  Absolute path to the git repository root.
            since_sha:  If provided, diff from this SHA to HEAD.
                        If None, uses HEAD~1..HEAD (last commit only).

        Returns:
            IndexResult with counts and timing.
        """
        repo_root = Path(repo_root).resolve()
        t0 = time.monotonic()

        result = IndexResult(
            workspace_id=self._workspace_id,
            repo_root=str(repo_root),
            mode="incremental",
        )

        # Step 1 — Determine dirty file set
        if since_sha:
            dirty = get_changed_files_since(repo_root, since_sha)
        else:
            dirty = get_changed_files(repo_root)

        if not dirty:
            log.info("No changed files detected — index is current", repo=str(repo_root))
            result.duration_ms = (time.monotonic() - t0) * 1000
            result.last_sha = current_head_sha(repo_root)
            return result

        # Step 2 — Expand to dependents via reverse-import BFS
        conn = self._connect()
        try:
            extra = find_dependents(
                conn, self._workspace_id, dirty, repo_root, self._max_hops
            )
        finally:
            conn.close()

        affected = dirty | extra
        log.info(
            "Incremental index: %d dirty + %d dependents = %d files to process",
            len(dirty), len(extra), len(affected),
        )

        # Step 3 — Hash-diff filter + parse + upsert
        result = self._index_files(affected, repo_root, result)
        result.last_sha = current_head_sha(repo_root)
        result.duration_ms = (time.monotonic() - t0) * 1000

        log.info(
            "Incremental index complete",
            parsed=result.files_parsed,
            skipped=result.files_skipped,
            failed=result.files_failed,
            nodes=result.nodes_upserted,
            edges=result.edges_upserted,
            duration_ms=f"{result.duration_ms:.0f}",
        )
        return result

    def run_full_index(self, repo_root: str | Path) -> IndexResult:
        """Re-index all source files in the repository.

        Used for:
          - First-time workspace setup
          - Forced refresh after schema changes
          - Recovery after a corrupted index

        Respects hash-diff: files whose hash matches the DB value are still
        skipped unless force=True (not yet implemented — add if needed).
        """
        repo_root = Path(repo_root).resolve()
        t0 = time.monotonic()

        result = IndexResult(
            workspace_id=self._workspace_id,
            repo_root=str(repo_root),
            mode="full",
        )

        all_files = full_scan(repo_root)
        log.info("Full index: %d source files found", len(all_files))

        result = self._index_files(all_files, repo_root, result)
        result.last_sha = current_head_sha(repo_root)
        result.duration_ms = (time.monotonic() - t0) * 1000

        log.info(
            "Full index complete",
            parsed=result.files_parsed,
            skipped=result.files_skipped,
            failed=result.files_failed,
            nodes=result.nodes_upserted,
            edges=result.edges_upserted,
            duration_ms=f"{result.duration_ms:.0f}",
        )
        return result

    # ── Core index loop ───────────────────────────────────────────────────────

    def _index_files(
        self,
        rel_files: set[Path],
        repo_root: Path,
        result: IndexResult,
    ) -> IndexResult:
        """Parse affected files (parallel), hash-diff skip unchanged, upsert."""

        conn = self._connect()
        cursor = conn.cursor()
        _set_workspace(cursor, self._workspace_id)

        # Load existing file hashes from DB in one query
        known_hashes = self._load_file_hashes(cursor, rel_files)

        # Separate files that need parsing from those that are hash-unchanged
        to_parse: list[Path] = []
        for rel_path in rel_files:
            abs_path = repo_root / rel_path
            if not abs_path.exists():
                continue
            current_hash = _sha256(abs_path)
            db_hash = known_hashes.get(str(rel_path))
            if db_hash == current_hash:
                result.files_skipped += 1
                log.debug("Hash unchanged, skipping: %s", rel_path)
            else:
                to_parse.append(rel_path)

        log.info(
            "Hash-diff: %d to parse, %d skipped (hash unchanged)",
            len(to_parse), result.files_skipped,
        )

        if not to_parse:
            cursor.close()
            conn.close()
            return result

        # Parse in parallel using ProcessPoolExecutor
        parse_args = [(str(repo_root / p), str(repo_root)) for p in to_parse]
        parse_results: list[tuple[list[NodeInfo], list[EdgeInfo]]] = []

        with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(_parse_worker, abs_path, repo_root_str): abs_path
                for abs_path, repo_root_str in parse_args
            }
            for future in as_completed(futures):
                abs_path = futures[future]
                try:
                    nodes, edges = future.result()
                    parse_results.append((nodes, edges))
                    result.files_parsed += 1
                except Exception as e:
                    log.warning("Parse failed for %s: %s", abs_path, e)
                    result.files_failed += 1
                    result.errors.append(f"{abs_path}: {e}")

        # Upsert all parsed nodes + edges in batches
        all_nodes = [n for nodes, _ in parse_results for n in nodes]
        all_edges = [e for _, edges in parse_results for e in edges]

        result.nodes_upserted = self._upsert_nodes(cursor, all_nodes)
        result.edges_upserted = self._upsert_edges(cursor, all_edges)

        conn.commit()
        cursor.close()
        conn.close()

        return result

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _connect(self):
        try:
            import psycopg2
            return psycopg2.connect(self._db_url)
        except ImportError:
            raise RuntimeError(
                "psycopg2 not installed. Run: pip install psycopg2-binary --break-system-packages"
            )

    def _load_file_hashes(self, cursor, rel_files: set[Path]) -> dict[str, str]:
        """Load file_path → file_hash from Postgres for the affected file set."""
        if not rel_files:
            return {}
        file_strs = [str(p) for p in rel_files]
        placeholders = ",".join(["%s"] * len(file_strs))
        cursor.execute(f"""
            SELECT DISTINCT file_path, file_hash
            FROM nodes
            WHERE workspace_id = %s::UUID
              AND file_path IN ({placeholders})
              AND file_hash IS NOT NULL
        """, [self._workspace_id] + file_strs)
        return {row[0]: row[1] for row in cursor.fetchall()}

    def _upsert_nodes(self, cursor, nodes: list[NodeInfo]) -> int:
        """Batch upsert NodeInfo records into the nodes table."""
        if not nodes:
            return 0

        upserted = 0
        for i in range(0, len(nodes), _UPSERT_BATCH):
            batch = nodes[i: i + _UPSERT_BATCH]

            # Compute risk scores for this batch
            scored_batch = []
            for node in batch:
                score, factors = compute_risk_score(node)
                scored_batch.append((node, score, factors))

            # Build INSERT rows
            rows = []
            for node, score, factors in scored_batch:
                rows.append((
                    self._workspace_id,
                    node.qualified_name,    # used as external_id
                    node.name,
                    node.kind,              # node_type
                    node.file_path,
                    node.qualified_name,
                    node.file_hash,
                    node.line_start,
                    node.line_end,
                    score,
                    json.dumps(factors),
                    node.language,
                ))

            # Use executemany with ON CONFLICT upsert
            cursor.executemany("""
                INSERT INTO nodes (
                    workspace_id,
                    external_id,
                    name,
                    node_type,
                    file_path,
                    qualified_name,
                    file_hash,
                    line_start,
                    line_end,
                    risk_score,
                    risk_factors,
                    metadata
                ) VALUES (
                    %s::UUID, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB,
                    jsonb_build_object('language', %s)
                )
                ON CONFLICT (workspace_id, external_id)
                DO UPDATE SET
                    name           = EXCLUDED.name,
                    node_type      = EXCLUDED.node_type,
                    file_path      = EXCLUDED.file_path,
                    qualified_name = EXCLUDED.qualified_name,
                    file_hash      = EXCLUDED.file_hash,
                    line_start     = EXCLUDED.line_start,
                    line_end       = EXCLUDED.line_end,
                    risk_score     = EXCLUDED.risk_score,
                    risk_factors   = EXCLUDED.risk_factors,
                    updated_at     = now()
            """, rows)
            upserted += len(batch)

        log.info("Upserted %d nodes", upserted)
        return upserted

    def _upsert_edges(self, cursor, edges: list[EdgeInfo]) -> int:
        """Batch upsert EdgeInfo records into the edges table."""
        if not edges:
            return 0

        upserted = 0
        for i in range(0, len(edges), _UPSERT_BATCH):
            batch = edges[i: i + _UPSERT_BATCH]

            rows = []
            for edge in batch:
                rows.append((
                    self._workspace_id,
                    edge.source,    # external_id of source node
                    edge.target,    # external_id of target node
                    edge.kind,
                    self._workspace_id,
                    edge.source,
                    self._workspace_id,
                    edge.target,
                ))

            cursor.executemany("""
                INSERT INTO edges (
                    workspace_id,
                    source_id,
                    target_id,
                    edge_type,
                    confidence,
                    is_pruned,
                    last_seen
                )
                SELECT
                    %s::UUID,
                    src.id,
                    tgt.id,
                    %s,
                    1.0,
                    false,
                    now()
                FROM nodes src
                JOIN nodes tgt ON tgt.workspace_id = %s::UUID
                                AND tgt.external_id = %s
                WHERE src.workspace_id = %s::UUID
                  AND src.external_id  = %s
                ON CONFLICT (workspace_id, source_id, target_id, edge_type)
                DO UPDATE SET
                    confidence = GREATEST(edges.confidence, 1.0),
                    is_pruned  = false,
                    last_seen  = now()
            """, rows)
            upserted += len(batch)

        log.info("Upserted %d edges", upserted)
        return upserted


# ── Module-level helpers ──────────────────────────────────────────────────────

def _set_workspace(cursor, workspace_id: str) -> None:
    cursor.execute("SET LOCAL app.workspace_id = %s", (workspace_id,))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_worker(abs_path: str, repo_root: str) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Top-level function for ProcessPoolExecutor (must be picklable)."""
    from companybrain.structural.parser import parse_file
    result = parse_file(abs_path, repo_root=repo_root)
    return result.nodes, result.edges


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s  %(message)s")

    parser = argparse.ArgumentParser(
        description="Incremental structural indexer — ADR-006 Week 2"
    )
    parser.add_argument("--repo", required=True, help="Path to git repository")
    parser.add_argument("--workspace-id", required=True, help="Workspace UUID")
    parser.add_argument(
        "--db-url",
        default="postgresql://companybrain:companybrain@localhost:5432/companybrain",
    )
    parser.add_argument(
        "--mode",
        choices=["incremental", "full"],
        default="incremental",
        help="incremental: diff-only (default)  |  full: re-index everything",
    )
    parser.add_argument(
        "--since-sha",
        default=None,
        help="For incremental mode: diff from this SHA to HEAD",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        help=f"Parallel parse workers (default: {_DEFAULT_WORKERS})",
    )
    args = parser.parse_args()

    indexer = StructuralIndexer(
        db_url=args.db_url,
        workspace_id=args.workspace_id,
        max_workers=args.workers,
    )

    if args.mode == "full":
        result = indexer.run_full_index(args.repo)
    else:
        result = indexer.run_incremental(args.repo, since_sha=args.since_sha)

    print(f"\n{'─'*50}")
    print(f"Mode          : {result.mode}")
    print(f"Files parsed  : {result.files_parsed}")
    print(f"Files skipped : {result.files_skipped}  (hash unchanged)")
    print(f"Files failed  : {result.files_failed}")
    print(f"Nodes upserted: {result.nodes_upserted}")
    print(f"Edges upserted: {result.edges_upserted}")
    print(f"Duration      : {result.duration_ms:.0f}ms")
    if result.last_sha:
        print(f"HEAD SHA      : {result.last_sha[:12]}")
    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors[:10]:
            print(f"  {err}")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    _cli()
