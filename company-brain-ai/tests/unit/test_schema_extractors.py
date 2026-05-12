"""Unit tests for the ADR-0058 schema-format extractors."""
from __future__ import annotations

from pathlib import Path

from companybrain.extractors.jooq_binding import parse_jooq_java, _normalize_sqltype
from companybrain.extractors.schema_graphql import GraphQLExtractor
from companybrain.extractors.schema_openapi import OpenAPIExtractor
from companybrain.extractors.schema_proto import ProtoExtractor
from companybrain.extractors.schema_resolver import (
    build_index,
    extract_schemas_for_repo,
    resolve_all,
    resolve_documents_edges,
    resolve_reads_column_edges,
    run_schema_extraction,
)
from companybrain.extractors.schema_sql import SchemaSqlExtractor
from companybrain.models.entities import (
    EDGE_BINDS_TO_COLUMN,
    EDGE_DOCUMENTS_OPENAPI,
    EDGE_FOREIGN_KEY,
    EDGE_INDEXES,
    EDGE_MIGRATION_ALTERS,
    EDGE_MIGRATION_CREATES,
    EDGE_READS_COLUMN,
    ExtractedEntity,
    ExtractedRelationship,
)


# ── S1: SQL DDL ───────────────────────────────────────────────────────────────

def _sql_batch(content: str, name: str = "V1__baseline.sql"):
    out = SchemaSqlExtractor().extract(Path(name), content, repo="netiq")
    return out._schema_batch


def test_create_table_emits_typed_columns_and_pk():
    b = _sql_batch(
        """
        CREATE TABLE plan_info (
            payer_plan_id varchar(64) PRIMARY KEY,
            is_current boolean NOT NULL DEFAULT TRUE
        );
        """
    )
    assert len(b.tables) == 1
    t = b.tables[0]
    assert t.name == "plan_info"
    assert t.primary_key_columns == ["payer_plan_id"]
    by_name = {c.name: c for c in b.columns}
    assert by_name["payer_plan_id"].type == "varchar(64)"
    assert by_name["payer_plan_id"].is_primary_key is True
    assert by_name["is_current"].type == "boolean"
    assert by_name["is_current"].nullable is False
    assert by_name["is_current"].default_value == "TRUE"


def test_text_array_type_preserved():
    """A9 — the lob column query depends on this: text[] is array, not text."""
    b = _sql_batch(
        """
        CREATE TABLE comp_providers (
            id uuid PRIMARY KEY,
            payer_id text[] NOT NULL
        );
        """
    )
    by_name = {c.name: c for c in b.columns}
    assert by_name["payer_id"].type == "text[]"
    assert by_name["payer_id"].is_array is True


def test_alter_table_add_column():
    b = _sql_batch(
        """
        ALTER TABLE plan_info ADD COLUMN lob text;
        ALTER TABLE plan_info ADD COLUMN deactivated boolean DEFAULT FALSE;
        """,
        name="V2__add_lob.sql",
    )
    by_name = {c.name: c for c in b.columns}
    assert "lob" in by_name
    assert by_name["lob"].type == "text"
    assert by_name["deactivated"].type == "boolean"
    assert any(e.edge_type == EDGE_MIGRATION_ALTERS for e in b.edges)


def test_create_index_partial_unique():
    b = _sql_batch(
        """
        CREATE UNIQUE INDEX idx_comp_payer
            ON comp_providers (payer_id)
            WHERE payer_id IS NOT NULL;
        """
    )
    assert len(b.indexes) == 1
    idx = b.indexes[0]
    assert idx.is_unique is True
    assert idx.columns == ["payer_id"]
    assert idx.where_clause is not None and "IS NOT NULL" in idx.where_clause
    assert any(e.edge_type == EDGE_INDEXES for e in b.edges)


