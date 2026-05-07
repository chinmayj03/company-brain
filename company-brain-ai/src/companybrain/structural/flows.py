# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/flows.py — detect_entry_points(), trace_flows(),
#           compute_criticality()
#
# Key changes from the original:
#   - Queries our Postgres nodes/edges tables instead of SQLite.
#   - Writes results to the flows + flow_memberships tables (ADR-006 schema).
#   - Multi-tenant: all queries scoped to workspace_id.
#   - Criticality formula unchanged from CRG; weights kept identical.
"""ADR-006 §9: Execution flow detection and BFS tracing.

Detects framework entry-points (HTTP route handlers, event listeners, scheduled
tasks, CLI commands, etc.) and traces the execution flow from each entry-point
via BFS over CALLS edges, up to a configurable depth.

For each flow:
  - depth      — maximum hop depth reached
  - node_count — total nodes in the BFS path
  - file_count — number of distinct source files
  - criticality — composite score (fan-in × risk × security)

Results are written to the `flows` and `flow_memberships` tables.

Usage::

    from companybrain.structural.flows import FlowDetector

    detector = FlowDetector(db_url="postgresql://...", workspace_id="uuid")
    result   = detector.run(repo_root="/path/to/repo", max_depth=10)
    print(result.flows_detected, result.nodes_in_flows)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

# ── Entry-point detection regexes ─────────────────────────────────────────────
#
# Ported from CRG's constants.py + flows.py entry-point heuristics.
# Each pattern matches a decorator, annotation, or function-name convention
# that signals an execution entry-point for a specific framework.

_ENTRY_POINT_PATTERNS: list[re.Pattern] = [p for p in [
    # Python web frameworks
    re.compile(r'@app\.(get|post|put|delete|patch|route)\b'),   # Flask / FastAPI
    re.compile(r'@router\.(get|post|put|delete|patch)\b'),       # FastAPI router
    re.compile(r'@blueprint\.\w+'),                              # Flask blueprints
    re.compile(r'@api\.route\b'),                                # flask-restful

    # Python async / tasks
    re.compile(r'@celery\.task\b'),
    re.compile(r'@shared_task\b'),
    re.compile(r'@app\.task\b'),
    re.compile(r'@periodic_task\b'),
    re.compile(r'@schedule\b'),

    # Spring (Java) — annotations on classes and methods
    re.compile(r'@(RestController|Controller|RequestMapping|GetMapping|PostMapping|'
               r'PutMapping|DeleteMapping|PatchMapping)\b'),
    re.compile(r'@(EventListener|KafkaListener|RabbitListener|JmsListener|'
               r'SqsListener|ScheduledFuture|Scheduled)\b'),
    re.compile(r'@(CommandLineRunner|ApplicationRunner)\b'),

    # TypeScript / Node.js
    re.compile(r'@(Get|Post|Put|Delete|Patch|Controller|EventPattern|'
               r'MessagePattern|GrpcMethod)\b'),   # NestJS
    re.compile(r'router\.(get|post|put|delete|patch)\s*\('),     # Express

    # Go (function-name conventions)
    re.compile(r'func\s+\w*(Handler|Controller|Serve|Listen)\w*\s*\('),
    re.compile(r'http\.(HandleFunc|Handle)\s*\('),
    re.compile(r'gin\.(GET|POST|PUT|DELETE|PATCH)\s*\('),

    # CLI entry-points
    re.compile(r'@click\.command\b'),
    re.compile(r'@click\.group\b'),
    re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']\s*:'),

    # Generic conventions
    re.compile(r'\bdef\s+(handle_|on_|process_|consume_)\w+'),  # handler naming
    re.compile(r'\bpublic\s+\w+\s+(handle|process|consume)\w*\s*\('),  # Java handlers
] if p is not None]

# Maximum BFS depth when tracing a flow.  CRG default is 10.
_DEFAULT_MAX_DEPTH: int = 10

# Minimum flow size to persist (avoids noise from trivial 1-node "flows").
_MIN_FLOW_NODES: int = 2

# Criticality weight constants (identical to CRG).
_W_FAN_IN   = 0.40   # how many other nodes call this entry-point
_W_RISK     = 0.40   # average risk score of nodes in the flow
_W_SECURITY = 0.20   # does any node in the flow contain a security keyword


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class FlowResult:
    """Summary of one flow detection run."""
    workspace_id: str
    flows_detected: int = 0
    nodes_in_flows: int = 0
    files_in_flows: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class _Flow:
    """Internal representation of one detected flow."""
    flow_id: str
    name: str
    entry_node_id: str
    entry_node_name: str
    depth: int
    nodes: list[dict]   # list of {node_id, name, node_type, file_path, risk_score, position}
    criticality: float


# ── Flow detector ─────────────────────────────────────────────────────────────

class FlowDetector:
    """Detects and persists execution flows for a workspace.

    One instance per pipeline run. Uses a synchronous psycopg2 connection
    (same pattern as StructuralIndexer).
    """

    def __init__(
        self,
        db_url: str,
        workspace_id: str,
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ):
        self._db_url = db_url
        self._workspace_id = workspace_id
        self._max_depth = max_depth

    def run(self) -> FlowResult:
        """Detect entry-points, trace flows, write to DB. Returns summary."""
        t0 = time.monotonic()
        result = FlowResult(workspace_id=self._workspace_id)

        conn = self._connect()
        try:
            cursor = conn.cursor()

            # 1 — Find entry-point nodes
            entry_nodes = self._detect_entry_points(cursor)
            log.info("Flow detection: %d entry-points found", len(entry_nodes))

            if not entry_nodes:
                result.duration_ms = (time.monotonic() - t0) * 1000
                return result

            # 2 — Trace BFS from each entry-point
            flows: list[_Flow] = []
            for entry in entry_nodes:
                try:
                    flow = self._trace_flow(cursor, entry)
                    if flow and len(flow.nodes) >= _MIN_FLOW_NODES:
                        flows.append(flow)
                except Exception as exc:
                    msg = f"Flow trace failed for {entry.get('name')}: {exc}"
                    log.warning(msg)
                    result.errors.append(msg)

            log.info("Flow detection: %d flows traced", len(flows))

            # 3 — Persist to DB (replace existing flows for this workspace)
            self._persist_flows(cursor, conn, flows)

            result.flows_detected = len(flows)
            result.nodes_in_flows = sum(len(f.nodes) for f in flows)
            result.files_in_flows = len({
                n.get("file_path") for f in flows for n in f.nodes if n.get("file_path")
            })

            cursor.close()
        finally:
            conn.close()

        result.duration_ms = (time.monotonic() - t0) * 1000
        log.info(
            "Flow detection complete: flows=%d nodes=%d files=%d duration=%.0fms",
            result.flows_detected, result.nodes_in_flows,
            result.files_in_flows, result.duration_ms,
        )
        return result

    # ── Entry-point detection ─────────────────────────────────────────────────

    def _detect_entry_points(self, cursor) -> list[dict]:
        """Return nodes whose source matches any entry-point pattern."""
        # We store the decorated source fragment in node_type or qualified_name;
        # the parser also sets a 'decorators' column via the structural schema.
        # Primary signal: node name pattern + any decorator stored as metadata.
        cursor.execute("""
            SELECT id, name, node_type, qualified_name, file_path, risk_score
            FROM nodes
            WHERE workspace_id = %s::UUID
              AND node_type IN ('Function', 'Method', 'Handler', 'Route', 'Endpoint')
              AND is_pruned = false
        """, [self._workspace_id])

        rows = cursor.fetchall()
        entry_nodes = []
        for row in rows:
            node_id, name, node_type, qualified_name, file_path, risk_score = row
            search_text = " ".join(filter(None, [name, qualified_name or "", node_type or ""]))
            if any(p.search(search_text) for p in _ENTRY_POINT_PATTERNS):
                entry_nodes.append({
                    "id": str(node_id),
                    "name": name,
                    "node_type": node_type,
                    "qualified_name": qualified_name,
                    "file_path": file_path,
                    "risk_score": float(risk_score) if risk_score is not None else 0.0,
                })

        return entry_nodes

    # ── BFS flow tracing ──────────────────────────────────────────────────────

    def _trace_flow(self, cursor, entry: dict) -> Optional[_Flow]:
        """BFS from entry-point over CALLS edges, up to max_depth."""
        entry_id = entry["id"]

        # BFS state
        visited: dict[str, dict] = {entry_id: {**entry, "position": 0}}
        frontier: list[str] = [entry_id]
        depth = 0

        while frontier and depth < self._max_depth:
            depth += 1
            batch = frontier[:]
            frontier = []

            placeholders = ",".join(["%s"] * len(batch))
            cursor.execute(f"""
                SELECT DISTINCT
                    n.id::TEXT,
                    n.name,
                    n.node_type,
                    n.qualified_name,
                    n.file_path,
                    COALESCE(n.risk_score, 0.0)
                FROM edges e
                JOIN nodes n ON n.id = e.target_id
                WHERE e.workspace_id = %s::UUID
                  AND e.edge_type = 'CALLS'
                  AND e.is_pruned = false
                  AND e.source_id::TEXT IN ({placeholders})
                  AND n.id::TEXT NOT IN ({placeholders})
            """, [self._workspace_id] + batch + list(visited.keys()))

            for row in cursor.fetchall():
                node_id, name, node_type, qname, file_path, risk_score = row
                nid = str(node_id)
                if nid not in visited:
                    visited[nid] = {
                        "id": nid,
                        "name": name,
                        "node_type": node_type,
                        "qualified_name": qname,
                        "file_path": file_path,
                        "risk_score": float(risk_score),
                        "position": len(visited),
                    }
                    frontier.append(nid)

        if len(visited) < _MIN_FLOW_NODES:
            return None

        nodes = sorted(visited.values(), key=lambda n: n["position"])
        criticality = self._compute_criticality(cursor, entry_id, nodes)

        return _Flow(
            flow_id=str(uuid4()),
            name=entry["name"],
            entry_node_id=entry_id,
            entry_node_name=entry["name"],
            depth=depth,
            nodes=nodes,
            criticality=criticality,
        )

    # ── Criticality scoring ───────────────────────────────────────────────────

    def _compute_criticality(self, cursor, entry_id: str, nodes: list[dict]) -> float:
        """Compute criticality for a flow. Ported from CRG compute_criticality().

        criticality = W_FAN_IN * fan_in_score
                    + W_RISK * avg_risk_score
                    + W_SECURITY * security_flag

        fan_in_score: normalised count of nodes that CALL the entry-point (capped at 1.0).
        avg_risk_score: mean risk_score across all nodes in the flow.
        security_flag: 1.0 if any node name contains a security keyword.
        """
        # Fan-in: how many nodes call this entry-point?
        cursor.execute("""
            SELECT COUNT(DISTINCT source_id)
            FROM edges
            WHERE workspace_id = %s::UUID
              AND edge_type = 'CALLS'
              AND target_id = %s::UUID
              AND is_pruned = false
        """, [self._workspace_id, entry_id])
        fan_in = int((cursor.fetchone() or [0])[0])
        fan_in_score = min(fan_in / 10.0, 1.0)   # cap at 10 callers = 1.0

        # Average risk score
        risk_scores = [n.get("risk_score") or 0.0 for n in nodes]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0.0

        # Security flag
        _SEC_KEYWORDS = frozenset({
            "auth", "token", "crypt", "password", "secret", "jwt", "oauth",
            "permission", "role", "acl", "admin", "payment", "charge", "refund",
            "wallet", "bank", "invoice", "pii", "encrypt", "decrypt",
        })
        security_flag = 1.0 if any(
            any(kw in (n.get("name") or "").lower() for kw in _SEC_KEYWORDS)
            for n in nodes
        ) else 0.0

        criticality = (
            _W_FAN_IN   * fan_in_score +
            _W_RISK     * avg_risk +
            _W_SECURITY * security_flag
        )
        return min(round(criticality, 3), 1.0)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_flows(self, cursor, conn, flows: list[_Flow]) -> None:
        """Delete existing flows for this workspace and insert new ones."""
        import json

        # Delete existing
        cursor.execute("""
            DELETE FROM flow_memberships
            WHERE flow_id IN (
                SELECT id FROM flows WHERE workspace_id = %s::UUID
            )
        """, [self._workspace_id])
        cursor.execute(
            "DELETE FROM flows WHERE workspace_id = %s::UUID",
            [self._workspace_id],
        )

        for flow in flows:
            # Insert flow
            path_json = json.dumps([n["id"] for n in flow.nodes])
            cursor.execute("""
                INSERT INTO flows
                    (id, workspace_id, name, entry_node_id, depth,
                     node_count, file_count, criticality, path_json)
                VALUES
                    (%s::UUID, %s::UUID, %s, %s::UUID, %s, %s, %s, %s, %s::JSONB)
            """, [
                flow.flow_id, self._workspace_id, flow.name,
                flow.entry_node_id, flow.depth,
                len(flow.nodes),
                len({n.get("file_path") for n in flow.nodes if n.get("file_path")}),
                flow.criticality,
                path_json,
            ])

            # Insert memberships
            if flow.nodes:
                values = ",".join(
                    cursor.mogrify("(%s::UUID, %s::UUID, %s)", [
                        flow.flow_id, n["id"], n["position"],
                    ]).decode()
                    for n in flow.nodes
                )
                cursor.execute(
                    f"INSERT INTO flow_memberships (flow_id, node_id, position) VALUES {values}"
                )

        conn.commit()
        log.info("Persisted %d flows to DB", len(flows))

    # ── DB connection ─────────────────────────────────────────────────────────

    def _connect(self):
        try:
            import psycopg2
            return psycopg2.connect(self._db_url)
        except ImportError:
            raise RuntimeError("psycopg2 not installed — pip install psycopg2-binary")


# ── Standalone helpers (called directly by MCP tools) ────────────────────────

def is_entry_point(node_name: str, qualified_name: str = "") -> bool:
    """Quick check: does this node look like an entry-point?"""
    text = f"{node_name} {qualified_name}"
    return any(p.search(text) for p in _ENTRY_POINT_PATTERNS)
