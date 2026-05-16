# ADR-0065 — Multi-Graph RRF Retrieval Fusion (amend SmartZoneAssembler)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's `dynamics/retrieval.py` (Apache 2.0). RRF algorithm itself is Cormack, Clarke & Buettcher 2009 — public-domain academic prior art.
**Sequenced with:** independent of 0055-0063; recommended to land BEFORE the seed demo (highest query-quality lift per day of effort in the entire roadmap).

---

## Context

Today's `SmartZoneAssembler` retrieves candidates via:

1. **Hybrid search** — BM25 + Qdrant vector similarity → ONE ranking
2. **Neo4j graph traversal** — callers/callees/depends-on hops → SEPARATE list, not ranked against the hybrid result
3. **JSON brain reads** — exact-URN retrieval for cited entities → straight lookup

These three signals are **never combined into a unified ranking**. Today's path is roughly: take hybrid search top-K, plus 1-hop neighbours from Neo4j, plus URN-exact reads, deduplicate, throw at SmartZoneAssembler. The relative weight between "semantically similar" vs "structurally close" vs "recently changed" is hardcoded per query intent or guessed by heuristic.

ContextDB demonstrated a clean alternative: each retrieval source produces a ranking; **fuse them with Reciprocal Rank Fusion**. The fused ranking is dramatically better than any single source because each captures a different facet of relevance.

After ADR-0055 (cross-file pass), ADR-0058 (schema awareness), ADR-0059 (temporal/domain), the brain has 5+ orthogonal graphs over the same entities:
- **Semantic** — embeddings
- **Structural** — CALLS / EXTENDS / IMPLEMENTS edges
- **Temporal** — git ownership + recency
- **Domain** — REPRESENTS edges to DomainEntity
- **Cross-cutting** — Pattern + SharedInvariant + IMPLEMENTS_PATTERN edges

Without fusion, querying "where does PII flow in this codebase" pulls semantic neighbours of "PII" — which is wrong (PII as a concept != where PII actually flows). With fusion, the query pulls semantic AND structural (data-flow edges) AND temporal (recently touched paths) AND domain (entities marked as handling sensitive data) all weighted into one ranking.

---

## Decision

Add `assembly/multi_graph_retrieval.py` that runs N graph rankings in parallel and fuses them via RRF. Plug into the existing SmartZoneAssembler as the candidate-source layer.

### The RRF algorithm (Cormack et al. 2009, public-domain)

For each entity `e` and each ranking `R_i`:

```
RRF_score(e) = Σ_i (1 / (k + rank_i(e)))
```

Where:
- `k = 60` (their default; works across most use cases)
- `rank_i(e)` is `e`'s position in ranking `R_i` (1-indexed; ∞ if not in ranking)

**Why it works:** RRF is rank-based, not score-based — it doesn't care that semantic similarity scores are 0–1 while temporal recency is in days. The reciprocal-rank scheme means top-of-list matches dominate; a high rank in 2 of 5 rankings beats a mediocre rank in all 5.

### Per-graph rankers

Each implements the `Ranker` protocol:

```python
class Ranker(Protocol):
    name: str
    weight: float                    # multiplier applied AFTER RRF; for query-intent biasing
    async def rank(self, query: QueryContext) -> list[RankedEntity]: ...
```

Initial 5 rankers (each ~50-100 LOC):

1. **`SemanticRanker`** — wraps existing Qdrant search.
2. **`StructuralRanker`** — Neo4j BFS from entities mentioned in the query (or seed entities); emits N hops weighted by depth.
3. **`TemporalRanker`** — uses ADR-0059's TemporalOwnership; ranks by recent churn + age; configurable "what changed in last 30 days?" mode.
4. **`DomainRanker`** — uses ADR-0059's DomainEntity; if query mentions "Payer", boosts everything REPRESENTS=Payer.
5. **`PatternRanker`** — uses ADR-0055's Pattern entities; if query references a known idiom, boosts pattern instances.

### Query intent biases the weights

A QueryClassifier (already implicit in SmartZoneAssembler today) sets per-ranker weights:

```python
INTENT_WEIGHTS = {
    "what_does_X_do":          {semantic: 1.0, structural: 0.8, temporal: 0.2, domain: 0.5, pattern: 0.4},
    "what_changed":            {semantic: 0.4, structural: 0.5, temporal: 1.5, domain: 0.3, pattern: 0.2},
    "blast_radius":            {semantic: 0.3, structural: 1.5, temporal: 0.4, domain: 0.6, pattern: 0.5},
    "explain_architecture":    {semantic: 0.6, structural: 0.7, temporal: 0.2, domain: 1.5, pattern: 1.2},
    "find_anti_patterns":      {semantic: 0.3, structural: 0.4, temporal: 0.3, domain: 0.4, pattern: 1.8},
    "default":                 {semantic: 1.0, structural: 1.0, temporal: 0.6, domain: 0.8, pattern: 0.6},
}
```

Final score: `RRF_score(e) × Σ_i weight_i if e in ranking_i`.

