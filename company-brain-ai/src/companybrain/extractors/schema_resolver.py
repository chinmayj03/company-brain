"""
Schema cross-edges resolver — ADR-0058.

After all schema extractors have run, this resolver links the schema-side
graph to the code-side graph:

  - jOOQ field constants (``PLAN_INFO.PAYER_PLAN_ID``) → ``DatabaseColumn``
    URNs, so any ``READS_COLUMN`` edge that the relationship extractor
    emitted using the jOOQ constant name can be rewritten to point at the
    actual DB column.
  - OpenAPI operations (path + method) → Spring / Express ``ApiEndpoint``
    entities, emitting a ``DOCUMENTS`` edge when the pair matches.
  - Protobuf RPCs → Java gRPC service method implementations.
  - GraphQL fields → resolver methods (best-effort name match).

The resolver is intentionally pure — it operates on already-extracted
objects, never reads files. Its only side effect is appending edges to the
combined ``SchemaExtractedBatch`` it returns.

This module also exposes ``run_schema_extraction`` — the orchestrator-facing
entry point that walks each repo for schema-shaped files, parses them with
the per-format extractors, runs the resolver, and returns telemetry. It's
intentionally self-contained (not routed through ``extractors.dispatch``):
that way ``ConfigExtractor`` keeps claiming ``application.yml`` while we
also handle ``openapi.yaml`` without modifying ADR-0057's dispatch order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from companybrain.models.entities import (
    DatabaseColumn,
    DatabaseTable,
    EDGE_BINDS_TO_COLUMN,
    EDGE_BINDS_TO_TABLE,
    EDGE_DOCUMENTS_OPENAPI,
    EDGE_IMPLEMENTS_RPC,
    EDGE_READS_COLUMN,
    EDGE_RESOLVES,
    ExtractedEntity,
    ExtractedRelationship,
    JooqFieldBinding,
    JooqTableBinding,
    OpenAPIOperation,
    ProtoRpc,
    GraphQLField,
    SchemaEdge,
    SchemaExtractedBatch,
)


@dataclass
class SchemaIndex:
    """Lookup tables built from a list of SchemaExtractedBatch payloads.

    All keys are lowercase to make matching case-insensitive — DB identifiers
    are conventionally lowercase in Postgres and snake_case across the
    pipeline, but the jOOQ Java constants are UPPER_SNAKE.
    """
    tables_by_name: dict[str, DatabaseTable] = field(default_factory=dict)
    columns_by_qualified: dict[str, DatabaseColumn] = field(default_factory=dict)
    columns_by_simple: dict[str, list[DatabaseColumn]] = field(default_factory=dict)
    jooq_table_by_constant: dict[str, JooqTableBinding] = field(default_factory=dict)
    jooq_field_by_constant: dict[str, JooqFieldBinding] = field(default_factory=dict)
    openapi_by_method_path: dict[tuple[str, str], OpenAPIOperation] = field(default_factory=dict)
    proto_rpcs: list[ProtoRpc] = field(default_factory=list)
    graphql_fields: list[GraphQLField] = field(default_factory=list)


def build_index(batches: Iterable[SchemaExtractedBatch]) -> SchemaIndex:
    idx = SchemaIndex()
    for b in batches:
        for t in b.tables:
            idx.tables_by_name[t.name.lower()] = t
        for c in b.columns:
            qkey = c.external_id.lower()
            idx.columns_by_qualified[qkey] = c
            idx.columns_by_simple.setdefault(c.name.lower(), []).append(c)
        for jt in b.jooq_tables:
            idx.jooq_table_by_constant[jt.java_constant.upper()] = jt
        for jf in b.jooq_fields:
            idx.jooq_field_by_constant[jf.jooq_constant.upper()] = jf
        for op in b.openapi_ops:
            idx.openapi_by_method_path[(op.method.upper(), _normalize_path(op.path))] = op
        idx.proto_rpcs.extend(b.proto_rpcs)
        idx.graphql_fields.extend(b.gql_fields)
    return idx


def resolve_all(batches: list[SchemaExtractedBatch]) -> SchemaExtractedBatch:
    """Re-resolve URNs across batches and emit the consolidated edges.

    Returns a new ``SchemaExtractedBatch`` carrying ONLY the cross-edges and
    any updates to existing bindings (no entities). Callers append these
    edges to the persistence stream.
    """
    idx = build_index(batches)
    result = SchemaExtractedBatch(file="", repo="", extractor_kind="schema_resolver")

    # 1. Re-aim jOOQ field bindings at real DatabaseColumn URNs where possible.
    for b in batches:
        for jf in b.jooq_fields:
            real = _resolve_jooq_field_to_column(jf, idx)
            if real is None:
                continue
            # Overwrite the placeholder URN that was emitted by the jooq extractor.
            jf.db_column_urn = real.external_id
            # Refresh the BINDS_TO_COLUMN edge with the resolved URN.
            result.edges.append(SchemaEdge(
                edge_type=EDGE_BINDS_TO_COLUMN,
                from_urn=jf.external_id,
                to_urn=real.external_id,
                evidence=f"{jf.jooq_constant} → {real.table_urn}.{real.name}",
            ))
        for jt in b.jooq_tables:
            tbl = idx.tables_by_name.get(jt.db_table_name.lower())
            if tbl is not None:
                jt.db_table_urn = tbl.external_id
                result.edges.append(SchemaEdge(
                    edge_type=EDGE_BINDS_TO_TABLE,
                    from_urn=jt.external_id,
                    to_urn=tbl.external_id,
                    evidence=f"{jt.jooq_class}.{jt.java_constant} → {tbl.name}",
                ))

    return result


def resolve_reads_column_edges(
    relationships: list[ExtractedRelationship],
    schema_batches: list[SchemaExtractedBatch],
) -> tuple[list[ExtractedRelationship], list[SchemaEdge]]:
    """Re-aim ``READS_COLUMN`` relationships that pointed at jOOQ constants.

    Walks the list of relationships looking for ``edge_type == READS_COLUMN``
    whose ``to_entity`` matches a known jOOQ field constant. For each match,
    rewrites ``to_entity`` to the resolved ``DatabaseColumn`` external_id.

    Returns ``(updated_relationships, supplementary_edges)``. The
    supplementary edges are SchemaEdge records (one per resolution) so the
    persistence layer can emit them alongside the relationship.
    """
    idx = build_index(schema_batches)
    extra: list[SchemaEdge] = []
    for rel in relationships:
        if rel.edge_type != EDGE_READS_COLUMN:
            continue
        target = (rel.to_entity or "").strip()
        if not target:
            continue
        column = _resolve_target_to_column(target, idx)
        if column is None:
            continue
        rel.to_entity = column.external_id
        rel.to_type = "DatabaseColumn"
        extra.append(SchemaEdge(
            edge_type=EDGE_READS_COLUMN,
            from_urn=rel.from_entity,
            to_urn=column.external_id,
            evidence=rel.evidence,
            confidence=rel.confidence,
        ))
    return relationships, extra


def resolve_documents_edges(
    code_endpoints: list[ExtractedEntity],
    schema_batches: list[SchemaExtractedBatch],
) -> list[SchemaEdge]:
    """For every code-side ``ApiEndpoint`` whose ``(method, path)`` matches an
    OpenAPI operation, emit a ``DOCUMENTS`` edge from the OpenAPI op → the
    endpoint. This is the killer feature for spec/impl drift detection.

    Code-side endpoints must encode ``(method, path)`` in their ``signature``
    or as keyword-formatted fields the relationship extractor has agreed on.
    We accept either of these signature shapes (the relationship_extractor
    already builds both):

        "GET /payers"
        "POST /v1/payers/{id}"
        "@GetMapping('/payers')"
    """
    idx = build_index(schema_batches)
    out: list[SchemaEdge] = []
    for ep in code_endpoints:
        if ep.entity_type != "ApiEndpoint":
            continue
        method, path = _parse_endpoint_signature(ep.signature)
        if not method or not path:
            continue
        op = idx.openapi_by_method_path.get((method.upper(), _normalize_path(path)))
        if op is None:
            continue
        out.append(SchemaEdge(
            edge_type=EDGE_DOCUMENTS_OPENAPI,
            from_urn=op.external_id,
            to_urn=ep.external_id,
            evidence=f"{method} {path} matches {op.method} {op.path}",
        ))
    return out


def resolve_grpc_implementations(
    code_methods: list[ExtractedEntity],
    schema_batches: list[SchemaExtractedBatch],
) -> list[SchemaEdge]:
    """Best-effort: link gRPC server impl methods to their ProtoRpc.

    Matches by ``Method.name == ProtoRpc.name`` AND containing-class name
    matches the proto service name (case-insensitive). The relationship
    extractor records the class on ``ExtractedEntity.signature`` as
    ``"ClassName.methodName(...)"``.
    """
    idx = build_index(schema_batches)
    out: list[SchemaEdge] = []
    rpc_by_name: dict[str, list[ProtoRpc]] = {}
    for rpc in idx.proto_rpcs:
        rpc_by_name.setdefault(rpc.name.lower(), []).append(rpc)
    for method in code_methods:
        if method.entity_type not in {"Method", "Function"}:
            continue
        m_name = method.name.split("(")[0].strip()
        candidates = rpc_by_name.get(m_name.lower()) or []
        for rpc in candidates:
            class_name = method.signature.split(".")[0] if "." in method.signature else ""
            if class_name and rpc.service_urn.lower().endswith(class_name.lower().rstrip("Impl").lower()):
                out.append(SchemaEdge(
                    edge_type=EDGE_IMPLEMENTS_RPC,
                    from_urn=method.external_id,
                    to_urn=rpc.external_id,
                    evidence=f"{class_name}.{m_name} implements {rpc.service_urn}.{rpc.name}",
                ))
                break
    return out


def resolve_graphql_resolvers(
    code_methods: list[ExtractedEntity],
    schema_batches: list[SchemaExtractedBatch],
) -> list[SchemaEdge]:
    """Best-effort: match GraphQL ``Query`` / ``Mutation`` fields to resolver methods.

    A resolver typically lives on a class named ``QueryResolver`` /
    ``MutationResolver`` and has a method whose name matches the GraphQL
    field. We pick the first such match. This is intentionally heuristic —
    the brain can refine later via the relationship extractor.
    """
    idx = build_index(schema_batches)
    by_field: dict[str, list[GraphQLField]] = {}
    for f in idx.graphql_fields:
        # only operation roots are worth resolving
        if f.parent_type_urn.endswith("::Query") or f.parent_type_urn.endswith("::Mutation"):
            by_field.setdefault(f.name.lower(), []).append(f)
    out: list[SchemaEdge] = []
    for method in code_methods:
        if method.entity_type not in {"Method", "Function"}:
            continue
        m_name = method.name.split("(")[0].strip().lower()
        candidates = by_field.get(m_name) or []
        if not candidates:
            continue
        out.append(SchemaEdge(
            edge_type=EDGE_RESOLVES,
            from_urn=method.external_id,
            to_urn=candidates[0].external_id,
            evidence=f"resolver method {method.name} ↔ {candidates[0].external_id}",
        ))
    return out


# ── lookup helpers ────────────────────────────────────────────────────────────

def _resolve_jooq_field_to_column(
    jf: JooqFieldBinding, idx: SchemaIndex,
) -> Optional[DatabaseColumn]:
    """Try several strategies to find the DatabaseColumn behind a jOOQ field."""
    # 1. The jooq extractor records ``db_column_name`` already; combine with the
    #    table the field belongs to (the part before the dot in jooq_constant).
    table_const, _, _ = jf.jooq_constant.partition(".")
    table = idx.jooq_table_by_constant.get(table_const.upper())
    if table is not None:
        # Use the resolved DB table name to look up the column qualified URN.
        candidate_key = f"column::table::public.{table.db_table_name.lower()}.{jf.db_column_name.lower()}"
        col = idx.columns_by_qualified.get(candidate_key)
        if col is not None:
            return col
    # 2. Fall back to the column-name-only index, but only if there's a single
    #    column with that name across the schema.
    candidates = idx.columns_by_simple.get(jf.db_column_name.lower()) or []
    if len(candidates) == 1:
        return candidates[0]
    return None


def _resolve_target_to_column(target: str, idx: SchemaIndex) -> Optional[DatabaseColumn]:
    """Try to interpret a READS_COLUMN target string as a column reference.

    Handles three forms:
      - ``PLAN_INFO.PAYER_PLAN_ID``  (jOOQ field constant)
      - ``plan_info.payer_plan_id``  (lowercase, table-qualified)
      - ``payer_plan_id``            (column-only; uses the simple-name index)
    """
    raw = target.strip()
    if not raw:
        return None

    # Already a column URN.
    if raw.lower().startswith("column::"):
        return idx.columns_by_qualified.get(raw.lower())

    upper = raw.upper()
    if upper in idx.jooq_field_by_constant:
        jf = idx.jooq_field_by_constant[upper]
        # The bind may now have a real URN attached.
        if jf.db_column_urn:
            col = idx.columns_by_qualified.get(jf.db_column_urn.lower())
            if col is not None:
                return col

    if "." in raw:
        table, _, col_name = raw.rpartition(".")
        if not col_name:
            return None
        candidate_key = f"column::table::public.{table.lower()}.{col_name.lower()}"
        col = idx.columns_by_qualified.get(candidate_key)
        if col is not None:
            return col

    candidates = idx.columns_by_simple.get(raw.lower()) or []
    if len(candidates) == 1:
        return candidates[0]
    return None


def _normalize_path(path: str) -> str:
    """Strip a trailing slash and collapse double slashes so Spring's
    ``/payers`` and OpenAPI's ``/payers/`` compare equal."""
    if not path:
        return ""
    out = path
    while "//" in out:
        out = out.replace("//", "/")
    if len(out) > 1 and out.endswith("/"):
        out = out[:-1]
    return out