def test_foreign_key_column_level():
    b = _sql_batch(
        """
        CREATE TABLE comp_providers (
            id uuid PRIMARY KEY,
            plan_info_id varchar(64) REFERENCES plan_info(payer_plan_id)
        );
        """
    )
    fk = next(c for c in b.columns if c.name == "plan_info_id")
    assert fk.is_foreign_key is True
    assert fk.fk_references == "public.plan_info.payer_plan_id"
    assert any(e.edge_type == EDGE_FOREIGN_KEY for e in b.edges)


def test_partitioning_recognised():
    b = _sql_batch(
        """
        CREATE TABLE metrics (
            id uuid,
            captured_at timestamp
        ) PARTITION BY RANGE (captured_at);
        """
    )
    t = b.tables[0]
    assert t.is_partitioned is True
    assert t.partition_strategy == "RANGE"


def test_migration_records_creates_and_alters():
    b = _sql_batch(
        """
        CREATE TABLE plan_info (id uuid PRIMARY KEY);
        ALTER TABLE plan_info ADD COLUMN lob text;
        """
    )
    assert len(b.migrations) == 1
    m = b.migrations[0]
    assert m.version == "V1"
    assert "table::public.plan_info" in m.creates
    assert "table::public.plan_info" in m.alters


# ── S2: jOOQ Tables.java ──────────────────────────────────────────────────────

_JOOQ_SAMPLE = """
package com.example.db.tables;

import org.jooq.TableField;
import org.jooq.impl.SQLDataType;
import org.jooq.impl.TableImpl;
import org.jooq.impl.DSL;

public class PlanInfo extends TableImpl<PlanInfoRecord> {

    public static final PlanInfo PLAN_INFO = new PlanInfo();

    private PlanInfo() {
        super(DSL.name("plan_info"));
    }

    public final TableField<PlanInfoRecord, String> PAYER_PLAN_ID =
        createField(DSL.name("payer_plan_id"), SQLDataType.VARCHAR(64).nullable(false), this, "");

    public final TableField<PlanInfoRecord, Boolean> IS_CURRENT =
        createField(DSL.name("is_current"), SQLDataType.BOOLEAN.nullable(false), this, "");
}
"""


def test_jooq_table_binding_class_and_constant():
    b = parse_jooq_java("PlanInfo.java", _JOOQ_SAMPLE, repo="netiq")
    assert len(b.jooq_tables) == 1
    tb = b.jooq_tables[0]
    assert tb.jooq_class == "com.example.db.tables.PlanInfo"
    assert tb.java_constant == "PLAN_INFO"
    assert tb.db_table_name == "plan_info"
    assert tb.db_table_urn == "table::public.plan_info"


def test_jooq_field_binding_and_type_preserved():
    b = parse_jooq_java("PlanInfo.java", _JOOQ_SAMPLE, repo="netiq")
    by_const = {f.jooq_constant: f for f in b.jooq_fields}
    assert "PLAN_INFO.PAYER_PLAN_ID" in by_const
    assert by_const["PLAN_INFO.PAYER_PLAN_ID"].db_column_name == "payer_plan_id"
    assert by_const["PLAN_INFO.PAYER_PLAN_ID"].db_type == "VARCHAR(64)"
    assert by_const["PLAN_INFO.IS_CURRENT"].db_type == "BOOLEAN"
    assert any(e.edge_type == EDGE_BINDS_TO_COLUMN for e in b.edges)


def test_normalize_sqltype_strips_chained_calls():
    assert _normalize_sqltype("SQLDataType.VARCHAR(64).nullable(false)") == "VARCHAR(64)"
    assert _normalize_sqltype("org.jooq.impl.SQLDataType.NUMERIC(10, 2)") == "NUMERIC(10, 2)"
    assert _normalize_sqltype("SQLDataType.BOOLEAN.nullable(false)") == "BOOLEAN"


# ── S3: OpenAPI ───────────────────────────────────────────────────────────────

