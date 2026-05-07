import pytest
from companybrain.store import FanoutBrainStore, JsonFileBrainStore, BrainEntity


class _FailingMirror:
    """Mirror that always raises on write — used to verify fanout resilience."""

    async def write(self, e, *, run_id, workspace_id):
        raise RuntimeError("boom — intentional mirror failure")

    async def read(self, _):
        return None

    async def is_fresh(self, _, __):
        return False

    async def list_ids(self):
        if False:
            yield

    async def commit_run(self, run_id):
        pass


class _TrackingMirror:
    """Mirror that records every write call without raising."""

    def __init__(self):
        self.written: list[BrainEntity] = []
        self.committed: list[str] = []

    async def write(self, e, *, run_id, workspace_id):
        self.written.append(e)

    async def read(self, _):
        return None

    async def is_fresh(self, _, __):
        return False

    async def list_ids(self):
        if False:
            yield

    async def commit_run(self, run_id):
        self.committed.append(run_id)


@pytest.mark.asyncio
async def test_mirror_failure_does_not_break_primary(tmp_path):
    """A failing mirror must not propagate its exception to the caller."""
    primary = JsonFileBrainStore(tmp_path)
    fan = FanoutBrainStore(primary=primary, mirrors=[_FailingMirror()])
    e = BrainEntity(
        id="r::c::A", entity_type="c", repo="r", file="a", qualified_name="A", version_hash="v"
    )
    # Must not raise
    await fan.write(e, run_id="r1", workspace_id="w")
    # Primary must have received the write
    assert await primary.read(e.id) is not None


@pytest.mark.asyncio
async def test_mirror_receives_writes(tmp_path):
    """A healthy mirror should receive every entity write."""
    primary = JsonFileBrainStore(tmp_path)
    mirror = _TrackingMirror()
    fan = FanoutBrainStore(primary=primary, mirrors=[mirror])

    entities = [
        BrainEntity(id=f"r::c::E{i}", entity_type="c", repo="r", file=f"e{i}", qualified_name=f"E{i}")
        for i in range(3)
    ]
    for e in entities:
        await fan.write(e, run_id="r1", workspace_id="w")

    assert len(mirror.written) == 3


@pytest.mark.asyncio
async def test_read_delegates_to_primary(tmp_path):
    """FanoutBrainStore.read() returns from the primary."""
    primary = JsonFileBrainStore(tmp_path)
    fan = FanoutBrainStore(primary=primary, mirrors=[])
    e = BrainEntity(id="r::c::X", entity_type="c", repo="r", file="x", qualified_name="X")
    await fan.write(e, run_id="r1", workspace_id="w")
    out = await fan.read("r::c::X")
    assert out is not None
    assert out.qualified_name == "X"


@pytest.mark.asyncio
async def test_is_fresh_delegates_to_primary(tmp_path):
    """FanoutBrainStore.is_fresh() delegates to primary."""
    primary = JsonFileBrainStore(tmp_path)
    fan = FanoutBrainStore(primary=primary, mirrors=[])
    e = BrainEntity(id="r::c::F", entity_type="c", repo="r", file="f", qualified_name="F", version_hash="h1")
    await fan.write(e, run_id="r1", workspace_id="w")
    assert await fan.is_fresh("r::c::F", "h1") is True
    assert await fan.is_fresh("r::c::F", "h2") is False


@pytest.mark.asyncio
async def test_commit_run_calls_all_mirrors(tmp_path):
    """commit_run() must call commit_run on primary and all mirrors."""
    primary = JsonFileBrainStore(tmp_path)
    m1 = _TrackingMirror()
    m2 = _TrackingMirror()
    fan = FanoutBrainStore(primary=primary, mirrors=[m1, m2])
    e = BrainEntity(id="r::c::Z", entity_type="c", repo="r", file="z", qualified_name="Z")
    await fan.write(e, run_id="r1", workspace_id="w")
    await fan.commit_run("r1")
    assert "r1" in m1.committed
    assert "r1" in m2.committed


@pytest.mark.asyncio
async def test_commit_run_mirror_failure_does_not_raise(tmp_path):
    """A failing mirror commit_run must not prevent the primary from committing."""
    primary = JsonFileBrainStore(tmp_path)
    fan = FanoutBrainStore(primary=primary, mirrors=[_FailingMirror()])
    e = BrainEntity(id="r::c::Q", entity_type="c", repo="r", file="q", qualified_name="Q")
    await fan.write(e, run_id="r1", workspace_id="w")
    # Must not raise even though mirror.commit_run raises
    await fan.commit_run("r1")
    # Manifest must have been written by primary
    import json
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["last_run_id"] == "r1"
