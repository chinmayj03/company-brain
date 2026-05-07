# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/analysis.py — find_hub_nodes(), find_bridge_nodes()
#
# Key changes from the original:
#   - Loads graph from Postgres edges table (not SQLite).
#   - Writes results to graph_metrics table (hub_degree, bridge_betweenness).
#   - Betweenness sampled at k=500 for workspaces > 5K nodes (same threshold as CRG).
#   - Multi-tenant: all queries scoped to workspace_id.
"""ADR-006 §10: Hub and bridge node detection — nightly topology job.

Hub nodes (high degree) and bridge nodes (high betweenness centrality) are the
structural chokepoints of the dependency graph. Both are important for:
  - Understanding which nodes have the highest blast radius
  - Prioritising refactoring candidates
  - Explaining architectural risk to non-engineers

Results are written to the `graph_metrics` table and served by the backend's
`/api/workspaces/{id}/graph/hubs` and `/api/workspaces/{id}/graph/bridges` endpoints.

Runs nightly as a Celery task (registered at the bottom of this module).

Usage::

    from companybrain.structural.topology import TopologyAnalyser

    analyser = TopologyAnalyser(db_url="postgresql://...", workspace_id="uuid")
    result   = analyser.run(top_hubs=50, top_bridges=25)
    print(result.hubs_written, result.bridges_written)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Betweenness centrality sample size.
# NetworkX approximates betweenness using k random pivots when k < |V|.
# CRG threshold: sample when |V| > 5 000 nodes (too slow otherwise).
_BETWEENNESS_SAMPLE_K: int = 500
_BETWEENNESS_THRESHOLD: int = 5_000


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class TopologyResult:
    workspace_id: str
    hubs_written: int = 0
    bridges_written: int = 0
    node_count: int = 0
    edge_count: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── Analyser ──────────────────────────────────────────────────────────────────

class TopologyAnalyser:
    """Computes hub + bridge metrics and writes them to graph_metrics.

    Designed to run nightly. One instance per workspace.
    """

    def __init__(self, db_url: str, workspace_id: str):
        self._db_url = db_url
        self._workspace_id = workspace_id

    def run(
        self,
        top_hubs: int = 50,
        top_bridges: int = 25,
    ) -> TopologyResult:
        """Load graph from Postgres, compute metrics, write to graph_metrics."""
        import networkx as nx

        t0 = time.monotonic()
        result = TopologyResult(workspace_id=self._workspace_id)

        conn = self._connect()
        try:
            cursor = conn.cursor()

            # Load graph
            G = self._load_graph(cursor)
            result.node_count = G.number_of_nodes()
            result.edge_count = G.number_of_edges()

            if result.node_count == 0:
                log.info("topology: workspace %s has no nodes — skipping", self._workspace_id)
                result.duration_ms = (time.monotonic() - t0) * 1000
                cursor.close()
                return result

            log.info(
                "topology: loaded graph (%d nodes, %d edges) for workspace %s",
                result.node_count, result.edge_count, self._workspace_id,
            )

            # Hubs — top-N by degree (in + out)
            hub_scores = find_hub_nodes(G, top_n=top_hubs)
            result.hubs_written = self._write_metrics(
                cursor, conn, hub_scores, metric_kind="hub_degree"
            )

            # Bridges — top-N by betweenness centrality
            bridge_scores = find_bridge_nodes(G, top_n=top_bridges)
            result.bridges_written = self._write_metrics(
                cursor, conn, bridge_scores, metric_kind="bridge_betweenness"
            )

            cursor.close()
        except Exception as exc:
            result.errors.append(str(exc))
            log.error("topology: analysis failed for workspace %s: %s", self._workspace_id, exc)
        finally:
            conn.close()

        result.duration_ms = (time.monotonic() - t0) * 1000
        log.info(
            "topology complete: hubs=%d bridges=%d duration=%.0fms",
            result.hubs_written, result.bridges_written, result.duration_ms,
        )
        return result

    # ── Graph loading ─────────────────────────────────────────────────────────

    def _load_graph(self, cursor) -> Any:
        """Load workspace edges from Postgres into a NetworkX DiGraph."""
        import networkx as nx

        cursor.execute("""
            SELECT source_id::TEXT, target_id::TEXT
            FROM edges
            WHERE workspace_id = %s::UUID
              AND is_pruned = false
              AND edge_type IN ('CALLS', 'IMPORTS_FROM', 'DEPENDS_ON')
        """, [self._workspace_id])

        G = nx.DiGraph()
        for source_id, target_id in cursor.fetchall():
            G.add_edge(source_id, target_id)

        return G

    # ── Metric persistence ────────────────────────────────────────────────────

    def _write_metrics(
        self,
        cursor,
        conn,
        scores: list[tuple[str, float]],
        metric_kind: str,
    ) -> int:
        """Upsert (node_id, metric_kind, score, rank) rows into graph_metrics."""
        if not scores:
            return 0

        # Delete stale rows for this workspace + kind
        cursor.execute("""
            DELETE FROM graph_metrics
            WHERE workspace_id = %s::UUID AND metric_kind = %s
        """, [self._workspace_id, metric_kind])

        rows_written = 0
        for rank, (node_id, score) in enumerate(scores, start=1):
            try:
                cursor.execute("""
                    INSERT INTO graph_metrics
                        (workspace_id, node_id, metric_kind, score, rank, computed_at)
                    VALUES
                        (%s::UUID, %s::UUID, %s, %s, %s, NOW())
                    ON CONFLICT (workspace_id, node_id, metric_kind)
                    DO UPDATE SET score = EXCLUDED.score,
                                  rank  = EXCLUDED.rank,
                                  computed_at = NOW()
                """, [self._workspace_id, node_id, metric_kind, score, rank])
                rows_written += 1
            except Exception as exc:
                log.debug("Could not write metric for node %s: %s", node_id, exc)

        conn.commit()
        return rows_written

    # ── DB connection ─────────────────────────────────────────────────────────

    def _connect(self):
        try:
            import psycopg2
            return psycopg2.connect(self._db_url)
        except ImportError:
            raise RuntimeError("psycopg2 not installed — pip install psycopg2-binary")


# ── Pure graph analysis functions (testable without DB) ───────────────────────

def find_hub_nodes(G: Any, top_n: int = 50) -> list[tuple[str, float]]:
    """Return top-N nodes sorted by total degree (in-degree + out-degree).

    Ported from CRG's analysis.py::find_hub_nodes.

    Args:
        G:     NetworkX DiGraph.
        top_n: Maximum nodes to return.

    Returns:
        List of (node_id, degree_score) tuples, sorted descending.
    """
    if G.number_of_nodes() == 0:
        return []

    # Combine in + out degree into a single "hub" score.
    # Normalise by max degree so score is in [0, 1].
    degree_map: dict[str, int] = {
        n: G.in_degree(n) + G.out_degree(n)
        for n in G.nodes()
    }
    max_degree = max(degree_map.values()) if degree_map else 1
    if max_degree == 0:
        return []

    ranked = sorted(
        ((node, deg / max_degree) for node, deg in degree_map.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[:top_n]


def find_bridge_nodes(G: Any, top_n: int = 25) -> list[tuple[str, float]]:
    """Return top-N nodes by betweenness centrality.

    For large graphs (> _BETWEENNESS_THRESHOLD nodes), uses approximate
    betweenness with k=_BETWEENNESS_SAMPLE_K random pivots (same threshold
    and sample size as CRG).

    Args:
        G:     NetworkX DiGraph.
        top_n: Maximum nodes to return.

    Returns:
        List of (node_id, betweenness) tuples, sorted descending.
    """
    import networkx as nx

    if G.number_of_nodes() == 0:
        return []

    n = G.number_of_nodes()

    if n > _BETWEENNESS_THRESHOLD:
        log.info(
            "betweenness: graph has %d nodes — using approximate (k=%d)",
            n, _BETWEENNESS_SAMPLE_K,
        )
        centrality = nx.betweenness_centrality(
            G, k=_BETWEENNESS_SAMPLE_K, normalized=True
        )
    else:
        centrality = nx.betweenness_centrality(G, normalized=True)

    ranked = sorted(centrality.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


# ── Celery nightly task ───────────────────────────────────────────────────────

def register_nightly_task(celery_app: Any) -> None:
    """Register the topology nightly job with Celery.

    Call this from the Celery app factory. The task is run for every active
    workspace; workspace_ids are discovered via the workspaces table.

    Example in celery_app.py::

        from companybrain.structural.topology import register_nightly_task
        register_nightly_task(celery_app)
    """
    import os

    db_url = os.getenv("DATABASE_URL", "")

    @celery_app.task(
        name="companybrain.topology.nightly",
        soft_time_limit=3600,   # 1 hour max per run
    )
    def topology_nightly():
        """Nightly topology job: compute hubs + bridges for all workspaces."""
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM workspaces WHERE is_active = true")
            workspace_ids = [str(row[0]) for row in cursor.fetchall()]
            cursor.close()
            conn.close()
        except Exception as exc:
            log.error("topology_nightly: failed to list workspaces: %s", exc)
            return

        log.info("topology_nightly: running for %d workspaces", len(workspace_ids))
        for wid in workspace_ids:
            try:
                analyser = TopologyAnalyser(db_url=db_url, workspace_id=wid)
                result = analyser.run()
                log.info(
                    "topology_nightly: workspace=%s hubs=%d bridges=%d",
                    wid, result.hubs_written, result.bridges_written,
                )
            except Exception as exc:
                log.error("topology_nightly: workspace=%s error: %s", wid, exc)
