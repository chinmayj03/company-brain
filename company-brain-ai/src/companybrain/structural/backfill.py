"""ADR-006 §30: One-time structural backfill for existing workspaces.

Runs the tree-sitter parser against a target repository, then:
  1. Upserts qualified_name, file_hash, line_start, line_end onto existing nodes
     that can be matched by external_id.
  2. Computes and stores risk_score + risk_factors for every structurally-scanned node.

Usage (against CRG fixture repo for demo / equivalence test)::

    python -m companybrain.structural.backfill \\
        --repo /tmp/code-review-graph \\
        --workspace-id 00000000-0000-0000-0000-000000000001 \\
        --db-url postgresql://companybrain:companybrain@localhost:5432/companybrain \\
        --dry-run          # show what would change without writing

    # Without dry-run, actually writes to Postgres:
    python -m companybrain.structural.backfill \\
        --repo /tmp/code-review-graph \\
        --workspace-id 00000000-0000-0000-0000-000000000001 \\
        --db-url postgresql://companybrain:companybrain@localhost:5432/companybrain

The script is idempotent: re-running it updates values in-place with no duplicates.

Week 1 note: Risk scoring works with the data available at this point.
Flow participation and community_crossing will be 0 until weeks 4+ populate
those tables.  The score is re-computable at any time by re-running this script.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ---------------------------------------------------------------------------
# DB helpers (sync psycopg2 — simpler for a one-shot script)
# ---------------------------------------------------------------------------

def _get_conn(db_url: str):
    try:
        import psycopg2
        return psycopg2.connect(db_url)
    except ImportError:
        log.error("psycopg2 not installed. Run: pip install psycopg2-binary --break-system-packages")
        sys.exit(1)


def _set_workspace(cursor, workspace_id: str) -> None:
    """Set RLS context so queries are scoped to the right workspace."""
    cursor.execute("SET LOCAL app.workspace_id = %s", (workspace_id,))


# ---------------------------------------------------------------------------
# Structural data queries
# ---------------------------------------------------------------------------

def _fetch_caller_counts(cursor, workspace_id: str) -> dict[str, int]:
    """Return a mapping of node external_id → number of CALLS edges targeting it."""
    cursor.execute("""
        SELECT n.external_id, COUNT(e.id) AS caller_count
        FROM edges e
        JOIN nodes n ON n.id = e.target_id
        WHERE e.workspace_id = %s
          AND e.edge_type = 'CALLS'
          AND e.is_pruned = false
        GROUP BY n.external_id
    """, (workspace_id,))
    return {row[0]: row[1] for row in cursor.fetchall()}


def _fetch_test_counts(cursor, workspace_id: str) -> dict[str, int]:
    """Return a mapping of node external_id → TESTED_BY edge count."""
    cursor.execute("""
        SELECT n.external_id, COUNT(e.id) AS test_count
        FROM edges e
        JOIN nodes n ON n.id = e.target_id
        WHERE e.workspace_id = %s
          AND e.edge_type = 'TESTED_BY'
          AND e.is_pruned = false
        GROUP BY n.external_id
    """, (workspace_id,))
    return {row[0]: row[1] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def run_backfill(
    repo_path: str,
    workspace_id: str,
    db_url: str,
    dry_run: bool = False,
) -> dict:
    """Parse the repo and write structural columns to Postgres.

    Returns a summary dict with counts of nodes scanned, updated, skipped.
    """
    from companybrain.structural.parser import parse_directory, ParseResult
    from companybrain.structural.risk import score_from_row

    repo = Path(repo_path)
    if not repo.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")

    log.info("Starting structural backfill: repo=%s workspace=%s dry_run=%s",
             repo_path, workspace_id, dry_run)

    # ── Step 1: Parse the repository ─────────────────────────────────────
    log.info("Parsing repository with tree-sitter...")
    results: list[ParseResult] = parse_directory(str(repo), repo_root=str(repo))

    all_nodes = [n for r in results for n in r.nodes]
    all_edges = [e for r in results for e in r.edges]
    error_files = [r.file_path for r in results if r.error and "No tree-sitter grammar" not in r.error]

    log.info("Parse complete: %d files, %d nodes, %d edges, %d errors",
             len(results), len(all_nodes), len(all_edges), len(error_files))
    if error_files:
        log.warning("Files with parse errors: %s", error_files[:10])

    # Index parsed nodes by qualified_name and file path for fast lookup
    qname_to_node = {n.qualified_name: n for n in all_nodes if n.kind != "File"}

    if dry_run:
        log.info("[DRY RUN] Would update %d non-File nodes", len(qname_to_node))
        _print_sample(list(qname_to_node.values())[:5])
        return {
            "dry_run": True,
            "files_parsed": len(results),
            "nodes_found": len(all_nodes),
            "edges_found": len(all_edges),
        }

    # ── Step 2: Connect and fetch supporting data ─────────────────────────
    conn = _get_conn(db_url)
    conn.autocommit = False
    cursor = conn.cursor()

    try:
        _set_workspace(cursor, workspace_id)

        caller_counts = _fetch_caller_counts(cursor, workspace_id)
        test_counts   = _fetch_test_counts(cursor, workspace_id)

        log.info("Fetched %d caller-count entries, %d test-count entries",
                 len(caller_counts), len(test_counts))

        # ── Step 3: Score all nodes, then write via temp table + single UPDATE FROM
        #
        # Old approach: one UPDATE per node  → O(N) round-trips (~5-10 ms each).
        # New approach: bulk-load into a temp table, then one UPDATE FROM joins it
        #               against the live `nodes` table → 3 round-trips total.
        #
        log.info("Computing risk scores and building batch (%d nodes)…", len(qname_to_node))

        batch_rows: list[tuple] = []
        for qname, node in qname_to_node.items():
            input_row = {
                "name":                         node.name,
                "qualified_name":               node.qualified_name,
                "flow_count":                   0,    # week 4: flows.py
                "flow_criticality_sum":         0.0,  # week 4
                "cross_community_caller_count": 0,    # week 4: communities.py
                "test_count":                   test_counts.get(qname, 0),
                "caller_count":                 caller_counts.get(qname, 0),
            }
            score, factors_dict = score_from_row(input_row)
            batch_rows.append((
                node.qualified_name,
                node.file_hash,
                node.line_start,
                node.line_end,
                float(score),
                json.dumps(factors_dict),
                node.name,
            ))

        # Round-trip 1: create temp staging table (auto-dropped at commit)
        cursor.execute("""
            CREATE TEMP TABLE _structural_batch (
                qualified_name  TEXT    NOT NULL,
                file_hash       TEXT,
                line_start      INTEGER,
                line_end        INTEGER,
                risk_score      NUMERIC(3,2),
                risk_factors    JSONB,
                simple_name     TEXT
            ) ON COMMIT DROP
        """)

        # Round-trip 2: batch-insert all scored rows.
        # psycopg2 executemany uses an extended-query pipeline — single network round-trip.
        cursor.executemany(
            "INSERT INTO _structural_batch VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
            batch_rows,
        )

        # Round-trip 3: single UPDATE FROM joining temp table to live nodes.
        # Priority match: exact qualified_name first, then name-substring fallback.
        cursor.execute("""
            UPDATE nodes n
            SET
                qualified_name = b.qualified_name,
                file_hash      = b.file_hash,
                line_start     = b.line_start,
                line_end       = b.line_end,
                risk_score     = b.risk_score,
                risk_factors   = b.risk_factors,
                updated_at     = now()
            FROM _structural_batch b
            WHERE n.workspace_id = %s
              AND n.qualified_name IS NULL
              AND (
                  n.external_id = b.qualified_name
                  OR n.external_id ILIKE '%%' || b.simple_name || '%%'
              )
        """, (workspace_id,))

        updated = cursor.rowcount
        skipped = len(batch_rows) - updated

        conn.commit()
        log.info("Backfill complete: %d nodes updated, %d not matched in DB",
                 updated, skipped)

        return {
            "dry_run": False,
            "files_parsed": len(results),
            "nodes_found":  len(all_nodes),
            "edges_found":  len(all_edges),
            "nodes_updated": updated,
            "nodes_skipped": skipped,
        }

    except Exception:
        conn.rollback()
        log.exception("Backfill failed; transaction rolled back")
        raise
    finally:
        cursor.close()
        conn.close()


def _print_sample(nodes) -> None:
    """Print a sample of parsed nodes for dry-run inspection."""
    from companybrain.structural.risk import score_from_row
    log.info("Sample parsed nodes (dry-run):")
    for node in nodes:
        row = {"name": node.name, "qualified_name": node.qualified_name,
               "test_count": 0, "caller_count": 0}
        score, factors = score_from_row(row)
        log.info("  [%s] %s  →  risk %.2f  file_hash=%s…",
                 node.kind, node.qualified_name, score, node.file_hash[:8])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ADR-006 §30: Structural backfill script")
    parser.add_argument("--repo", required=True,
                        help="Path to repository to parse (e.g. /tmp/code-review-graph)")
    parser.add_argument("--workspace-id", required=True,
                        help="Workspace UUID to update (e.g. 00000000-0000-0000-0000-000000000001)")
    parser.add_argument("--db-url",
                        default="postgresql://companybrain:companybrain@localhost:5432/companybrain",
                        help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and compute risk scores but don't write to DB")
    args = parser.parse_args()

    summary = run_backfill(
        repo_path=args.repo,
        workspace_id=args.workspace_id,
        db_url=args.db_url,
        dry_run=args.dry_run,
    )

    print("\n── Backfill Summary ──────────────────────────────")
    for k, v in summary.items():
        print(f"  {k:<25} {v}")
    print("──────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
