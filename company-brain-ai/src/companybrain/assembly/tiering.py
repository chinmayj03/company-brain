"""T0 / T1 / T2 selection within a token budget."""
from __future__ import annotations

from companybrain.assembly.types import TokenBudget, SmartZonePayload
from companybrain.store.base import BrainEntity


# Approx tokens per character — overestimate slightly for safety.
TOKEN_PER_CHAR = 0.27


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * TOKEN_PER_CHAR))


def assign_tiers(*, ranked_urns: list[str],
                 entities: dict[str, BrainEntity],
                 budget: TokenBudget,
                 t1_top_n: int, t2_top_k: int) -> SmartZonePayload:
    """Place entities into T0 / T1 / T2 slots within budget."""
    payload = SmartZonePayload(task="", task_type=None, tokens_budget=budget.total)

    # T0 — all matched entities get their t0 token (always cheap, ≤ 30 tok each)
    t0_used = 0
    for u in ranked_urns:
        e = entities.get(u)
        if not e:
            continue
        t0_text = e.t0_token or e.qualified_name
        t = estimate_tokens(t0_text)
        if t0_used + t > budget.t0_summaries:
            break
        payload.t0.append({"urn": u, "t0": t0_text, "type": e.entity_type})
        t0_used += t
    payload.tokens_used += t0_used

    # T1 — top-N get their t1 token (~100 tok each)
    t1_used = 0
    for u in ranked_urns[:t1_top_n]:
        e = entities.get(u)
        if not e or not e.t1_token:
            continue
        t = estimate_tokens(e.t1_token)
        if t1_used + t > budget.t1_detail:
            break
        payload.t1.append({"urn": u, "t1": e.t1_token, "type": e.entity_type})
        t1_used += t
    payload.tokens_used += t1_used

    # T2 — top-K get full JSON (share the remaining t1_detail budget)
    t2_used = 0
    t2_budget = budget.t1_detail - t1_used
    for u in ranked_urns[:t2_top_k]:
        e = entities.get(u)
        if not e:
            continue
        payload_json = e.to_dict()
        text = str(payload_json)
        t = estimate_tokens(text)
        if t2_used + t > t2_budget:
            break
        payload.t2.append({"urn": u, "entity": payload_json})
        t2_used += t
    payload.tokens_used += t2_used

    return payload