# ── orchestrator entry point ──────────────────────────────────────────────────

def _is_jooq_path(path: Path) -> bool:
    s = str(path).replace("\\", "/")
    return s.endswith(".java") and (
        "/generated-sources/jooq/" in s or "/generated/jooq/" in s
        or "/generated/sources/jooq/" in s
    )


def scan_repo_for_schema_files(repo_root: Path) -> list[Path]:
    """Walk a repo for the file shapes this ADR's extractors care about.

    Skipped directories: ``node_modules``, ``.git``, ``venv``, ``.venv``,
    ``target`` (except for the jOOQ generated subtree which we explicitly
    keep), ``build`` (same caveat).
    """
    if not repo_root.exists() or not repo_root.is_dir():
        return []

    skip_dirs = {"node_modules", ".git", ".idea", ".gradle", "__pycache__", "venv", ".venv"}
    schema_extensions = {".sql", ".proto", ".graphql", ".graphqls", ".gql"}
    out: list[Path] = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(repo_root).parts[:-1])
        if parts & skip_dirs:
            continue
        # Skip ``target``/``build`` outputs unless this is the jOOQ subtree.
        rel = "/".join(path.relative_to(repo_root).parts)
        if ("/target/" in f"/{rel}/" or rel.startswith("target/")) and not _is_jooq_path(path):
            continue
        if ("/build/" in f"/{rel}/" or rel.startswith("build/")) and not _is_jooq_path(path):
            continue

        suffix = path.suffix.lower()
        if suffix in schema_extensions:
            out.append(path)
            continue
        # OpenAPI YAML / JSON named with "openapi" or "swagger" hint.
        name = path.name.lower()
        stem = name.rsplit(".", 1)[0]
        if suffix in {".yaml", ".yml", ".json"} and ("openapi" in stem or "swagger" in stem):
            out.append(path)
            continue
        if _is_jooq_path(path):
            out.append(path)
            continue
    return out


