# ADR-0066 — ExperientialMemory Tier (the brain learns over time)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's `memory/experiential.py` (Apache 2.0; pattern adopted, code is our own per LEGAL-CONTEXTDB-INTEGRATION.md)
**Sequenced with:** ADR-0064 (audit log writes power experiential trace), ADR-0056 (verifier corrections feed experiential), ADR-0063 M3 (provenance schema). Can ship in parallel with most others; depends only on those three.

---

## Context

Today's brain is **stateless across queries**. Every `/query` is independent: hit the same question twice, you get answers based on the same retrieval. There's no concept of "this question was asked yesterday and the answer turned out to be wrong" or "users keep asking about the X module — pre-warm its context". Re-running `brain index` overwrites entities; previous extractions' signal is gone.

ContextDB's third memory tier — **Experiential** — captures what worked. Three distinct streams:
1. **Trajectories**: action → outcome → success/failure tuples
2. **Reflections**: synthesized lessons across multiple trajectories
3. **Workflows**: reusable plans derived from successful trajectories

Mapped to the brain's domain, the equivalent streams are:
1. **Query trajectories**: (question, answer, user_feedback, citations_used, latency, cost) per `/query` call
2. **Verifier corrections**: (entity_urn, original_claim, verified_claim, drift_reason) per `verifier_loop.py` invocation (ADR-0056)
3. **Pattern utility outcomes**: (pattern_urn, surfaced_in_query, did_user_find_useful) — does this idiom-detection pattern actually help users?

Without the experiential tier, the brain has no feedback loop. It's a one-way extractor + one-way querier. **With it**, the brain becomes a learning system: queries that succeeded shape future retrieval; corrections seed future verifications; patterns that no one cares about get pruned.

This is the difference between "stateless RAG" and "memory-augmented system" — the same difference that makes ChatGPT-with-memory more useful than ChatGPT-without.

---

## Decision

Add a third memory tier alongside `factual` (current entity store) and the implicit `working` (per-job state). Three sub-stores:

### M1 — Query Trajectory store

Every `/query` call writes:

```python
@dataclass
class QueryTrajectory:
    id: str                          # UUID
    workspace_id: str
    user_id: Optional[str]
    question: str
    question_intent: str             # from ADR-0065 intent_classifier
    answer_summary_md: str
    cited_entity_urns: list[str]
    confidence: float
    latency_ms: int
    cost_usd: float
    rankers_contributed: dict[str, int]   # from ADR-0065 telemetry
    user_feedback: Optional[Literal["thumbs_up", "thumbs_down", "edited", "ignored"]]
    user_correction: Optional[str]   # if user provided their own answer
    asked_at: datetime
    answered_at: datetime
    embedding: list[float]           # of question (for "we've seen this before" detection)
```

**Retention**: indefinite for thumbs_up + edited; 1 year for ignored; 90 days for thumbs_down (the failure cases are valuable data).

**Used at retrieval time**:
- `query_intent_classifier` consults past trajectories to refine intent guesses
- `MultiGraphRetrieval` (ADR-0065) checks if a similar question was answered well before; if so, boosts the entities that were cited last time
- A new `ExperientialRanker` (5th-and-a-half ranker for ADR-0065) ranks entities that appeared in past successful answers higher

### M2 — Verifier Corrections store

Every time `verifier_loop.py` (ADR-0056) drops, downgrades, or self-corrects an entity, write:

```python
@dataclass
class VerifierCorrection:
    id: str
    workspace_id: str
    entity_urn: str
    original_claim: dict             # what initial extraction said
    verified_claim: dict             # what the verifier confirmed (or None if dropped)
    correction_kind: Literal["dropped_hallucinated", "downgraded_confidence", "self_corrected"]
    correction_reason: str
    verifier_mode: Literal["deterministic", "subagent", "self_correction"]
    occurred_at: datetime
    same_pattern_count: int          # how many times this same correction has been seen
```

**Used at extraction time**: before the next extraction of the same entity, the ContextAgent's prompt is augmented with: *"Heads up — last time this method was extracted, claim X was rejected by the verifier because Y. Don't repeat the mistake."*

This is **active learning** — the brain gets better at extraction the more it extracts.

### M3 — Pattern Utility store

Every time a Pattern entity (from ADR-0055) is surfaced in a `/query` answer's citations, increment its utility counter:

```python
@dataclass
class PatternUtility:
    pattern_urn: str
    workspace_id: str
    surfaced_count: int              # times included in a query's cited entities
    user_acted_on_count: int         # times user gave thumbs_up or edited (positive signal)
    last_surfaced_at: datetime
```

**Used at evolution time** (with ADR-0067 evolution background process): patterns with `surfaced_count > N` AND `user_acted_on_count / surfaced_count > 30%` are "high-utility"; patterns surfaced > 30 days ago with zero user interaction are pruned (they were noise).

**Used at retrieval time**: the `PatternRanker` (from ADR-0065) weighs high-utility patterns higher than low-utility ones — even within the same workspace.

---

## Cross-workspace experiential learning (the moat)

When the brain has been running on multiple customer workspaces, the experiential signal aggregates across them — **WITHOUT** leaking customer data:

- Query intent classifications are shared (no customer code)
- Pattern utility scores can inform pattern detection in NEW customer workspaces
- Verifier-correction patterns ("when LLM emits a fake `query_text` for a method-with-only-Mockito-tests, drop it") become global rules

A `cross_workspace_experiential.py` aggregator (in ADR-0067 evolution layer) anonymises trajectory metadata, derives global rules, and writes them to a shared `global_brain_lessons` table that all workspaces benefit from.