### Telemetry-driven calibration

Every query logs the per-ranker contributions to the final top-K. After 1000 queries, we can answer "for blast_radius questions, what % of cited answers came from which ranker?" — and tune INTENT_WEIGHTS empirically.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/assembly/multi_graph_retrieval.py     # NEW — fusion orchestrator
company-brain-ai/src/companybrain/assembly/rrf.py                       # NEW — pure RRF algorithm
company-brain-ai/src/companybrain/assembly/rankers/                     # NEW DIRECTORY
company-brain-ai/src/companybrain/assembly/rankers/semantic_ranker.py
company-brain-ai/src/companybrain/assembly/rankers/structural_ranker.py
company-brain-ai/src/companybrain/assembly/rankers/temporal_ranker.py
company-brain-ai/src/companybrain/assembly/rankers/domain_ranker.py
company-brain-ai/src/companybrain/assembly/rankers/pattern_ranker.py
company-brain-ai/src/companybrain/assembly/rankers/base.py              # Ranker protocol + RankedEntity
company-brain-ai/src/companybrain/assembly/intent_classifier.py         # NEW — maps query → INTENT_WEIGHTS key
tests/unit/test_rrf.py                                                    # NEW
tests/unit/test_multi_graph_retrieval.py                                  # NEW
tests/acceptance/test_rrf_quality_lift.py                                 # NEW — A/B vs current
```

Append-only edits to:

```
company-brain-ai/src/companybrain/assembly/smart_zone.py    # use MultiGraphRetrieval as candidate source
company-brain-ai/src/companybrain/api/routes/query.py       # surface per-ranker contribution in response telemetry
```

---

## Acceptance test

```python
async def test_rrf_combines_orthogonal_signals():
    """Build a fixture where:
      - Entity A is semantic top-1 for query Q
      - Entity B is structural top-1 for Q (1-hop from cited entity)
      - Entity C is in both rankings at position 5
    RRF should rank C high (in 2 lists) above A and B (each top of one list)."""
    ranking = await multi_graph_retrieve(query="...", rankers=[fixture_rankers])
    assert ranking[0].entity == "C"
    assert ranking[0].telemetry["sources"] == ["semantic", "structural"]


async def test_intent_weights_bias_correctly():
    """A 'blast_radius' query should weight structural higher than semantic."""
    blast = await multi_graph_retrieve(query="what breaks if I rename customer_id?")
    assert blast.telemetry["weights_used"]["structural"] > blast.telemetry["weights_used"]["semantic"]


async def test_quality_lift_on_benchmark():
    """Run the BENCHMARK 5 canonical queries with RRF on; pass rate should
    increase by ≥ 10% vs single-source retrieval."""
    baseline = await run_benchmark(use_rrf=False)
    fused    = await run_benchmark(use_rrf=True)
    assert fused.pass_count >= baseline.pass_count * 1.1


async def test_ranker_contribution_telemetry():
    """Each query response includes per-ranker contribution counts."""
    response = await query("what does getPayerCompetitors do?")
    assert "ranker_contributions" in response.telemetry
    assert sum(response.telemetry["ranker_contributions"].values()) > 0
```

---

## Effort estimate

2 days, easily parallelisable across 2 sessions (rankers are independent):

| Workstream | Days |
|---|---|
| RRF algorithm + Ranker protocol + base | 0.5 |
| 5 per-graph rankers (parallel) | 1 |
| Intent classifier + integration into SmartZoneAssembler | 0.5 |

---

## Action items

1. [ ] `assembly/rrf.py` — pure function; takes N rankings, returns fused ranking. ~30 LOC.
2. [ ] `assembly/rankers/base.py` — Ranker protocol + RankedEntity dataclass.
3. [ ] `assembly/rankers/semantic_ranker.py` — wraps Qdrant.
4. [ ] `assembly/rankers/structural_ranker.py` — Neo4j BFS with depth weighting.
5. [ ] `assembly/rankers/temporal_ranker.py` — uses ADR-0059's TemporalOwnership.
6. [ ] `assembly/rankers/domain_ranker.py` — uses ADR-0059's DomainEntity.
7. [ ] `assembly/rankers/pattern_ranker.py` — uses ADR-0055's Pattern entities.
8. [ ] `assembly/intent_classifier.py` — keyword + Sonnet hybrid (small LLM call to confirm intent if ambiguous).
9. [ ] `assembly/multi_graph_retrieval.py` — orchestrator: parallel rankers → RRF → weighted final.
10. [ ] Wire into `assembly/smart_zone.py` as the candidate source.
11. [ ] Acceptance: 4 tests above PASS; benchmark pass rate +10%.
12. [ ] Telemetry: per-query `ranker_contributions`, `intent`, `weights_used`.
13. [ ] In comments: `# RRF fusion (Cormack et al. 2009; pattern adapted from ContextDB)`.
14. [ ] Add `THIRD-PARTY-INSPIRATIONS.md` entry per LEGAL-CONTEXTDB-INTEGRATION.md guidance.
