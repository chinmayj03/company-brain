"""
Integration test: after a rebuild on any fixture, no edge in Neo4j should
reference a node that doesn't exist (orphaned edge).

Requires: Neo4j running at NEO4J_URI (skipped otherwise).
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI") and not os.getenv("CI"),
    reason="Neo4j not configured — set NEO4J_URI to run",
)
def test_no_orphan_source_nodes() -> None:
    """Every edge's source_id must resolve to an existing CBNode."""
    from neo4j import GraphDatabase  # type: ignore

    uri  = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pw   = os.environ.get("NEO4J_PASSWORD", "neo4j")

    with GraphDatabase.driver(uri, auth=(user, pw)) as driver:
        with driver.session() as session:
            # Find edges whose source_id has no matching node.
            result = session.run(
                """
                MATCH ()-[r]->()
                WHERE NOT EXISTS {
                    MATCH (n:CBNode) WHERE n.id = r.source_id
                }
                RETURN count(r) AS orphaned
                """
            )
            record = result.single()
            orphaned = int(record["orphaned"]) if record else 0

    assert orphaned == 0, (
        f"{orphaned} edges have a source_id with no matching CBNode — "
        "check for monorepo URN leaks in neo4j_writer._external_id_to_urn"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI") and not os.getenv("CI"),
    reason="Neo4j not configured — set NEO4J_URI to run",
)
def test_no_orphan_target_nodes() -> None:
    """Every edge's target_id must resolve to an existing CBNode."""
    from neo4j import GraphDatabase  # type: ignore

    uri  = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pw   = os.environ.get("NEO4J_PASSWORD", "neo4j")

    with GraphDatabase.driver(uri, auth=(user, pw)) as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH ()-[r]->()
                WHERE NOT EXISTS {
                    MATCH (n:CBNode) WHERE n.id = r.target_id
                }
                RETURN count(r) AS orphaned
                """
            )
            record = result.single()
            orphaned = int(record["orphaned"]) if record else 0

    assert orphaned == 0, (
        f"{orphaned} edges have a target_id with no matching CBNode — "
        "check for monorepo URN leaks in neo4j_writer._external_id_to_urn"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI") and not os.getenv("CI"),
    reason="Neo4j not configured — set NEO4J_URI to run",
)
def test_no_monorepo_urns_in_graph() -> None:
    """No node or edge in Neo4j should carry a URN with the repo segment 'monorepo'."""
    from neo4j import GraphDatabase  # type: ignore

    uri  = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pw   = os.environ.get("NEO4J_PASSWORD", "neo4j")

    with GraphDatabase.driver(uri, auth=(user, pw)) as driver:
        with driver.session() as session:
            node_result = session.run(
                "MATCH (n:CBNode) WHERE n.id CONTAINS ':monorepo:' RETURN count(n) AS c"
            )
            node_count = int((node_result.single() or {}).get("c", 0))

            edge_result = session.run(
                """
                MATCH ()-[r]->()
                WHERE r.source_id CONTAINS ':monorepo:' OR r.target_id CONTAINS ':monorepo:'
                RETURN count(r) AS c
                """
            )
            edge_count = int((edge_result.single() or {}).get("c", 0))

    assert node_count == 0, (
        f"{node_count} nodes have ':monorepo:' in their URN — "
        "repo was not threaded through during extraction"
    )
    assert edge_count == 0, (
        f"{edge_count} edges have ':monorepo:' in source_id/target_id — "
        "check _entity_to_row and _external_id_to_urn"
    )
