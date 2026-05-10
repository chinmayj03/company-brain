"""
Neo4jWriter — async dual-write surface for LLM-extracted entities.

Mirrors the TypeScript GraphClient (packages/graph/src/client.ts) using the
same URN scheme and node/edge envelope structure. All writes are scoped to a
workspace_id (= URN scope segment).

URN format (ADR-0013 canonical): urn:cb:{tenant}:code:{repo}:{entity_type}:{qname}

Design rules (matching the TypeScript client):
  - Nodes are NEVER deleted — only soft-invalidated (valid_to_commit set).
  - Every upsert is idempotent: MERGE on the `id` property.
  - This writer NEVER raises — errors are logged and swallowed so that a Neo4j
    outage cannot crash the Postgres write path.
  - Connection pooling via the neo4j-driver async pool (max 50 connections).
  - Exponential-backoff retry on transient errors (ServiceUnavailable /
    SessionExpired) up to MAX_RETRIES attempts.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Any, Optional

import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver, exceptions as neo4j_exc

from companybrain.models.entities import (
    BusinessContext,
    ExtractedEntity,
    ExtractedRelationship,
)
from companybrain.store.identity import (
    to_urn,
    workspace_slug_for,
    NODE_TYPE_TAXONOMY,
    DEFAULT_DOMAIN,
    RepoUnknownForUrn,
)

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

NEO4J_URI_DEFAULT = "bolt://neo4j:7687"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5   # seconds
BATCH_SIZE = 100

# Python entity_type → CB node label (matches TypeScript CB node types)
_ENTITY_LABEL_MAP: dict[str, str] = {
    "class":       "Class",
    "function":    "Function",
    "method":      "Method",
    "module":      "Module",
    "interface":   "Interface",
    "enum":        "Enum",
    # Pass-through for types already in title-case
    "Class":       "Class",
    "Function":    "Function",
    "Method":      "Method",
    "Module":      "Module",
    "Interface":   "Interface",
    "Enum":        "Enum",
    # Extended types from the pipeline
    "apiendpoint":    "Function",
    "ApiEndpoint":    "Function",
    "schemafield":    "Module",
    "SchemaField":    "Module",
    "databasecolumn": "Module",
    "DatabaseColumn": "Module",
    "databasequery":  "Function",
    "DatabaseQuery":  "Function",
    "interfacemethod": "Function",
    "InterfaceMethod": "Function",
}

# Python edge_type → CB relationship type. Lowercased and normalised so
# Cypher edge labels are consistent and so the LLM's case sensitivity doesn't
# multiply edge labels for the same relationship (CALLS vs calls).
#
# Some types collapse to a canonical name to keep the graph readable:
#   - column-level reads/writes collapse to reads/writes
#   - RENDERS_FIELD collapses to reads (frontend reads a field)
#   - INVOKES collapses to calls
#   - DEPENDS_ON collapses to uses
# Most types stay distinct so dependency / impact queries remain precise.
def _norm(name: str) -> str:
    """Normalise an edge type to lower-snake-case."""
    return name.strip().lower().replace("-", "_")


_CANONICAL_EDGE_TYPES: dict[str, str] = {
    # structure / inheritance
    "extends":          "extends",
    "implements":       "implements",
    "overrides":        "overrides",
    "contains":         "contains",
    "annotates":        "annotates",
    "imports":          "imports",
    # behavior / call flow
    "calls":            "calls",
    "invokes":          "calls",        # synonym
    "awaits":           "awaits",
    "calls_endpoint":   "calls_endpoint",
    "delegates_to":     "delegates_to",
    "instantiates":     "instantiates",
    "uses":             "uses",
    "depends_on":       "uses",         # synonym
    # data flow
    "reads_column":     "reads_column",
    "writes_column":    "writes_column",
    "reads_field":      "reads_field",
    "writes_field":     "writes_field",
    "reads":            "reads_column", # legacy alias
    "writes":           "writes_column",# legacy alias
    "renders_field":    "renders_field",
    "returns":          "returns",
    "accepts_param":    "accepts_param",
    "transforms":       "transforms",
    "serializes_to":    "serializes_to",
    # persistence
    "persists_to":      "persists_to",
    "cached_by":        "cached_by",
    "indexed_by":       "indexed_by",
    "constrained_by":   "constrained_by",
    # validation
    "validates":        "validates",
    "enforces":         "enforces",
    "sanitizes":        "sanitizes",
    # error flow
    "throws":           "throws",
    "catches":          "catches",
    "wraps_exception":  "wraps_exception",
    "handles_error":    "handles_error",
    # ui
    "renders":          "renders",
    "binds_to":         "binds_to",
    "routed_by":        "routed_by",
    "listens_to":       "listens_to",
    # authz / security
    "authorized_by":    "authorized_by",
    "protected_by":     "protected_by",
    "audited_by":       "audited_by",
    # async / eventing
    "publishes_to":     "publishes_to",
    "subscribes_to":    "subscribes_to",
    "scheduled_by":     "scheduled_by",
    # observability
    "logs_to":          "logs_to",
    "emits_metric":     "emits_metric",
    "traced_by":        "traced_by",
    # testing
    "tested_by":        "tested_by",
    "mocks":            "mocks",
    "fixture_for":      "fixture_for",
    # config / lifecycle
    "configured_by":    "configured_by",
    "initialized_by":   "initialized_by",
    "rate_limited_by":  "rate_limited_by",
    # assumption / dependency (assumption_miner static extractor)
    "relies_on":        "relies_on",
}


class _EdgeTypeMap:
    """Case-insensitive lookup that delegates to _CANONICAL_EDGE_TYPES."""
    def get(self, key: str, default: str = "") -> str:
        return _CANONICAL_EDGE_TYPES.get(_norm(key), default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and _norm(key) in _CANONICAL_EDGE_TYPES

    def __getitem__(self, key: str) -> str:
        return _CANONICAL_EDGE_TYPES[_norm(key)]


_EDGE_TYPE_MAP = _EdgeTypeMap()

# Confidence label → numeric score (mirrors builder.py)
_CONFIDENCE_SCORE: dict[str, float] = {
    "high":   1.0,
    "medium": 0.7,
    "low":    0.4,
}

# Allowed chars in URN segments (mirrors urn.ts ALLOWED_CHARS)
_URN_ALLOWED = re.compile(r"^[A-Za-z0-9/_.:@\-]+$")


# ── URN helpers ───────────────────────────────────────────────────────────────

def _sanitize_urn_segment(segment: str) -> str:
    """Replace characters not allowed in URN segments with underscores."""
    return re.sub(r"[^A-Za-z0-9/_.:@\-]", "_", segment)


def build_llm_urn(workspace_id: str, file_path: str, entity_name: Optional[str] = None) -> str:
    """
    Build a Company Brain URN for an LLM-extracted entity.

    Format: urn:cb:llm:{workspace_id}:{file_path}[:{entity_name}]

    Args:
        workspace_id: Workspace identifier used as the URN scope.
        file_path:    Relative path to the source file (artifact segment).
        entity_name:  Qualified entity name within the file (symbol segment).
                      Omit for file-level nodes.

    Returns:
        A validated URN string of up to 512 characters.
    """
    scope    = _sanitize_urn_segment(workspace_id)
    artifact = _sanitize_urn_segment(file_path)
    base     = f"urn:cb:llm:{scope}:{artifact}"

    if entity_name:
        symbol = _sanitize_urn_segment(entity_name)
        urn = f"{base}:{symbol}"
    else:
        urn = base

    if len(urn) > 512:
        # Hash the tail to fit within the 512-char limit
        digest = hashlib.md5(urn.encode()).hexdigest()[:16]
        urn = urn[:490] + f"...{digest}"

    return urn


# ── Main class ────────────────────────────────────────────────────────────────

class Neo4jWriter:
    """
    Async Neo4j writer scoped to a single workspace.

    Lifecycle::

        writer = Neo4jWriter(workspace_id="acme/web")
        await writer.connect()
        ...
        await writer.close()

    Or use as an async context manager::

        async with Neo4jWriter(workspace_id="acme/web") as writer:
            await writer.upsert_entities(entities)
    """

    def __init__(
        self,
        workspace_id: str,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        database: str = "neo4j",
    ) -> None:
        """
        Args:
            workspace_id: Workspace identifier (= URN scope segment).
            uri:          Neo4j Bolt URI. Falls back to NEO4J_URI env var,
                          then to bolt://neo4j:7687.
            username:     Neo4j username. Falls back to NEO4J_USERNAME env var,
                          then to "neo4j".
            password:     Neo4j password. Falls back to NEO4J_PASSWORD env var,
                          then to "neo4j".
            database:     Target database name (default "neo4j").
        """
        self.workspace_id = workspace_id
        self._uri      = uri      or os.environ.get("NEO4J_URI",      NEO4J_URI_DEFAULT)
        self._username = username or os.environ.get("NEO4J_USERNAME",  "neo4j")
        self._password = password or os.environ.get("NEO4J_PASSWORD",  "neo4j")
        self._database = database
        self._driver: Optional[AsyncDriver] = None
        # Name → canonical URN map populated in upsert_entities. Used by
        # _external_id_to_urn so an edge whose to_entity is just a bare name
        # (very common — that's what the LLM tends to emit) resolves to the
        # SAME URN the corresponding node was stored under, instead of the
        # bogus 'monorepo' fallback that orphans every edge in Neo4j.
        self._name_to_urn: dict[str, str] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the driver and verify connectivity. Safe to call multiple times."""
        if self._driver is not None:
            return
        self._driver = AsyncGraphDatabase.driver(
            self._uri,
            auth=(self._username, self._password),
            max_connection_pool_size=50,
            connection_acquisition_timeout=10.0,
        )
        try:
            await self._driver.verify_connectivity()
            await self._ensure_constraints()
            log.info("Neo4j connected", uri=self._uri, workspace=self.workspace_id)
        except Exception as exc:
            log.error("Neo4j connectivity check failed", error=str(exc), uri=self._uri)
            # Driver stays open — queries will fail and be swallowed individually.

    async def close(self) -> None:
        """Close the driver and release all connections."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            log.info("Neo4j driver closed", workspace=self.workspace_id)

    async def __aenter__(self) -> "Neo4jWriter":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Schema constraints ────────────────────────────────────────────────────

    async def _ensure_constraints(self) -> None:
        """
        Idempotently create the uniqueness constraint and indexes required by
        the CB graph schema. Mirrors _ensureConstraints() in client.ts.
        """
        statements = [
            # Node uniqueness (mirrors cb_node_id in TypeScript)
            (
                "CREATE CONSTRAINT cb_node_id IF NOT EXISTS "
                "FOR (n:CBNode) REQUIRE n.id IS UNIQUE"
            ),
            # Indexes for fast scoped lookups
            "CREATE INDEX cb_node_type  IF NOT EXISTS FOR (n:CBNode) ON (n.type)",
            "CREATE INDEX cb_node_scope IF NOT EXISTS FOR (n:CBNode) ON (n.scope)",
        ]
        async with self._session() as session:
            for stmt in statements:
                try:
                    await session.run(stmt)
                except Exception as exc:
                    log.warning("Constraint/index creation skipped", stmt=stmt[:60], error=str(exc))

    # ── Public write methods ──────────────────────────────────────────────────

    async def upsert_entities(self, entities: list[ExtractedEntity]) -> None:
        """
        Upsert a list of LLM-extracted entities as CBNode nodes in Neo4j.

        Each entity gets:
          - Label :CBNode (always)
          - Label :<NodeType>  e.g. :Function, :Class
          - id  = URN  (urn:cb:llm:{workspace_id}:{file}:{name})
          - All provenance fields as top-level properties

        Args:
            entities: Entities from LLM Pass 1.
        """
        if not entities:
            return

        if self._driver is None:
            # Don't pretend the write happened — misleading success logs cost an
            # afternoon today.
            log.error(
                "Neo4j upsert_entities called before connect() — refusing to lie about success",
                count=len(entities),
                workspace=self.workspace_id,
            )
            return

        # Build rows; skip entities whose repo is missing so we never write a
        # monorepo-URN node that would orphan every edge referencing that node.
        rows: list[dict[str, Any]] = []
        valid_entities: list[ExtractedEntity] = []
        skipped = 0
        for e in entities:
            try:
                rows.append(self._entity_to_row(e))
                valid_entities.append(e)
            except RepoUnknownForUrn:
                skipped += 1
        if skipped:
            log.warning(
                "Neo4j upsert_entities: skipped entities with missing repo",
                skipped=skipped,
                total=len(entities),
            )

        # Populate the bare-name → canonical URN index so subsequent edge
        # writes can resolve LLM-emitted bare names to the right node URN.
        # First-wins so duplicate names (rare) don't churn the entry.
        for e, row in zip(valid_entities, rows):
            if e.name and row.get("id"):
                self._name_to_urn.setdefault(e.name, row["id"])
                # Also register qualified forms the LLM sometimes emits.
                if "." in e.name:
                    self._name_to_urn.setdefault(e.name.split(".", 1)[-1], row["id"])
        # Process in batches of BATCH_SIZE; track which actually succeeded so the
        # success log doesn't lie when every batch errored (e.g. DNS fail to neo4j:7687).
        succeeded = 0
        failed    = 0
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            result = await self._run_with_retry(
                self._upsert_node_batch,
                batch,
                context=f"upsert_entities batch {i // BATCH_SIZE}",
            )
            # _run_with_retry returns None on failure, otherwise the inner result.
            if result is None:
                failed += len(batch)
            else:
                succeeded += len(batch)

        if failed and not succeeded:
            log.error(
                "Neo4j upsert_entities — ALL batches failed",
                attempted=len(entities),
                workspace=self.workspace_id,
            )
        else:
            log.info(
                "Neo4j upserted entities",
                attempted=len(entities),
                succeeded=succeeded,
                failed=failed,
                workspace=self.workspace_id,
            )

    async def upsert_relationships(
        self, relationships: list[ExtractedRelationship]
    ) -> None:
        """
        Upsert a list of LLM-extracted relationships as typed edges in Neo4j.

        Both source and target nodes must already exist (MATCH, not MERGE) —
        skips silently if either end is absent.

        Args:
            relationships: Relationships from LLM Pass 2.
        """
        if not relationships:
            return

        if self._driver is None:
            log.error(
                "Neo4j upsert_relationships called before connect() — refusing to lie about success",
                count=len(relationships),
                workspace=self.workspace_id,
            )
            return

        # Group by canonical edge type for UNWIND efficiency (mirrors TypeScript
        # upsertEdges which groups by type before issuing dynamic MERGE queries).
        # Rows whose URN resolution failed return None and are counted as dropped.
        by_type: dict[str, list[dict[str, Any]]] = {}
        dropped = 0
        for rel in relationships:
            cb_type = _EDGE_TYPE_MAP.get(rel.edge_type, rel.edge_type.lower())
            row     = self._rel_to_row(rel, cb_type)
            if row is None:
                dropped += 1
                continue
            by_type.setdefault(cb_type, []).append(row)
        if dropped:
            log.warning(
                "Neo4j upsert_relationships: dropped edges with unresolvable URNs",
                dropped=dropped,
                total=len(relationships),
            )

        succeeded = 0
        failed    = 0
        for cb_type, rows in by_type.items():
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i : i + BATCH_SIZE]
                result = await self._run_with_retry(
                    self._upsert_edge_batch,
                    cb_type,
                    batch,
                    context=f"upsert_relationships[{cb_type}] batch {i // BATCH_SIZE}",
                )
                if result is None:
                    failed += len(batch)
                else:
                    succeeded += len(batch)

        if failed and not succeeded:
            log.error(
                "Neo4j upsert_relationships — ALL batches failed",
                attempted=len(relationships),
                workspace=self.workspace_id,
            )
        else:
            log.info(
                "Neo4j upserted relationships",
                attempted=len(relationships),
                succeeded=succeeded,
                failed=failed,
                workspace=self.workspace_id,
            )

    async def upsert_context(
        self, entity_external_id: str, context: BusinessContext
    ) -> None:
        """
        Upsert a BusinessContext node linked to the given entity.

        Creates a :BusinessContext node (label :CBNode + :BusinessContext) and
        a HAS_CONTEXT relationship from the entity node to the context node.

        Args:
            entity_external_id: The entity's external_id (repo/file::name).
            context:            BusinessContext from LLM Pass 3.
        """
        # Derive the entity URN from its external_id using canonical form.
        entity_urn = self._external_id_to_urn(entity_external_id)

        context_urn = f"{entity_urn}:_context"
        confidence  = _CONFIDENCE_SCORE.get(context.source_confidence, 0.5)

        props: dict[str, Any] = {
            "id":                  context_urn,
            "type":                "BusinessContext",
            "scope":               self.workspace_id,
            "source":              "llm",
            "entity_external_id":  entity_external_id,
            "purpose":             context.purpose or "",
            "history_summary":     context.history_summary or "",
            "invariants":          context.invariants,
            "change_risk":         context.change_risk or "",
            "change_risk_reason":  context.change_risk_reason or "",
            "source_confidence":   context.source_confidence or "",
            "confidence":          confidence,
            "owner_team":          context.owner_team or "",
            "external_dependencies": context.external_dependencies,
            "gaps":                context.gaps,
            "valid_to_commit":     None,
            "status":              "active",
        }

        await self._run_with_retry(
            self._upsert_context_node,
            entity_urn,
            props,
            context=f"upsert_context:{entity_external_id}",
        )

    async def invalidate_stale(
        self,
        file_path: str,
        new_hash: str,
        commit_sha: str = "unknown",
        *,
        repo: str = "",
    ) -> int:
        """
        Soft-delete all nodes for *file_path* whose source_checksum differs from
        *new_hash* and have not already been invalidated.

        Sets valid_to_commit = commit_sha and status = "removed" on matching nodes.
        The nodes are preserved — never deleted (mirrors TypeScript invalidateByPrefix).

        Args:
            file_path:   Relative file path (used to match source_uri).
            new_hash:    MD5 of the current file content at extraction time.
            commit_sha:  The commit SHA that triggered re-extraction.
            repo:        Real repository name (required — no monorepo default).

        Returns:
            Number of nodes invalidated.
        """
        if not repo:
            log.warning(
                "invalidate_stale called without repo — matching all repos for tenant scope",
                file_path=file_path,
            )
        tenant = workspace_slug_for(self.workspace_id)
        # Use the real repo when available; fall back to the tenant prefix only
        # so we still invalidate nodes even when repo is not threaded through.
        if repo:
            urn_prefix = f"urn:cb:{tenant}:{DEFAULT_DOMAIN}:{repo}:"
        else:
            urn_prefix = f"urn:cb:{tenant}:{DEFAULT_DOMAIN}:"
        count      = await self._run_with_retry(
            self._do_invalidate,
            urn_prefix,
            new_hash,
            commit_sha,
            context=f"invalidate_stale:{file_path}",
        )
        if count:
            log.info(
                "Neo4j invalidated stale nodes",
                file_path=file_path,
                count=count,
                commit=commit_sha,
                workspace=self.workspace_id,
            )
        return count or 0

    # ── Internal Cypher execution ─────────────────────────────────────────────

    async def _upsert_node_batch(self, rows: list[dict[str, Any]]) -> None:
        """UNWIND batch of node rows into MERGE statements.

        The previous version used apoc.create.addLabels() to attach a dynamic
        :NodeType label (Function, Class, etc.) but APOC is not installed in
        the default Neo4j docker image — every batch failed with "Unknown
        procedure apoc.create.addLabels". Switched to plain Cypher and rely
        on the n.type property + :CBNode label, which is what all our queries
        already filter on. No functional loss, no APOC dependency.

        Property cleanup: Neo4j only accepts primitives, strings, booleans,
        and arrays-of-primitives as property values. Pass-through dicts /
        lists-of-dicts / None values cause "Property values can only be
        of primitive types or arrays thereof" and abort the entire batch.
        We strip Nones and stringify any non-primitive nested values up front.
        """

        def _clean_props(props: dict) -> dict:
            cleaned = {}
            for k, v in props.items():
                if v is None:
                    continue
                if isinstance(v, (str, bool, int, float)):
                    cleaned[k] = v
                elif isinstance(v, list):
                    # Keep lists of primitives; flatten any stray nested objects to strings.
                    safe = [
                        x if isinstance(x, (str, bool, int, float)) else str(x)
                        for x in v if x is not None
                    ]
                    cleaned[k] = safe
                else:
                    # dict, set, custom obj — cast to string so the property survives.
                    cleaned[k] = str(v)
            return cleaned

        clean_rows = [{**r, "props": _clean_props(r.get("props", {}))} for r in rows]

        # Entry log so we know this function actually fires (helps distinguish
        # 'rows never reached cypher' from 'cypher failed silently').
        log.info(
            "Neo4j _upsert_node_batch ENTER",
            rows=len(clean_rows),
            sample_id=(clean_rows[0].get("id") if clean_rows else None),
        )

        cypher = """
