"""ADR-006 §28: Structural-index query helpers for CodeTracer.

Replaces brute-force regex filesystem scans with targeted Postgres queries
against the structural layer (nodes + edges tables populated by parser.py).

Design
------
CodeTracer previously found handler files by scanning every file in the repo
with regexes. After ADR-006, the structural layer already parsed all files and
stored node type, qualified_name, and file_path in Postgres.

This module provides three query functions:

  find_handler_candidates(...)   — Controller/Route/Handler files for an endpoint
  find_api_caller_candidates(...)— TypeScript/JS files that call an API endpoint
  get_import_targets(...)        — Files imported by a specific file (replaces _TS_IMPORT_RE / _PY_IMPORT_RE)

Pattern
-------
Each function returns a list of file paths. CodeTracer calls these FIRST and
only falls back to the full regex filesystem scan when the list is empty (i.e.
structural index not yet populated for this workspace).

This is the "graceful degradation" pattern from ADR-006 §28:
  structural-index fast path → regex fallback on empty result

Usage::

    from companybrain.structural.index_helper import StructuralIndexHelper

    helper = StructuralIndexHelper(db_url="postgresql://...", workspace_id="uuid")
    candidates = helper.find_handler_candidates("/api/users/charge", "java")
    # → ["backend/src/main/java/com/example/UserController.java", ...]
"""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# Node types in the structural index that represent HTTP/event entry-points.
_HANDLER_NODE_TYPES = frozenset({
    "Function", "Method", "Handler", "Route", "Endpoint", "Controller",
})

# Keywords in qualified_name / file_path that indicate controller / handler classes.
_CONTROLLER_KEYWORDS = (
    "controller", "resource", "handler", "endpoint", "route", "rest",
    "api", "router", "servlet", "view",
)


