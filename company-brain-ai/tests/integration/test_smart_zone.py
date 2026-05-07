"""Integration test for SmartZoneAssembler — requires Neo4j + .brain/."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from companybrain.assembly.smart_zone import SmartZoneAssembler
from companybrain.assembly.types import TokenBudget
from companybrain.store.base import BrainEntity
from companybrain.store.json_store import JsonFileBrainStore


def _make_entity(qname: str, entity_type: str = "component") -> BrainEntity:
    return BrainEntity(
        id=f"urn:cb:dev:code:pilot:{entity_type}:{qname}",
        entity_type=entity_type,
        repo="pilot",
        file=f"src/{qname}.py",
        qualified_name=qname,
        t0_token=f"{qname} — a {entity_type}",
        t1_token=f"{qname} handles the {entity_type} logic for the pilot repo.",
        t1_summary=f"{qname} summary.",
        relationships=[],
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_assemble_returns_payload(tmp_path):
    # Populate .brain/ with a few entities
    store = JsonFileBrainStore(tmp_path)
    entities = [
        _make_entity("UserCard"),
        _make_entity("PaymentService"),
        _make_entity("UserDTO", "data_model"),
    ]
    for e in entities:
        await store.write(e, run_id="test", workspace_id="dev")

    # Mock Neo4j driver to return no neighbours (avoids live DB dependency)
    mock_session = AsyncMock()
    mock_result  = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__  = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    assembler = SmartZoneAssembler(
        brain_root=tmp_path,
        workspace_id="dev",
        store=store,
        neo4j_driver=mock_driver,
    )
    # Pass entities directly to bypass HybridSearcher (no Qdrant/BM25 in unit env)
    entity_urns = [e.id for e in entities]
    payload = await assembler.assemble(task="what does UserCard do",
                                       entities=entity_urns)

    assert payload.task_type.value == "READ"
    assert payload.rendered.startswith("=== COMPANY BRAIN CONTEXT ===")
    assert payload.tokens_used > 0
    assert payload.tokens_used <= payload.tokens_budget


@pytest.mark.asyncio
@pytest.mark.integration
async def test_budget_not_exceeded(tmp_path):
    store = JsonFileBrainStore(tmp_path)
    for i in range(30):
        e = BrainEntity(
            id=f"urn:cb:dev:code:pilot:component:Entity{i}",
            entity_type="component",
            repo="pilot",
            file=f"src/Entity{i}.py",
            qualified_name=f"Entity{i}",
            t0_token="A" * 50,
            t1_token="B" * 400,
        )
        await store.write(e, run_id="test", workspace_id="dev")

    mock_session = AsyncMock()
    mock_result  = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__  = AsyncMock(return_value=False)
    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    assembler = SmartZoneAssembler(
        brain_root=tmp_path,
        workspace_id="dev",
        store=store,
        neo4j_driver=mock_driver,
    )
    budget = TokenBudget.conservative()
    # Pass entities directly to bypass HybridSearcher
    entity_urns = [f"urn:cb:dev:code:pilot:component:Entity{i}" for i in range(30)]
    payload = await assembler.assemble(task="change Entity0 to add a status field",
                                       entities=entity_urns,
                                       budget=budget)
    assert payload.tokens_used <= budget.total
