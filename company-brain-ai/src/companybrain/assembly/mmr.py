"""Maximum Marginal Relevance — diversify top-k retrieval results."""
from __future__ import annotations
import math


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def mmr_rerank(*, query_emb: list[float],
               candidate_embs: dict[str, list[float]],
               relevance: dict[str, float],
               lambda_: float = 0.7,
               top_k: int = 10) -> list[str]:
    """Return up to top_k urns balancing relevance and novelty."""
    selected: list[str] = []
    remaining = list(candidate_embs.keys())
    while remaining and len(selected) < top_k:
        if not selected:
            best = max(remaining, key=lambda u: relevance.get(u, 0.0))
        else:
            best, best_score = None, -1e9
            for u in remaining:
                rel = relevance.get(u, 0.0)
                redundancy = max(
                    cosine(candidate_embs[u], candidate_embs[s]) for s in selected
                )
                score = lambda_ * rel - (1 - lambda_) * redundancy
                if score > best_score:
                    best, best_score = u, score
        selected.append(best)
        remaining.remove(best)
    return selected
