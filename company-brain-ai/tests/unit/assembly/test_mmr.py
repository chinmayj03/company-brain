from companybrain.assembly.mmr import mmr_rerank


def test_picks_diverse():
    embs = {
        "a": [1, 0, 0],
        "b": [0.99, 0.01, 0],
        "c": [0, 1, 0],
    }
    rel = {"a": 0.9, "b": 0.85, "c": 0.5}
    chosen = mmr_rerank(query_emb=[1, 0, 0], candidate_embs=embs,
                        relevance=rel, lambda_=0.5, top_k=2)
    # Should pick a (most relevant) then c (most diverse) — not b (near-duplicate of a)
    assert chosen == ["a", "c"]


def test_empty_candidates():
    chosen = mmr_rerank(query_emb=[1, 0], candidate_embs={}, relevance={}, top_k=5)
    assert chosen == []


def test_single_candidate():
    embs = {"x": [1.0, 0.0]}
    chosen = mmr_rerank(query_emb=[1, 0], candidate_embs=embs,
                        relevance={"x": 0.9}, top_k=5)
    assert chosen == ["x"]


def test_top_k_respected():
    embs = {str(i): [float(i), 0.0] for i in range(10)}
    rel  = {str(i): float(i) / 10 for i in range(10)}
    chosen = mmr_rerank(query_emb=[1, 0], candidate_embs=embs,
                        relevance=rel, lambda_=0.7, top_k=3)
    assert len(chosen) == 3
