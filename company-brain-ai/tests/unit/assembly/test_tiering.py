from companybrain.assembly.tiering import assign_tiers, estimate_tokens
from companybrain.assembly.types import TokenBudget
from companybrain.store.base import BrainEntity


def _make_entity(i: int) -> BrainEntity:
    return BrainEntity(
        id=f"urn:cb:t:c:r:component:E{i}",
        entity_type="component",
        repo="r",
        file="f",
        qualified_name=f"E{i}",
        t0_token="x" * 30,
        t1_token="y" * 200,
    )


def test_tiering_respects_budget():
    entities = {
        f"urn:cb:t:c:r:component:E{i}": _make_entity(i)
        for i in range(20)
    }
    payload = assign_tiers(
        ranked_urns=list(entities.keys()),
        entities=entities,
        budget=TokenBudget(total=1000, t0_summaries=200, t1_detail=400,
                           business_context=100, blast_radius=100),
        t1_top_n=10,
        t2_top_k=2,
    )
    assert payload.tokens_used <= 700  # T0 + T1 budget


def test_t0_populated():
    entities = {f"urn:cb:t:c:r:component:E{i}": _make_entity(i) for i in range(5)}
    payload = assign_tiers(
        ranked_urns=list(entities.keys()),
        entities=entities,
        budget=TokenBudget(),
        t1_top_n=3,
        t2_top_k=1,
    )
    assert len(payload.t0) > 0


def test_t1_limited_to_top_n():
    entities = {f"urn:cb:t:c:r:component:E{i}": _make_entity(i) for i in range(10)}
    payload = assign_tiers(
        ranked_urns=list(entities.keys()),
        entities=entities,
        budget=TokenBudget(total=10000, t0_summaries=5000, t1_detail=5000,
                           business_context=500, blast_radius=500),
        t1_top_n=3,
        t2_top_k=0,
    )
    assert len(payload.t1) <= 3


def test_estimate_tokens_nonzero():
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("") == 1  # clamped to 1