_OPENAPI = """
openapi: 3.0.3
info: { title: t, version: '1' }
paths:
  /payers:
    get:
      operationId: listPayers
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Payer'
    post:
      operationId: createPayer
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Payer'
      responses:
        '201':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Payer'
components:
  schemas:
    Payer:
      type: object
      required: [id]
      properties:
        id: { type: string }
        lob: { type: array, items: { type: string } }
"""


def test_openapi_emits_operations_and_schemas():
    out = OpenAPIExtractor().extract(Path("openapi.yaml"), _OPENAPI, repo="netiq")
    b = out._schema_batch
    methods_paths = {(o.method, o.path) for o in b.openapi_ops}
    assert ("GET", "/payers") in methods_paths
    assert ("POST", "/payers") in methods_paths
    schema_names = {s.name for s in b.openapi_schemas}
    assert "Payer" in schema_names


def test_openapi_supports_only_named_files():
    e = OpenAPIExtractor()
    assert e.supports(Path("openapi.yaml")) is True
    assert e.supports(Path("openapi-v1.json")) is True
    assert e.supports(Path("api.swagger.yaml")) is True
    assert e.supports(Path("application.yml")) is False
    assert e.supports(Path("schema.graphqls")) is False


# ── S4: Protobuf ──────────────────────────────────────────────────────────────

_PROTO = """
syntax = "proto3";
package payers.v1;

message Payer {
    string id = 1;
    repeated string lob = 2;
}

message ListPayersRequest { int32 page_size = 1; }
message ListPayersResponse { repeated Payer payers = 1; }

service PayerService {
    rpc ListPayers (ListPayersRequest) returns (ListPayersResponse);
    rpc StreamPayers (ListPayersRequest) returns (stream Payer);
}
"""


def test_proto_messages_services_rpcs():
    out = ProtoExtractor().extract(Path("payers.proto"), _PROTO, repo="netiq")
    b = out._schema_batch
    msg_names = {m.name for m in b.proto_messages}
    assert {"Payer", "ListPayersRequest", "ListPayersResponse"} <= msg_names
    assert any(s.name == "PayerService" for s in b.proto_services)
    rpcs = {r.name: r for r in b.proto_rpcs}
    assert "ListPayers" in rpcs
    assert rpcs["StreamPayers"].server_streaming is True


def test_proto_repeated_fields_marked():
    out = ProtoExtractor().extract(Path("payers.proto"), _PROTO, repo="netiq")
    b = out._schema_batch
    payer = next(m for m in b.proto_messages if m.name == "Payer")
    lob = next(f for f in payer.fields if f["name"] == "lob")
    assert lob["repeated"] is True


# ── S5: GraphQL ───────────────────────────────────────────────────────────────

_GQL = """
type Payer { id: ID! lob: [String!]! }
type Query { payer(id: ID!): Payer  listPayers(limit: Int = 50): [Payer!]! }
type Mutation { createPayer(name: String!): Payer! }
"""


def test_graphql_types_and_operations():
    out = GraphQLExtractor().extract(Path("schema.graphqls"), _GQL, repo="netiq")
    b = out._schema_batch
    types = {t.name: t for t in b.gql_types}
    assert types["Payer"].kind == "OBJECT"
    ops = {o.name: o for o in b.gql_ops}
    assert ops["payer"].return_type == "Payer"
    assert ops["listPayers"].return_type == "[Payer!]!"
    assert ops["createPayer"].operation == "mutation"


# ── Resolver ──────────────────────────────────────────────────────────────────

def test_resolver_links_jooq_field_to_db_column():
    sql_b = _sql_batch(
        """
        CREATE TABLE plan_info (
            payer_plan_id varchar(64) PRIMARY KEY
        );
        """
    )
    jooq_b = parse_jooq_java("PlanInfo.java", _JOOQ_SAMPLE, repo="netiq")
    resolve_all([sql_b, jooq_b])
    # After resolution the field's db_column_urn should match the column external_id.
    field = next(f for f in jooq_b.jooq_fields if f.jooq_constant == "PLAN_INFO.PAYER_PLAN_ID")
    target_column = next(c for c in sql_b.columns if c.name == "payer_plan_id")
    assert field.db_column_urn == target_column.external_id


