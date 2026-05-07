import json
import pytest
from pathlib import Path
from companybrain.store import JsonFileBrainStore, BrainEntity


@pytest.mark.asyncio
async def test_round_trip(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(
        id="repo::component::Foo",
        entity_type="component",
        repo="repo",
        file="src/Foo.tsx",
        qualified_name="Foo",
        t1_summary="Foo component",
        version_hash="abc",
    )
    await store.write(e, run_id="r1", workspace_id="w")
    out = await store.read(e.id)
    assert out is not None
    assert out.t1_summary == "Foo component"
    assert out.entity_type == "component"
    assert out.version_hash == "abc"


@pytest.mark.asyncio
async def test_fresh_check_after_write(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(
        id="r::c::A",
        entity_type="component",
        repo="r",
        file="A.tsx",
        qualified_name="A",
        version_hash="v1",
    )
    await store.write(e, run_id="r1", workspace_id="w")
    assert await store.is_fresh(e.id, "v1") is True
    assert await store.is_fresh(e.id, "v2") is False


@pytest.mark.asyncio
async def test_index_persistence(tmp_path: Path):
    """Index survives creating a new JsonFileBrainStore instance."""
    s = JsonFileBrainStore(tmp_path)
    await s.write(
        BrainEntity(id="x::y::A", entity_type="y", repo="x", file="a", qualified_name="A"),
        run_id="r",
        workspace_id="w",
    )
    # New store instance reading from same root
    s2 = JsonFileBrainStore(tmp_path)
    assert await s2.read("x::y::A") is not None


@pytest.mark.asyncio
async def test_commit_run_writes_manifest(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(id="r::c::B", entity_type="c", repo="r", file="b", qualified_name="B")
    await store.write(e, run_id="run-42", workspace_id="w")
    await store.commit_run("run-42")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["last_run_id"] == "run-42"
    assert len(manifest["runs"]) == 1


@pytest.mark.asyncio
async def test_list_ids(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    for i in range(3):
        e = BrainEntity(
            id=f"r::c::E{i}", entity_type="c", repo="r", file=f"e{i}.ts", qualified_name=f"E{i}"
        )
        await store.write(e, run_id="r", workspace_id="w")
    ids = [x async for x in store.list_ids()]
    assert len(ids) == 3
    assert "r::c::E0" in ids


@pytest.mark.asyncio
async def test_entity_file_is_valid_json(tmp_path: Path):
    store = JsonFileBrainStore(tmp_path)
    e = BrainEntity(
        id="r::api_contract::POST_users",
        entity_type="api_contract",
        repo="r",
        file="UserController.java",
        qualified_name="POST_users",
        metadata={"confidence": 0.95},
    )
    await store.write(e, run_id="r1", workspace_id="w")
    entity_files = list((tmp_path / "api_contract").glob("*.json"))
    assert len(entity_files) == 1
    parsed = json.loads(entity_files[0].read_text())
    assert parsed["entity_type"] == "api_contract"
    assert parsed["metadata"]["confidence"] == 0.95
