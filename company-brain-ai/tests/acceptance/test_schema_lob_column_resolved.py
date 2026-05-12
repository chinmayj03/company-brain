"""
Acceptance: ADR-0058 schema-format awareness over the network-iq-snapshot
fixture.

Mirrors the four scenarios called out in the ADR:

  - A9 "which tables have a lob column" — answered from DatabaseColumn entities,
    not code references.
  - jOOQ binding links PLAN_INFO.PAYER_PLAN_ID to the real DatabaseColumn URN.
  - OpenAPI drift detection: ``/v1/orphan-endpoint`` has no matching
    @GetMapping in the fixture, so the spec → impl resolver returns nothing
    for that operation. (And the converse — a Spring endpoint that DOES match
    the spec gets a DOCUMENTS edge.)
  - text[] type is preserved verbatim (``comp_providers.payer_id`` is text[],
    NOT text).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from companybrain.extractors.schema_resolver import (
    build_index,
    extract_schemas_for_repo,
    resolve_all,
    resolve_documents_edges,
)
from companybrain.models.entities import (
    EDGE_DOCUMENTS_OPENAPI,
    ExtractedEntity,
)


pytestmark = pytest.mark.acceptance


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "network-iq-snapshot"


def _index_for_fixture():
    batches = extract_schemas_for_repo(FIXTURE, repo_name="network-iq-snapshot")
    resolve_all(batches)
    return batches, build_index(batches)


def test_lob_columns_surface_via_database_column_entities():
    """A9 — must now PASS after S1 + S2.

    The brain should know which tables have a ``lob`` column purely from the
    DDL extractor; no LLM, no string-matching against query text.
    """
    batches, _ = _index_for_fixture()
    tables_with_lob: set[str] = set()
    for b in batches:
        for c in b.columns:
            if c.name.lower() == "lob":
                tables_with_lob.add(c.table_urn.removeprefix("table::"))
    # plan_info gets lob via the V2 ALTER; comp_providers has it on the CREATE.
    assert tables_with_lob >= {"public.plan_info", "public.comp_providers"}


def test_jooq_binding_links_code_to_real_column():
    """When code says ``READS_COLUMN PLAN_INFO.PAYER_PLAN_ID`` the brain must
    follow JooqFieldBinding → DatabaseColumn — i.e. the field binding's
    ``db_column_urn`` should point at the real DatabaseColumn external_id.
    """
    batches, idx = _index_for_fixture()
    field = idx.jooq_field_by_constant.get("PLAN_INFO.PAYER_PLAN_ID")
    assert field is not None
    # The resolver should have rewritten the URN to the actual column.
    expected = "column::table::public.plan_info.payer_plan_id"
    assert field.db_column_urn == expected


def test_text_array_column_type_preserved():
    """The brain must know comp_providers.payer_id is text[] (not text)."""
    batches, _ = _index_for_fixture()
    target = None
    for b in batches:
        for c in b.columns:
            if c.table_urn.endswith("comp_providers") and c.name == "payer_id":
                target = c
    assert target is not None, "payer_id column should have been extracted"
    assert target.type == "text[]"
    assert target.is_array is True


def test_openapi_drift_orphan_endpoint_has_no_documents_edge():
    """The fixture's openapi.yaml declares /v1/orphan-endpoint but there is no
    matching @GetMapping in the Java sources. ``resolve_documents_edges`` should
    NOT emit a DOCUMENTS edge for it (and SHOULD emit one for the matching
    /v1/payers endpoint we feed in synthetically)."""
    batches, _ = _index_for_fixture()

    endpoints = [
        ExtractedEntity(
            entity_type="ApiEndpoint",
            name="GET /v1/payers",
            file="src/main/java/com/example/PayerController.java",
            repo="network-iq-snapshot",
            signature="GET /v1/payers",
            last_modified_commit="hash",
            confidence=1.0,
        ),
    ]
    edges = resolve_documents_edges(endpoints, batches)
    assert any(e.edge_type == EDGE_DOCUMENTS_OPENAPI for e in edges)
    # No edge should resolve to the orphan op — that's the drift signal.
    assert not any("orphanEndpoint" in e.from_urn for e in edges)


def test_migration_creates_edge_emitted_per_table():
    """Every CREATE TABLE in V1__baseline.sql should emit a MIGRATION_CREATES
    edge so the graph can answer "which migration introduced this table?"."""
    batches, _ = _index_for_fixture()
    edge_targets: set[str] = set()
    for b in batches:
        for e in b.edges:
            if e.edge_type == "MIGRATION_CREATES":
                edge_targets.add(e.to_urn)
    assert "table::public.plan_info" in edge_targets
    assert "table::public.comp_providers" in edge_targets