UNWIND $rows AS row
MERGE (n:CBNode { id: row.id })
SET n += row.props,
    n.type  = row.node_type,
    n.scope = $scope
RETURN count(n) AS c
"""
        try:
            async with self._session() as session:
                result = await session.run(cypher, rows=clean_rows, scope=self.workspace_id)
                # Force materialisation of any error from the server (lazy cursors
                # can swallow errors until consumed).
                summary = await result.consume()
                log.info(
                    "Neo4j _upsert_node_batch OK",
                    rows=len(clean_rows),
                    nodes_created=summary.counters.nodes_created if summary else 0,
                    props_set=summary.counters.properties_set if summary else 0,
                )
                # Return a truthy value so _run_with_retry's `result is None`
                # check (in upsert_entities) treats this as success. Without
                # an explicit return the batch counts as failed even though
                # the cypher succeeded.
                return len(clean_rows)
        except Exception as exc:
            sample = clean_rows[0] if clean_rows else {}
            sample_keys = list((sample.get("props") or {}).keys())
            log.error(
                "Neo4j upsert_node_batch failed",
                error=str(exc),
                error_type=type(exc).__name__,
                rows=len(clean_rows),
                sample_id=sample.get("id"),
                sample_prop_keys=sample_keys[:25],
            )
            raise

    async def _upsert_edge_batch(
        self, cb_type: str, rows: list[dict[str, Any]]
    ) -> None:
        """UNWIND batch of edge rows into MERGE statements for a single edge type."""

        def _clean_props(props: dict) -> dict:
            cleaned = {}
            for k, v in props.items():
                if v is None:
                    continue
                if isinstance(v, (str, bool, int, float)):
                    cleaned[k] = v
                elif isinstance(v, list):
                    cleaned[k] = [
                        x if isinstance(x, (str, bool, int, float)) else str(x)
                        for x in v if x is not None
                    ]
                else:
                    cleaned[k] = str(v)
            return cleaned

        clean_rows = [{**r, "props": _clean_props(r.get("props", {}))} for r in rows]

        # Dynamic relationship type — use backtick quoting (mirrors TypeScript)
        cypher = f"""
