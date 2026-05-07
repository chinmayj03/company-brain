"""
Integration test: URN alignment across .brain/, Postgres, and Neo4j.

After running the orchestrator on the pilot repo, every entity's URN must
appear identically in:
  - .brain/index.json   (JsonFileBrainStore SOT)
  - postgres nodes.urn  (Postgres mirror)
  - neo4j (n.id)        (Neo4j structural graph)

This test is skipped unless the environment variables
  BRAIN_TEST_INTEGRATION=1
  BRAIN_PILOT_PATH=<path to pilot repo>
  DATABASE_URL=<postgres dsn>
  NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
are all set, so it does not run in the standard `pytest` unit suite.

To run:
  BRAIN_TEST_INTEGRATION=1 \
  BRAIN_PILOT_PATH=./pilot \
  DATABASE_URL=postgresql://... \
  pytest tests/integration/test_urn_alignment.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

INTEGRATION = os.environ.get("BRAIN_TEST_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not INTEGRATION,
    reason="Set BRAIN_TEST_INTEGRATION=1 to run integration tests",
)


def _load_brain_urns(pilot_path: str) -> set[str]:
    index = Path(pilot_path) / ".brain" / "index.json"
    if not index.exists():
        return set()
    return set(json.loads(index.read_text()).keys())


@pytest.fixture(scope="module")
def brain_urns():
    pilot = os.environ.get("BRAIN_PILOT_PATH", "./pilot")
    return _load_brain_urns(pilot)


@pytest.fixture(scope="module")
def pg_urns():
    import asyncpg
    import asyncio

    dsn = os.environ["DATABASE_URL"]

    async def _fetch():
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch("SELECT urn FROM nodes WHERE urn IS NOT NULL")
        await conn.close()
        return {r["urn"] for r in rows}

    return asyncio.get_event_loop().run_until_complete(_fetch())


@pytest.fixture(scope="module")
def neo4j_urns():
    from neo4j import GraphDatabase

    uri  = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd  = os.environ.get("NEO4J_PASSWORD", "password")

    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    with driver.session() as session:
        result = session.run(
            "MATCH (n:CBNode) WHERE n.id STARTS WITH 'urn:cb:' RETURN n.id AS id"
        )
        urns = {r["id"] for r in result}
    driver.close()
    return urns


def test_brain_urns_are_nonempty(brain_urns):
    assert len(brain_urns) > 0, ".brain/index.json is empty — run brain indexing first"


def test_pg_urns_are_nonempty(pg_urns):
    assert len(pg_urns) > 0, "No URNs found in Postgres — run migration V5 first"


def test_neo4j_urns_are_nonempty(neo4j_urns):
    assert len(neo4j_urns) > 0, "No canonical URNs in Neo4j — run migrate-urn.ts first"


def test_brain_and_postgres_urns_align(brain_urns, pg_urns):
    missing_from_pg = brain_urns - pg_urns
    missing_from_brain = pg_urns - brain_urns
    assert not missing_from_pg, (
        f"URNs in .brain/ but not in Postgres ({len(missing_from_pg)}): "
        f"{sorted(missing_from_pg)[:5]}"
    )
    assert not missing_from_brain, (
        f"URNs in Postgres but not in .brain/ ({len(missing_from_brain)}): "
        f"{sorted(missing_from_brain)[:5]}"
    )


def test_brain_and_neo4j_urns_align(brain_urns, neo4j_urns):
    missing_from_neo4j = brain_urns - neo4j_urns
    missing_from_brain = neo4j_urns - brain_urns
    assert not missing_from_neo4j, (
        f"URNs in .brain/ but not in Neo4j ({len(missing_from_neo4j)}): "
        f"{sorted(missing_from_neo4j)[:5]}"
    )
    assert not missing_from_brain, (
        f"URNs in Neo4j but not in .brain/ ({len(missing_from_brain)}): "
        f"{sorted(missing_from_brain)[:5]}"
    )