def extract_schemas_for_repo(repo_root: Path, *, repo_name: str) -> list[SchemaExtractedBatch]:
    """Run every ADR-0058 extractor over a single repo, returning the typed
    batches. Pure synchronous work — invoked from the orchestrator with no
    DB or LLM side effects."""
    # Imports here to keep the resolver loadable when individual format
    # extractors have heavier optional dependencies.
    from companybrain.extractors.schema_sql import SchemaSqlExtractor
    from companybrain.extractors.schema_openapi import OpenAPIExtractor
    from companybrain.extractors.schema_proto import ProtoExtractor
    from companybrain.extractors.schema_graphql import GraphQLExtractor
    from companybrain.extractors.jooq_binding import JooqTablesExtractor

    sql_ext = SchemaSqlExtractor()
    openapi_ext = OpenAPIExtractor()
    proto_ext = ProtoExtractor()
    gql_ext = GraphQLExtractor()
    jooq_ext = JooqTablesExtractor()

    batches: list[SchemaExtractedBatch] = []

    for path in scan_repo_for_schema_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        ext = _pick_extractor(path, sql_ext, openapi_ext, proto_ext, gql_ext, jooq_ext)
        if ext is None:
            continue
        try:
            out = ext.extract(path, content, repo=repo_name)
        except Exception:
            continue
        typed = getattr(out, "_schema_batch", None)
        if typed is not None:
            batches.append(typed)
    return batches