def test_reads_column_edge_rewritten_via_jooq_resolution():
    sql_b = _sql_batch(
        """
        CREATE TABLE plan_info (
            payer_plan_id varchar(64) PRIMARY KEY
        );
        """
    )
    jooq_b = parse_jooq_java("PlanInfo.java", _JOOQ_SAMPLE, repo="netiq")
    # Pre-run the resolver so jooq fields have real URNs.
    resolve_all([sql_b, jooq_b])

    rel = ExtractedRelationship(
        from_entity="netiq/svc/PayerService.java::getPayer",
        from_type="Method",
        edge_type=EDGE_READS_COLUMN,
        to_entity="PLAN_INFO.PAYER_PLAN_ID",
        to_type="String",
        confidence=0.9,
        evidence="jOOQ DSL select",
    )
    rels, extra = resolve_reads_column_edges([rel], [sql_b, jooq_b])
    assert rel.to_entity == "column::table::public.plan_info.payer_plan_id"
    assert rel.to_type == "DatabaseColumn"
    assert len(extra) == 1


def test_documents_edge_matches_spring_annotation():
    out = OpenAPIExtractor().extract(Path("openapi.yaml"), _OPENAPI, repo="netiq")
    openapi_b = out._schema_batch
    endpoint = ExtractedEntity(
        entity_type="ApiEndpoint",
        name="GET /payers",
        file="src/main/java/com/example/PayerController.java",
        repo="netiq",
        signature="GET /payers",
        last_modified_commit="abc",
        confidence=1.0,
    )
    edges = resolve_documents_edges([endpoint], [openapi_b])
    assert len(edges) == 1
    assert edges[0].edge_type == EDGE_DOCUMENTS_OPENAPI
    assert edges[0].to_urn.endswith("PayerController.java::GET /payers")


def test_documents_edge_handles_spring_annotation_signature():
    out = OpenAPIExtractor().extract(Path("openapi.yaml"), _OPENAPI, repo="netiq")
    openapi_b = out._schema_batch
    endpoint = ExtractedEntity(
        entity_type="ApiEndpoint",
        name="listPayers",
        file="x.java",
        repo="netiq",
        signature='@GetMapping("/payers")',
        last_modified_commit="abc",
        confidence=1.0,
    )
    edges = resolve_documents_edges([endpoint], [openapi_b])
    assert len(edges) == 1


# ── End-to-end: walk the network-iq-snapshot fixture ─────────────────────────

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "network-iq-snapshot"


def test_extract_schemas_for_repo_picks_up_every_format():
    batches = extract_schemas_for_repo(_FIXTURE, repo_name="network-iq-snapshot")
    kinds = {b.extractor_kind for b in batches}
    # All five schema kinds must be represented (the fixture ships fixtures of each)
    assert kinds == {
        "schema_sql",
        "schema_jooq",
        "schema_openapi",
        "schema_proto",
        "schema_graphql",
    }


def test_run_schema_extraction_summary_shape():
    class _FakeRepo:
        local_path = str(_FIXTURE)
        url = None
    summary = run_schema_extraction([_FakeRepo()])
    assert summary["files"] > 0
    assert summary["entities"] > 0
    assert summary["edges"] > 0
    assert set(summary["by_kind"].keys()) == {
        "schema_sql",
        "schema_jooq",
        "schema_openapi",
        "schema_proto",
        "schema_graphql",
    }


def test_schema_dispatch_registered_for_sql_proto_graphql():
    """ADR-0057 dispatch should pick our schema extractors for the file shapes
    that don't conflict with the universal extractors."""
    from companybrain.extractors.dispatch import extractor_kind_for

    assert extractor_kind_for(Path("V1__baseline.sql")) == "schema_sql"
    assert extractor_kind_for(Path("api.proto")) == "schema_proto"
    assert extractor_kind_for(Path("schema.graphqls")) == "schema_graphql"