**Critical**: only ANONYMOUS PATTERNS cross workspaces. Raw query text, customer code, customer entity URNs — never. Per ADR-0064 audit guarantees the boundary.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/memory/                              # NEW DIRECTORY
company-brain-ai/src/companybrain/memory/__init__.py
company-brain-ai/src/companybrain/memory/experiential/                 # NEW
company-brain-ai/src/companybrain/memory/experiential/__init__.py
company-brain-ai/src/companybrain/memory/experiential/query_trajectories.py
company-brain-ai/src/companybrain/memory/experiential/verifier_corrections.py
company-brain-ai/src/companybrain/memory/experiential/pattern_utility.py
company-brain-ai/src/companybrain/memory/experiential/store.py         # shared writer
company-brain-ai/src/companybrain/memory/cross_workspace_experiential.py  # aggregator
company-brain-ai/src/companybrain/assembly/rankers/experiential_ranker.py  # NEW — uses past trajectories
db/migrations/V17__experiential_tables.sql                              # NEW
tests/unit/test_experiential_store.py                                    # NEW
tests/unit/test_pattern_utility_pruning.py                               # NEW
tests/acceptance/test_brain_learns_from_feedback.py                      # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/api/routes/query.py        # write QueryTrajectory after every response; expose POST /feedback
company-brain-ai/src/companybrain/api/routes/feedback.py     # write user_feedback into the trajectory
company-brain-ai/src/companybrain/pipeline/verifier_loop.py  # write VerifierCorrection on every correction
company-brain-ai/src/companybrain/agents/context_agent.py    # consult VerifierCorrections before re-extracting same entity
company-brain-ai/src/companybrain/assembly/multi_graph_retrieval.py  # invoke ExperientialRanker as 6th source
company-brain-ai/src/companybrain/assembly/rankers/pattern_ranker.py  # consult PatternUtility for weighting
```

Does NOT touch any file owned by ADR-0055-0065 OR 0067-0069.

---

## Acceptance test

```python
async def test_query_trajectory_written_per_query():
    await query("what does getPayerCompetitors do?")
    trajs = await experiential_store.query_trajectories(question_contains="getPayerCompetitors")
    assert len(trajs) == 1
    assert trajs[0].confidence > 0
    assert len(trajs[0].cited_entity_urns) > 0


async def test_thumbs_down_feedback_recorded_and_used():
    """User thumbs-down a bad answer; same question next day surfaces a different
    set of cited entities thanks to the ExperientialRanker downweighting them."""
    r1 = await query("what tables does foo read?")
    await api.post("/feedback", {"trajectory_id": r1.id, "feedback": "thumbs_down"})
    r2 = await query("what tables does foo read?")
    assert set(r1.cited_entity_urns) != set(r2.cited_entity_urns)


async def test_verifier_correction_warns_next_extraction():
    """Verifier dropped a hallucinated query_text yesterday; today's
    re-extraction prompt for the same entity includes the warning."""
    await create_verifier_correction(entity_urn="X.Y", reason="...")
    prompt = await context_agent.build_prompt_for("X.Y")
    assert "last time this method was extracted, claim X was rejected" in prompt


async def test_pattern_utility_pruning():
    """A Pattern with no user interactions in 30 days gets pruned."""
    await create_pattern(name="useless_pattern")
    await advance_clock(days=31)
    await run_evolution_pruner()
    patterns = await brain_query("Pattern entities")
    assert "useless_pattern" not in [p.name for p in patterns]


async def test_cross_workspace_lesson_does_not_leak_customer_data():
    """An aggregated lesson surfaced from workspace A must not contain
    workspace A's raw query text or entity URNs."""
    await query_in_workspace("ws_A", "what does CustomerSecretMethod do?")
    lessons = await cross_workspace_experiential.aggregated_lessons()
    for l in lessons:
        assert "CustomerSecretMethod" not in l.lesson_text
        assert "ws_A" not in l.lesson_text
```

---

## Effort estimate

3 days, parallelisable to 2 days with 2 sessions:

| Workstream | Days |
|---|---|
| Schema + V17 migration + 3 store modules | 1 |
| ExperientialRanker + ContextAgent prompt augmentation | 1 |
| Cross-workspace aggregator + privacy guards | 1 |

---

## Action items

1. [ ] V17 migration: `query_trajectories`, `verifier_corrections`, `pattern_utility`, `global_brain_lessons` tables.
2. [ ] `memory/experiential/store.py` — shared writer with workspace_id scoping.
3. [ ] `memory/experiential/query_trajectories.py` — write after `/query`; query helpers for similarity lookup.
4. [ ] `memory/experiential/verifier_corrections.py` — write from `verifier_loop.py`.
5. [ ] `memory/experiential/pattern_utility.py` — increment surfaced/user_acted counters.
6. [ ] `memory/cross_workspace_experiential.py` — aggregator with strict anonymisation.
7. [ ] `assembly/rankers/experiential_ranker.py` — boosts entities cited in similar past queries.
8. [ ] Wire ExperientialRanker into ADR-0065's MultiGraphRetrieval.
9. [ ] Wire VerifierCorrection consultation into ContextAgent prompt builder.
10. [ ] Wire PatternUtility consultation into PatternRanker.
11. [ ] `POST /feedback` endpoint for thumbs_up/down/edit/correction.
12. [ ] Acceptance: 5 tests above PASS.
13. [ ] Telemetry: per-run `query_trajectories_written`, `verifier_corrections_consulted`, `experiential_ranker_contributions`.
14. [ ] Add `THIRD-PARTY-INSPIRATIONS.md` entry per LEGAL-CONTEXTDB-INTEGRATION.md.
