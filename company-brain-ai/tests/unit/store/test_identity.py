"""
Unit tests for companybrain.store.identity — ADR-0013.

Covers:
  - canonical URN construction and round-tripping
  - encoding/decoding of special characters in qualified_name
  - rejection of unknown entity types
  - legacy migration helpers
  - backward-compatible legacy shims
"""
import pytest
from companybrain.store.identity import (
    to_urn,
    parse_urn,
    from_legacy_postgres,
    from_legacy_neo4j,
    URNParts,
    ALLOWED_ENTITY_TYPES,
    make_entity_id,
    parse_entity_id,
    to_external_id,
    NODE_TYPE_TAXONOMY,
    workspace_slug_for,
)


# ── Canonical URN round-trip ─────────────────────────────────────────────────

def test_round_trip():
    urn = to_urn(tenant="acme", domain="code", repo="web",
                 entity_type="component", qualified_name="UserCard")
    parts = parse_urn(urn)
    assert parts == URNParts("acme", "code", "web", "component", "UserCard")


def test_path_qname_round_trip():
    urn = to_urn(tenant="acme", domain="code", repo="api",
                 entity_type="api_contract",
                 qualified_name="GET /users/{id}")
    parts = parse_urn(urn)
    assert parts.qualified_name == "GET /users/{id}"


def test_round_trip_all_entity_types():
    for etype in ALLOWED_ENTITY_TYPES:
        urn = to_urn(tenant="t", domain="code", repo="r",
                     entity_type=etype, qualified_name="Q")
        assert parse_urn(urn).entity_type == etype


def test_urn_starts_with_scheme():
    urn = to_urn(tenant="dev", domain="code", repo="monorepo",
                 entity_type="component", qualified_name="Foo")
    assert urn.startswith("urn:cb:")


def test_urn_parts_to_urn_matches_to_urn():
    parts = URNParts("acme", "code", "api", "data_model", "UserDTO")
    assert parts.to_urn() == to_urn(
        tenant="acme", domain="code", repo="api",
        entity_type="data_model", qualified_name="UserDTO",
    )


# ── Encoding of special characters ───────────────────────────────────────────

def test_colon_in_qname_round_trips():
    qname = "namespace:MyClass"
    urn = to_urn(tenant="t", domain="code", repo="r",
                 entity_type="component", qualified_name=qname)
    assert parse_urn(urn).qualified_name == qname


def test_slash_in_qname_round_trips():
    qname = "GET /payments/{id}/charge"
    urn = to_urn(tenant="t", domain="code", repo="r",
                 entity_type="api_contract", qualified_name=qname)
    assert parse_urn(urn).qualified_name == qname


def test_curly_braces_in_qname_round_trips():
    qname = "GET /users/{userId}/orders"
    urn = to_urn(tenant="t", domain="code", repo="r",
                 entity_type="api_contract", qualified_name=qname)
    assert parse_urn(urn).qualified_name == qname


# ── Validation ────────────────────────────────────────────────────────────────

def test_rejects_unknown_entity_type():
    with pytest.raises(ValueError, match="Unknown entity_type"):
        to_urn(tenant="a", domain="code", repo="r",
               entity_type="weird_type", qualified_name="X")


def test_parse_urn_rejects_non_cb_urn():
    with pytest.raises(ValueError, match="Not a CB URN"):
        parse_urn("urn:other:something")


def test_parse_urn_rejects_too_few_segments():
    with pytest.raises(ValueError, match="too few segments"):
        parse_urn("urn:cb:tenant:domain")


# ── Legacy Postgres migration helper ─────────────────────────────────────────

def test_legacy_postgres_translation():
    urn = from_legacy_postgres(
        workspace_slug="dev",
        node_type="ApiEndpoint",
        legacy_external_id="backend/src/p.ts::charge",
        repo="monorepo",
    )
    assert urn == "urn:cb:dev:code:monorepo:api_contract:charge"


def test_legacy_postgres_no_separator():
    urn = from_legacy_postgres(
        workspace_slug="dev",
        node_type="Class",
        legacy_external_id="PaymentService",
        repo="monorepo",
    )
    assert parse_urn(urn).qualified_name == "PaymentService"
    assert parse_urn(urn).entity_type == "component"


def test_legacy_postgres_unknown_node_type_defaults_to_component():
    urn = from_legacy_postgres(
        workspace_slug="dev",
        node_type="WeirdType",
        legacy_external_id="foo::Bar",
        repo="monorepo",
    )
    assert parse_urn(urn).entity_type == "component"


# ── Legacy Neo4j migration helper ─────────────────────────────────────────────

def test_legacy_neo4j_translation():
    legacy = "urn:cb:llm:dev:src/Foo.ts:Foo"
    urn = from_legacy_neo4j(legacy, repo="monorepo")
    assert urn.startswith("urn:cb:dev:code:monorepo:component:")
    assert parse_urn(urn).qualified_name == "Foo"


def test_legacy_neo4j_rejects_non_llm_urn():
    with pytest.raises(ValueError, match="Not a legacy Neo4j URN"):
        from_legacy_neo4j("urn:cb:somethingelse:x:y:z", repo="r")


# ── NODE_TYPE_TAXONOMY completeness ──────────────────────────────────────────

def test_taxonomy_values_are_allowed_entity_types():
    for node_type, entity_type in NODE_TYPE_TAXONOMY.items():
        assert entity_type in ALLOWED_ENTITY_TYPES, (
            f"NODE_TYPE_TAXONOMY[{node_type!r}] = {entity_type!r} "
            f"is not in ALLOWED_ENTITY_TYPES"
        )


# ── workspace_slug_for ───────────────────────────────────────────────────────

def test_workspace_slug_for_returns_env_or_default(monkeypatch):
    monkeypatch.setenv("BRAIN_WORKSPACE_SLUG", "staging")
    assert workspace_slug_for("any-uuid") == "staging"


def test_workspace_slug_for_default(monkeypatch):
    monkeypatch.delenv("BRAIN_WORKSPACE_SLUG", raising=False)
    assert workspace_slug_for("any-uuid") == "dev"


# ── Legacy shims (backward compatibility) ────────────────────────────────────

def test_make_entity_id():
    eid = make_entity_id("my-repo", "component", "PaymentService")
    assert eid == "my-repo::component::PaymentService"


def test_parse_entity_id():
    repo, etype, qname = parse_entity_id("my-repo::api_contract::POST_charge")
    assert repo == "my-repo"
    assert etype == "api_contract"
    assert qname == "POST_charge"


def test_parse_entity_id_invalid():
    with pytest.raises(ValueError):
        parse_entity_id("no-separators-here")


def test_to_external_id_passthrough():
    eid = "repo::component::Foo"
    assert to_external_id(eid) == eid


def test_legacy_round_trip():
    original = make_entity_id("svc", "data_model", "UserTable")
    parts = parse_entity_id(original)
    assert parts == ("svc", "data_model", "UserTable")