def _pick_extractor(path, *extractors):
    for ex in extractors:
        try:
            if ex.supports(path):
                return ex
        except Exception:
            continue
    return None


def run_schema_extraction(repos: Iterable) -> dict:
    """Driver invoked by the orchestrator's Stage 0.5c hook.

    ``repos`` is iterable of objects with ``local_path``/``url`` attrs (the
    pipeline's ``RepoConfig``). Returns telemetry for stages_summary.
    """
    by_kind: dict[str, int] = {}
    all_batches: list[SchemaExtractedBatch] = []
    files_scanned = 0
    total_entities = 0

    for repo_cfg in repos:
        root_str = getattr(repo_cfg, "local_path", None) or getattr(repo_cfg, "url", None)
        if not root_str:
            continue
        root = Path(root_str)
        repo_name = root.name
        batches = extract_schemas_for_repo(root, repo_name=repo_name)
        all_batches.extend(batches)
        for b in batches:
            files_scanned += 1
            total_entities += b.entity_count
            by_kind[b.extractor_kind] = by_kind.get(b.extractor_kind, 0) + 1

    resolved = resolve_all(all_batches)
    total_edges = sum(len(b.edges) for b in all_batches) + len(resolved.edges)

    return {
        "files": files_scanned,
        "by_kind": by_kind,
        "entities": total_entities,
        "edges": total_edges,
    }


def _parse_endpoint_signature(signature: str) -> tuple[str, str]:
    """Extract ``(method, path)`` from common Spring / Express endpoint
    signatures. Tolerates these shapes:

      "GET /payers"
      "POST  /v1/payers/{id}"
      "@GetMapping('/payers')"
      "@PostMapping(\"/payers\")"
    """
    if not signature:
        return "", ""
    s = signature.strip()
    # "VERB path"
    parts = s.split(None, 1)
    if parts and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return parts[0].upper(), parts[1].strip() if len(parts) > 1 else ""

    # Spring annotation form
    annotation_to_method = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
        "RequestMapping": "",
    }
    if s.startswith("@"):
        head, _, rest = s[1:].partition("(")
        if head in annotation_to_method:
            method = annotation_to_method[head]
            path = ""
            inner = rest.rstrip(")").strip()
            if inner.startswith(("'", '"')):
                quote = inner[0]
                end = inner.find(quote, 1)
                if end > 0:
                    path = inner[1:end]
            return method, path
    return "", ""