UNWIND $rows AS row
MATCH (src:CBNode {{ id: row.source_id }})
MATCH (tgt:CBNode {{ id: row.target_id }})
MERGE (src)-[r:`{cb_type}` {{ id: row.edge_id }}]->(tgt)
SET r += row.props
RETURN count(r) AS c
"""
        try:
            async with self._session() as session:
                result = await session.run(cypher, rows=clean_rows)
                await result.consume()
                # Truthy return so the per-batch counter in upsert_relationships
                # treats this as success.
                return len(clean_rows)
        except Exception as exc:
            log.error(
                "Neo4j upsert_edge_batch failed",
                cb_type=cb_type,
                error=str(exc),
                error_type=type(exc).__name__,
                rows=len(clean_rows),
                sample=clean_rows[0] if clean_rows else None,
            )
            raise

    async def _upsert_context_node(
        self,
        entity_urn: str,
        props: dict[str, Any],
    ) -> None:
        """Upsert a BusinessContext node and attach it to the entity node.

        Standard Neo4j Docker images don't ship APOC, so the previous
        apoc.create.addLabels call would 500. Use a static dual-label MERGE
        and rely on the type=BusinessContext property for queries that
        need to distinguish context nodes from regular entity nodes.
        """
        # Force a type marker on the props so MATCH (n:CBNode {type:'BusinessContext'})
        # works as a substitute for the missing :BusinessContext label.
        props = {**props, "type": "BusinessContext"}
        cypher = """