class StructuralIndexHelper:
    """Queries the structural index (Postgres) to assist CodeTracer.

    Methods are synchronous (psycopg2) to match CodeTracer's sync execution model.
    All queries are scoped to workspace_id via parameterised SQL.
    """

    def __init__(self, db_url: str, workspace_id: str):
        self._db_url = db_url
        self._workspace_id = workspace_id
        self._conn = None  # lazy connect

    # ── Public API ─────────────────────────────────────────────────────────────

    def find_handler_candidates(
        self,
        endpoint: str,
        language: str,
        limit: int = 20,
    ) -> list[str]:
        """Return file paths that likely contain the handler for *endpoint*.

        Queries nodes where:
          - node_type is a handler/controller kind
          - file_path or qualified_name contains controller-like naming
          - name / qualified_name contains segments of the endpoint path

        Returns a deduplicated, ordered list of file paths. Empty list means
        the structural index has no data — caller should fall back to regex scan.

        Args:
            endpoint: API path string, e.g. "/api/users/charge".
            language: "java", "python", "typescript", or "javascript".
            limit:    Maximum candidate files to return.
        """
        path_segments = [
            seg for seg in endpoint.split("/")
            if seg and not re.match(r'^v\d+$', seg) and seg not in ("api", "rest")
        ]
        if not path_segments:
            return []

        cursor = self._cursor()
        if cursor is None:
            return []

        try:
            # Build a LIKE pattern matching any path segment in the qualified_name.
            # This catches e.g. "UserController.chargeUser" matching "/users/charge".
            conditions = " OR ".join(
                f"(n.qualified_name ILIKE %s OR n.name ILIKE %s)"
                for _ in path_segments
            )
            params: list = [self._workspace_id]
            for seg in path_segments:
                params += [f"%{seg}%", f"%{seg}%"]

            # Language filter: Java → .java; Python → .py; TS/JS → .ts .tsx .js .jsx
            lang_filter = self._lang_filter(language)
            if lang_filter:
                conditions = f"({conditions}) AND n.file_path ILIKE %s"
                params.append(lang_filter)

            cursor.execute(f"""
                SELECT DISTINCT n.file_path
                FROM nodes n
                WHERE n.workspace_id = %s::UUID
                  AND n.node_type = ANY(ARRAY['Function','Method','Handler',
                                              'Route','Endpoint','Controller'])
                  AND n.is_pruned = false
                  AND ({conditions})
                ORDER BY n.file_path
                LIMIT %s
            """, params + [limit])

            rows = cursor.fetchall()
            results = [row[0] for row in rows if row[0]]
            log.debug(
                "find_handler_candidates: endpoint=%s lang=%s candidates=%d",
                endpoint, language, len(results),
            )
            return results

        except Exception as exc:
            log.warning("find_handler_candidates query failed: %s", exc)
            return []
        finally:
            cursor.close()

    def find_api_caller_candidates(
        self,
        endpoint: str,
        limit: int = 30,
    ) -> list[str]:
        """Return TypeScript/JS files that contain nodes calling *endpoint*.

        Queries for file_path values whose nodes have CALLS edges pointing to
        Route/Endpoint nodes that match the endpoint path segments.

        Falls back gracefully to empty list if no data is found.

        Args:
            endpoint: API path string, e.g. "/api/users/charge".
            limit:    Maximum candidate files to return.
        """
        path_segments = [
            seg for seg in endpoint.split("/")
            if seg and not re.match(r'^v\d+$', seg) and seg not in ("api", "rest")
        ]
        if not path_segments:
            return []

        cursor = self._cursor()
        if cursor is None:
            return []

        try:
            # Find files that import or call into controller/API nodes matching the path.
            # Strategy: join edges (CALLS) from ts/js source nodes to target nodes that
            # match the endpoint segments — these are the client files.
            conditions = " OR ".join(
                f"(target.qualified_name ILIKE %s OR target.name ILIKE %s)"
                for _ in path_segments
            )
            params: list = [self._workspace_id, self._workspace_id]
            for seg in path_segments:
                params += [f"%{seg}%", f"%{seg}%"]

            cursor.execute(f"""
                SELECT DISTINCT caller.file_path
                FROM edges e
                JOIN nodes caller ON caller.id = e.source_id
                JOIN nodes target ON target.id = e.target_id
                WHERE e.workspace_id = %s::UUID
                  AND caller.workspace_id = %s::UUID
                  AND e.edge_type IN ('CALLS', 'IMPORTS_FROM')
                  AND e.is_pruned = false
                  AND (caller.file_path ILIKE '%.ts'
                       OR caller.file_path ILIKE '%.tsx'
                       OR caller.file_path ILIKE '%.js'
                       OR caller.file_path ILIKE '%.jsx')
                  AND ({conditions})
                ORDER BY caller.file_path
                LIMIT %s
            """, params + [limit])

            rows = cursor.fetchall()
            results = [row[0] for row in rows if row[0]]
            log.debug(
                "find_api_caller_candidates: endpoint=%s candidates=%d",
                endpoint, len(results),
            )
            return results

        except Exception as exc:
            log.warning("find_api_caller_candidates query failed: %s", exc)
            return []
        finally:
            cursor.close()

    def get_import_targets(self, file_path: str) -> list[str]:
        """Return the qualified_names of files imported by *file_path*.

        Replaces _TS_IMPORT_RE and _PY_IMPORT_RE regex extraction with a
        direct query against IMPORTS_FROM edges in the structural index.

        Returns [] if the file is not yet in the index (caller uses regex fallback).

        Args:
            file_path: Relative path of the importing file, e.g. "src/api/client.ts".
        """
        cursor = self._cursor()
        if cursor is None:
            return []

        try:
            cursor.execute("""
                SELECT DISTINCT target.file_path
                FROM edges e
                JOIN nodes source ON source.id = e.source_id
                JOIN nodes target ON target.id = e.target_id
                WHERE e.workspace_id = %s::UUID
                  AND source.workspace_id = %s::UUID
                  AND e.edge_type = 'IMPORTS_FROM'
                  AND e.is_pruned = false
                  AND (source.file_path = %s OR source.file_path ILIKE %s)
                ORDER BY target.file_path
            """, [
                self._workspace_id, self._workspace_id,
                file_path, f"%{file_path}",
            ])

            rows = cursor.fetchall()
            return [row[0] for row in rows if row[0]]

        except Exception as exc:
            log.warning("get_import_targets query failed: %s", exc)
            return []
        finally:
            cursor.close()

    # ── Internals ──────────────────────────────────────────────────────────────

    def _cursor(self):
        """Return a fresh cursor, connecting lazily. Returns None on failure."""
        try:
            import psycopg2
            if self._conn is None or self._conn.closed:
                self._conn = psycopg2.connect(self._db_url)
            return self._conn.cursor()
        except Exception as exc:
            log.debug("StructuralIndexHelper: cannot connect to DB: %s", exc)
            return None

    @staticmethod
    def _lang_filter(language: str) -> Optional[str]:
        """Return a LIKE pattern for the file_path column, or None."""
        _MAP = {
            "java":       "%.java",
            "python":     "%.py",
            "typescript": "%.ts",
            "javascript": "%.js",
        }
        return _MAP.get(language.lower())

    def close(self) -> None:
        """Close the underlying DB connection if open."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