MERGE (ctx:CBNode { id: $id })
SET ctx += $props
WITH ctx
MATCH (entity:CBNode { id: $entity_urn })
MERGE (entity)-[:HAS_CONTEXT]->(ctx)
RETURN ctx
"""
        async with self._session() as session:
            await session.run(
                cypher,
                id=props["id"],
                props=props,
                entity_urn=entity_urn,
            )

    async def _do_invalidate(
        self,
        urn_prefix: str,
        new_hash: str,
        commit_sha: str,
    ) -> int:
        """Execute the soft-delete Cypher and return the count of updated nodes."""
        cypher = """
MATCH (n:CBNode)
WHERE n.id STARTS WITH $prefix
  AND n.source_checksum <> $new_hash
  AND n.valid_to_commit IS NULL
SET n.valid_to_commit = $commit_sha,
    n.status = 'removed'
RETURN count(n) AS c
"""
        async with self._session() as session:
            result = await session.run(
                cypher,
                prefix=urn_prefix,
                new_hash=new_hash,
                commit_sha=commit_sha,
            )
            record = await result.single()
            return int(record["c"]) if record else 0

    # ── Row builders ──────────────────────────────────────────────────────────

    def _entity_to_row(self, entity: ExtractedEntity) -> dict[str, Any]:
        """Convert an ExtractedEntity to a Neo4j UNWIND row dict."""
        tenant     = workspace_slug_for(self.workspace_id)
        repo       = entity.repo
        if not repo:
            log.error(
                "Neo4j _entity_to_row: entity missing repo — skipping to avoid monorepo URN",
                entity_name=entity.name,
                entity_type=entity.entity_type,
                file=entity.file,
            )
            raise RepoUnknownForUrn(
                f"entity {entity.name!r} has no repo set; cannot build a valid URN"
            )
        etype      = NODE_TYPE_TAXONOMY.get(entity.entity_type, "component")
        urn        = to_urn(
            tenant=tenant, domain=DEFAULT_DOMAIN, repo=repo,
            entity_type=etype, qualified_name=entity.name,
        )
        node_label = _ENTITY_LABEL_MAP.get(entity.entity_type, "Function")

        props: dict[str, Any] = {
            "id":                   urn,
            "type":                 node_label,
            "scope":                self.workspace_id,
            "source":               "llm",
            "name":                 entity.name,
            "entity_type":          entity.entity_type,
            "file":                 entity.file,
            "repo":                 entity.repo,
            "signature":            entity.signature or "",
            "confidence":           entity.confidence,
            "source_uri":           f"{entity.repo}/{entity.file}",
            "source_checksum":      "",   # populated by caller via upsert_entities
            "valid_from_commit":    entity.first_appeared_commit or "",
            "last_modified_commit": entity.last_modified_commit or "",
            "valid_to_commit":      None,
            "status":               "active",
            # ── Plain-text summary for LLM retrieval ─────────────────────────
            # A human-readable sentence describing what this node is.
            # Stored so graph queries can surface semantic context without a
            # secondary LLM call at read time. Used by MCP tools and the
            # query engine when building prompt context.
            "text_summary":         _build_node_summary(entity),
        }

        # Structural-hints enrichment (optional fields from ADR-006)
        if entity.structural_purpose:
            props["structural_purpose"] = entity.structural_purpose
        if entity.structural_change_risk:
            props["structural_change_risk"] = entity.structural_change_risk
        if entity.structural_risk_flags:
            props["structural_risk_flags"] = entity.structural_risk_flags
        if entity.structural_data_reads:
            props["structural_data_reads"] = entity.structural_data_reads
        if entity.structural_data_writes:
            props["structural_data_writes"] = entity.structural_data_writes

        return {
            "id":         urn,
            "node_type":  node_label,
            "node_label": node_label,
            "props":      props,
        }

    def _rel_to_row(
        self, rel: ExtractedRelationship, cb_type: str
    ) -> dict[str, Any] | None:
        """Convert an ExtractedRelationship to a Neo4j UNWIND row dict.

        Returns None when either endpoint's URN cannot be resolved (e.g. the
        entity had no repo). Callers must filter out None rows.
        """
        # Derive URNs from external_ids (repo/file::name)
        source_urn = self._external_id_to_urn(rel.from_entity)
        target_urn = self._external_id_to_urn(rel.to_entity)
        if source_urn is None or target_urn is None:
            log.warning(
                "Dropping edge — unresolvable URN for endpoint",
                from_entity=rel.from_entity,
                to_entity=rel.to_entity,
                source_urn=source_urn,
                target_urn=target_urn,
            )
            return None

        # Edge id = deterministic hash of (source, type, target) — idempotent
        edge_id = "urn:cb:edge:" + hashlib.md5(
            f"{source_urn}|{cb_type}|{target_urn}".encode()
        ).hexdigest()

        props: dict[str, Any] = {
            "id":          edge_id,
            "type":        cb_type,
            "source_id":   source_urn,
            "target_id":   target_urn,
            "confidence":  rel.confidence,
            "evidence":    rel.evidence or "",
            "from_type":   rel.from_type or "",
            "to_type":     rel.to_type or "",
            "scope":       self.workspace_id,
            "source":      "llm",
            # ── Plain-text edge label for LLM retrieval ───────────────────────
            # A readable sentence stating what this edge means. Lets the query
            # engine include relationship context in prompts without re-parsing
            # structured fields.
            "label":       _build_edge_label(rel, cb_type),
        }

        return {
            "edge_id":   edge_id,
            "source_id": source_urn,
            "target_id": target_urn,
            "props":     props,
        }

    def _external_id_to_urn(self, external_id: str) -> str:
        """
        Convert a pipeline external_id to a canonical URN, preserving real repo
        + type information so the resulting URN matches the corresponding node.

        Accepted input shapes:
          - already a URN  ("urn:cb:...")  → returned unchanged
          - "{repo}/{file}::{name}"        → URN with that repo
          - bare name                      → fallback to "monorepo" / "component"

        Critical bug it fixes: previously this method always emitted
        urn:cb:{tenant}:{domain}:monorepo:component:{name} regardless of the
        entity's actual repo/type. Nodes were stored under
        urn:cb:{tenant}:{domain}:network-iq-backend-java:component:{name}, so
        MATCH (src {id: $edge_source_urn}) silently found nothing — every
        edge was orphaned in Neo4j and blast-radius returned 0 hops.
        """
        # Already a canonical URN? Return as-is so node/edge URNs line up.
        if external_id.startswith("urn:"):
            return external_id

        # Bare-name lookup: if the name was registered during upsert_entities
        # we know the canonical URN exactly. This is the path that fixes
        # 'every edge orphaned in Neo4j' for relationships whose to_entity
        # is the LLM's bare name (most relationships hit this path).
        if external_id in self._name_to_urn:
            return self._name_to_urn[external_id]
        # Sometimes the LLM qualifies the name (Class.method) or vice-versa.
        if "." in external_id:
            tail = external_id.split(".", 1)[-1]
            if tail in self._name_to_urn:
                return self._name_to_urn[tail]

        tenant = workspace_slug_for(self.workspace_id)
        parts  = external_id.split("::", 1)
        if len(parts) == 2:
            # "{repo}/{file_or_path}::{name}" — use the real repo segment so
            # the URN matches what _entity_to_row produced for the node.
            repo_and_path = parts[0]
            name_part     = parts[1]
            real_repo     = repo_and_path.split("/", 1)[0] or "monorepo"
            # Try the index again now that we've parsed out the bare name.
            if name_part in self._name_to_urn:
                return self._name_to_urn[name_part]
            return to_urn(
                tenant=tenant, domain=DEFAULT_DOMAIN, repo=real_repo,
                entity_type="component", qualified_name=name_part,
            )
        # No match found — log the offending id and return None so the caller
        # can drop the edge rather than silently orphaning it with a monorepo URN.
        log.warning(
            "Edge URN resolution failed — dropping edge (no repo known for id)",
            external_id=external_id,
            workspace=self.workspace_id,
        )
        return None  # type: ignore[return-value]

    # ── Retry logic ───────────────────────────────────────────────────────────

    async def _run_with_retry(
        self,
        fn: Any,
        *args: Any,
        context: str = "",
    ) -> Any:
        """
        Call *fn(*args)* with exponential-backoff retry on transient Neo4j errors.

        Never raises — logs the final failure and returns None.
        Transient errors retried: ServiceUnavailable, SessionExpired.
        Non-transient errors (auth failures, bad Cypher): logged once, no retry.
        """
        if self._driver is None:
            # Self-heal: try to connect once. connect() is idempotent.
            try:
                await self.connect()
            except Exception as exc:
                log.error(
                    "Neo4j writer not connected and auto-connect() failed",
                    context=context,
                    workspace=self.workspace_id,
                    error=str(exc),
                )
                return None
            if self._driver is None:
                log.error(
                    "Neo4j writer not connected after auto-connect()",
                    context=context,
                    workspace=self.workspace_id,
                )
                return None

        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await fn(*args)
            except (
                neo4j_exc.ServiceUnavailable,
                neo4j_exc.SessionExpired,
            ) as exc:
                last_exc = exc
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "Neo4j transient error — retrying",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    delay=delay,
                    error=str(exc),
                    context=context,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                # Non-transient: log and bail immediately
                log.error(
                    "Neo4j write failed (non-transient)",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    context=context,
                    workspace=self.workspace_id,
                )
                return None

        log.error(
            "Neo4j write failed after retries",
            attempts=MAX_RETRIES,
            error=str(last_exc),
            context=context,
            workspace=self.workspace_id,
        )
        return None

    # ── Session factory ───────────────────────────────────────────────────────

    def _session(self):  # type: ignore[return]
        """Return an async Neo4j session bound to the configured database."""
        if self._driver is None:
            raise RuntimeError("Neo4jWriter not connected — call connect() first")
        return self._driver.session(database=self._database)


# ── Plain-text summary helpers ────────────────────────────────────────────────
# These produce human-readable sentences stored directly on nodes and edges.
# The goal is that any LLM querying the graph can retrieve rich semantic context
# from a single node/edge lookup without needing a second inference call.

def _build_node_summary(entity: "ExtractedEntity") -> str:  # noqa: F821
    """
    Build a one-sentence plain-text description of a graph node.

    Priority order for description text:
      1. structural_purpose  (set by the context-manager agent)
      2. signature           (method signature — readable even without prose)
      3. Fallback template   (entity_type + name + file)

    Examples:
      "Function getPayerCompetitors in CompetitivenessController — returns a
       paginated list of competitor payers filtered by LOB and state."
      "DatabaseQuery getPayerCompetitors_query — SELECT payer_name, market_share
       FROM competitors WHERE lob = ? AND state IN (?)"
      "Class DefaultCompetitivenessService in application/competitiveness."
    """
    etype = entity.entity_type
    name  = entity.name
    file  = entity.file.split("/")[-1].replace(".java", "").replace(".py", "").replace(".ts", "")

    # Use LLM-generated purpose when available
    purpose = getattr(entity, "structural_purpose", None) or ""
    sig     = entity.signature or ""

    if purpose:
        return f"{etype} {name} ({file}) — {purpose}"
    if sig:
        # For DatabaseQuery/InterfaceMethod, the signature may include SQL — include verbatim
        if etype in ("DatabaseQuery", "InterfaceMethod"):
            return f"{etype} {name}: {sig[:200]}"
        return f"{etype} {name} in {file} — signature: {sig[:150]}"
    # Minimal fallback
    return f"{etype} {name} in {file}."


_EDGE_VERB: dict[str, str] = {
    "calls":        "calls",
    "imports":      "imports",
    "extends":      "extends",
    "implements":   "implements",
    "reads":        "reads",
    "writes":       "writes",
    "CALLS":           "calls",
    "READS_COLUMN":    "reads column",
    "WRITES_COLUMN":   "writes column",
    "RENDERS_FIELD":   "renders field",
    "CALLS_ENDPOINT":  "calls endpoint",
    "VALIDATES":       "validates",
    "TESTED_BY":       "is tested by",
    "USES":            "uses",
    "DEPENDS_ON":      "depends on",
    "THROWS":          "throws",
    "INVOKES":         "invokes",
    "EXTENDS":         "extends",
    "IMPLEMENTS":      "implements",
    # Expanded taxonomy — keep human-readable verbs aligned with the prompt.
    "OVERRIDES":       "overrides",
    "CONTAINS":        "contains",
    "ANNOTATES":       "is annotated by",
    "IMPORTS":         "imports",
    "AWAITS":          "awaits",
    "DELEGATES_TO":    "delegates to",
    "INSTANTIATES":    "instantiates",
    "READS_FIELD":     "reads field",
    "WRITES_FIELD":    "writes field",
    "RETURNS":         "returns",
    "ACCEPTS_PARAM":   "accepts parameter",
    "TRANSFORMS":      "transforms to",
    "SERIALIZES_TO":   "serializes to",
    "PERSISTS_TO":     "persists to",
    "CACHED_BY":       "is cached by",
    "INDEXED_BY":      "is indexed by",
    "CONSTRAINED_BY":  "is constrained by",
    "ENFORCES":        "enforces",
    "SANITIZES":       "sanitizes",
    "CATCHES":         "catches",
    "WRAPS_EXCEPTION": "wraps exception",
    "HANDLES_ERROR":   "handles error",
    "RENDERS":         "renders",
    "BINDS_TO":        "binds to",
    "ROUTED_BY":       "is routed by",
    "LISTENS_TO":      "listens to",
    "AUTHORIZED_BY":   "is authorized by",
    "PROTECTED_BY":    "is protected by",
    "AUDITED_BY":      "is audited by",
    "PUBLISHES_TO":    "publishes to",
    "SUBSCRIBES_TO":   "subscribes to",
    "SCHEDULED_BY":    "is scheduled by",
    "LOGS_TO":         "logs to",
    "EMITS_METRIC":    "emits metric",
    "TRACED_BY":       "is traced by",
    "MOCKS":           "mocks",
    "FIXTURE_FOR":     "is a fixture for",
    "CONFIGURED_BY":   "is configured by",
    "INITIALIZED_BY":  "is initialized by",
    "RATE_LIMITED_BY": "is rate-limited by",
    "RELIES_ON":       "relies on",
}


def _build_edge_label(rel: "ExtractedRelationship", cb_type: str) -> str:  # noqa: F821
    """
    Build a plain-text label for a graph edge.

    Format: "{from_entity} {verb} {to_entity}[ — {evidence}]"

    Examples:
      "getPayerCompetitors calls DefaultCompetitivenessService — competitorService.getPayerCompetitors(lob)"
      "fetchAllCompetitors reads column competitors.payer_name — SELECT payer_name FROM competitors"
      "CompetitorTable renders field CompetitorDto.marketShare — {competitor.marketShare}"
    """
    verb     = _EDGE_VERB.get(rel.edge_type, cb_type)
    from_e   = rel.from_entity
    to_e     = rel.to_entity
    evidence = rel.evidence or ""

    label = f"{from_e} {verb} {to_e}"
    if evidence:
        label += f" — {evidence[:120]}"
    return label
