# ADR-0063 — Convention Inference + Augmentation Agent + Adaptive Smart Zone (handling messy repos)

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0055 (cross-file pass), ADR-0056 (verifier), ADR-0058 (schema), ADR-0061 (exploration agent), ADR-0062 (calibration packs), existing SmartZoneAssembler (T0/T1/T2 tiering)
**Sequenced with:** ships AFTER 0055-0061; orthogonal to 0062 (packs assume conventions exist; this ADR handles their absence).

---

## Context

Every prior ADR (0042 through 0062) implicitly assumes the target repo follows SOME convention — controllers are named `*Controller`, services are `*Service`, methods describe their intent, types are meaningful. **Real-world repos routinely violate every one of these.** Concrete failure shapes seen even in "well-engineered" codebases:

- Classes named `Manager`, `Helper`, `Util`, `Foo`, `Stuff`. Methods named `process()`, `handle()`, `run()`, `doWork()`. Variables `x`, `tmp`, `data`. No semantic content in any name.
- Inconsistent naming WITHIN one codebase: `getUserById`, `fetch_user`, `findUserBy_id`, `loadOne(int id)` all pulling from the same table.
- Half the codebase uses `*Repository` for repositories; the other half (older) uses `*DAO`.
- No comments, no docstrings, no README, no tests.
- A controller that doesn't extend any base class, doesn't have a `@RestController` annotation (uses a custom router), is named `Endpoint43`.
- A poorly-typed `Map<String, Object>` parameter with 8 nested keys whose meaning is encoded in a tribal Confluence page.
- Generated code that looks like real code but is meaningless to extract (UI mockups, scaffolded boilerplate).

When the brain hits these, three things go wrong:

1. **Extraction returns hallucinated `purpose` fields.** Sonnet sees `Manager.process(data: Map)` and emits "manages data processing" — vacuous, but plausible-looking. The verifier (ADR-0056) doesn't catch it because the *claim* isn't false; it's just useless.
2. **Smart Zone gets fed garbage signal.** The SmartZoneAssembler retrieves entities by similarity; when names are meaningless, BM25/embedding similarity returns wrong neighbours. Sonnet then synthesises an answer from irrelevant context.
3. **No fallback to external knowledge.** The LLM has no way to say "I don't recognise this method; let me check the DB or the web". The pipeline is single-pass and trust-based even when trust is unwarranted.

The user's framing: *"if LLM doesn't know anything it should reach out to our existing tools from DBs to get the required information and have a merging strategy to add new context. Naming conventions can be bad in repos and we should be able to handle it. We should always stay in the smart zone."*

Four coordinated mechanisms below.

---

## Decision

Four mechanisms that work together. Each can ship independently (parallel-safe), but they unlock the most value combined.

---

### M1 — Convention Inference Pass (measure THIS repo's conventions; flag outliers)

A new pass runs once per `brain index` AFTER structural extraction (Stage 0.5) and BEFORE the cross-file pass (Stage 2.5). Its job: **derive what conventions THIS repo actually uses**, not what conventions an idiomatic Spring/FastAPI/etc. repo would use.

**Algorithm (deterministic, no LLM):**

1. **Naming-pattern fingerprint.** For each entity_type:
   - Tokenize names → token frequency map.
   - Detect dominant case (camelCase / snake_case / PascalCase / SCREAMING / kebab-case).
   - Detect dominant prefix/suffix (`*Controller` 80%, `*Endpoint` 15%, none 5%).
   - Detect verb-noun ordering (`getUserById` vs `userIdGet`).
   - Detect parameter-naming styles (`id` vs `userId`, single-letter `x` ratio).

2. **Outliers, not absolutes.** Flag entities whose name DEVIATES from the repo's own dominant pattern. `Endpoint43` in a repo where 80% of controllers end with `*Controller` is an outlier; `Endpoint43` in a repo where everyone is named that way is normal.

3. **Quality score per entity** (0.0 – 1.0):
   - +0.3 if name matches dominant pattern
   - +0.2 if name has ≥ 3 meaningful tokens (excludes common stopwords / single chars)
   - +0.2 if has docstring / Javadoc / inline comment
   - +0.15 if has at least one test referencing it
   - +0.15 if type signatures are concrete (not `Map<String, Object>` or `Any`)

4. **Per-entity outputs**:
   - `naming_quality_score: float` — how informative is this name?
   - `convention_match: bool` — does it follow the repo's dominant pattern?
   - `outlier_reason: Optional[str]` — "name length 3, repo avg 12; uses snake_case in mostly camelCase repo"

5. **Per-repo outputs** (a `RepoConventions` entity):
   - Dominant patterns per entity_type
   - Confidence in each (how strongly the repo adheres)
   - Top 20 outliers needing extra augmentation

The Convention Inference Pass downstream signals: **anything with `naming_quality_score < 0.4` triggers M2 (Augmentation Agent) instead of trusting the LLM's first guess.**

**Cost: $0** (pure deterministic analysis).

---

### M2 — Augmentation Agent (LLM-uncertain → reach out to tools)

When extraction sees an entity with low `naming_quality_score` OR low `confidence` from initial extraction, spawn an **AugmentationAgent** sub-agent (related to but distinct from ADR-0061's ExplorationAgent — that one serves QUERIES; this one serves EXTRACTION).

**Tool catalog the AugmentationAgent has:**

| Tool | Source | What it returns | Cost |
|---|---|---|---|
| `read_callers(entity_urn, limit=10)` | Brain graph | Up to 10 call-site bodies that call this entity | $0 |
| `read_test_usage(entity_urn)` | Brain graph + ADR-0057 BehavioralSpec | Test methods that exercise this entity, with their `then` clauses | $0 |
| `read_git_history(entity_urn, n=5)` | GitCollector | Last 5 commit messages + diffs that touched this entity | $0 |
| `read_db_schema(table_or_column_name)` | ADR-0058 DatabaseTable/Column | Actual DDL + index info | $0 |
| `query_brain_other_repos(entity_signature)` | Qdrant cross-workspace (ADR-0061 E6) | Similar-shape entities in OTHER previously-extracted repos with their established context | $0 |
| `web_search(query)` | WebFetch | Top-3 results for "$framework_name $entity_pattern" | $0.001 |
| `read_inline_comments(file, line_range)` | Source file | Any comments adjacent to entity | $0 |
| `infer_from_types(entity_urn)` | Tree-sitter + types | Parameter/return types as semantic hints | $0 |
| `ask_llm_with_augmented_context(...)` | The agent's own LLM call with everything above gathered | A revised entity payload | $0.005 |

**Agent loop:**

```
LOOP (capped at 6 tool calls):
  1. Decide which tool gives the most signal for the gap.
     (e.g., "name is meaningless; types are Map<String, Object>" → read_callers
      first to see how it's actually used.)
  2. Call the tool, append result to working memory.
  3. If working memory is now sufficient → call ask_llm_with_augmented_context.
  4. If still uncertain → next tool.
END
```

**Termination guards:**
- Hard cap: 6 tool calls (~$0.005 budget).
- Soft cap: stop when augmented context > 4 KB.
- If still no signal: emit entity with `confidence: low` + `augmentation_status: insufficient_signal` + the working memory as evidence (for humans to review later).

**The killer pattern**: `query_brain_other_repos` — when the brain has previously extracted similar code, even from a totally different customer's repo, the agent can borrow that context. Cross-workspace pattern-matching is what turns the brain from "stateless extractor" into "accumulating institutional knowledge".

**Cost per augmented entity**: ~$0.005-0.01. Triggered for ~5-15% of entities in a messy repo. Total per-run impact: $0.05-0.20.

---

### M3 — Knowledge Merging Strategy (when new context arrives, how do we update the brain?)

When an entity is augmented (or a `brain index` is re-run, or a customer manually edits via `BRAIN.md`), we need a deterministic merge policy. Today, re-extraction overwrites blindly — losing prior context, manual notes, verifier corrections.

**The provenance model**: every fact on an entity carries a source tag.

```python
@dataclass
class FactWithProvenance:
    value: Any
    source: Literal["initial_llm", "augmentation_agent", "verifier_correction",
                    "human_brain_md", "cross_repo_pattern", "db_schema_resolved"]
    confidence: float
    set_at: datetime
    set_by_run_id: str
```

Each `BusinessContext` field becomes `dict[str, FactWithProvenance]` (or for list fields, list of FactWithProvenance — preserving WHO contributed each list item).

**Merge rules per source** (highest precedence first):

1. `human_brain_md` — explicit human override; never overwritten.
2. `verifier_correction` — verifier dropped/changed; preserve unless human overrides.
3. `db_schema_resolved` — pulled from authoritative DDL; trust over LLM guesses for types/constraints.
4. `cross_repo_pattern` — borrowed from another repo's verified context; trust unless conflicting with this repo's structural evidence.
5. `augmentation_agent` — trusted over `initial_llm` from the SAME run.
6. `initial_llm` — baseline; overwritten by anything above.

**Merge operation**:

```python
def merge_fact(existing: FactWithProvenance, new: FactWithProvenance) -> FactWithProvenance:
    # Precedence by source
    if PRECEDENCE[new.source] > PRECEDENCE[existing.source]:
        return new
    if PRECEDENCE[new.source] < PRECEDENCE[existing.source]:
        return existing
    # Same precedence: newer wins, BUT keep both as alt_provenance for audit
    if new.set_at > existing.set_at:
        return FactWithProvenance(
            value=new.value, source=new.source, confidence=new.confidence,
            set_at=new.set_at, set_by_run_id=new.set_by_run_id,
            alt_provenance=[existing],   # preserve history
        )
    return existing
```

**Conflict surfacing**: when two HIGH-PRECEDENCE sources disagree (e.g., human BRAIN.md says "this is a payment gateway" + db_schema_resolved infers it's a logging table), emit a `KnowledgeConflict` entity for human review. Surface in the verification report (ADR-0054).

**Storage update**: Postgres `node_context` table gets a `provenance` JSONB column. Migration V15. Backwards-compat: existing rows treated as `initial_llm` source.

---

### M4 — Adaptive Smart Zone (stay in the zone even when entities are messy)

The current SmartZoneAssembler builds T0/T1/T2 tiers based on the QUESTION's intent. Tier sizes are fixed. Result: when the queried entity has thin context (poor naming, no docs), the model gets thin context. Garbage in → garbage out.

**The change**: zone size becomes a function of (question intent) × (entity quality). Specifically:

```python
def compute_zone(question: str, primary_entities: list[Entity]) -> Zone:
    # Quality of the primary entities being asked about
    avg_quality = mean(e.naming_quality_score for e in primary_entities)

    if avg_quality > 0.7:
        # High-quality entities: tight zone (the existing behaviour)
        return Zone(t0_max=3, t1_max=8, t2_max=10, t2_token_budget=4000)

    elif avg_quality > 0.4:
        # Mid-quality: pull MORE neighbours via callers + similar-name pattern
        return Zone(t0_max=5, t1_max=15, t2_max=20, t2_token_budget=6000,
                    expansion_strategy="bfs_callers_2hops")

    else:
        # Low-quality (Manager/Helper/process()): aggressive expansion
        return Zone(t0_max=8, t1_max=25, t2_max=40, t2_token_budget=8000,
                    expansion_strategy="full_module_dump_plus_test_traces")
```

**Three expansion strategies**:

1. **`tight`** — current behaviour. Used when the question is specific and entities are well-named.
2. **`bfs_callers_2hops`** — for medium-quality, walk 2 hops of CALLERS edges from the primary entity. Caller bodies often disambiguate poorly-named callees.
3. **`full_module_dump_plus_test_traces`** — for low-quality, include EVERY entity in the same module (capped at 40), PLUS every BehavioralSpec entity that references any of them. Tests are often the only signal of intent in messy code.

**Critical guardrail**: zone token budget is HARD-CAPPED. The expansion strategies allow MORE entities but with shorter T1 summaries (more breadth, less depth). The model never gets > 8000 tokens of zone context regardless. This stays inside the smart-zone discipline; it just spreads the budget differently for messy repos.

**The "stay in the zone" guarantee**:

- Tier 0 (must-include) is always ≤ 8 entities — even in low-quality mode, only 8 entities are guaranteed in full.
- Tier 1 (summaries) scales up but each entry is compressed to ~50 tokens.
- Tier 2 (full context for top-K) cap shrinks per-entity as count grows: 40 entities × 200 tokens each instead of 10 × 800.

Total token budget is constant. Composition adapts.

---

## Composition: how M1-M4 chain together

```
1. Convention Inference (M1)        ← runs once per `brain index`
       ↓
   labels every entity with naming_quality_score
       ↓
2. Initial extraction (existing)
       ↓
3. Per-entity decision:
   if naming_quality_score < 0.4 OR llm_confidence < 0.6:
       → spawn Augmentation Agent (M2)
       ↓
4. M2 augments → produces FactWithProvenance updates
       ↓
5. Knowledge Merging (M3) writes new facts respecting precedence
       ↓
6. Cross-file pass (ADR-0055), Verifier (ADR-0056), etc. operate on
   the augmented entities
       ↓
7. At /query time:
   primary entities looked up
   compute_zone (M4) decides expansion based on their quality scores
   smart zone built within constant token budget
       ↓
8. Sonnet answers from zone
```

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/convention_inference.py        # NEW — M1 main
company-brain-ai/src/companybrain/pipeline/naming_pattern_fingerprint.py  # NEW — M1 helper
company-brain-ai/src/companybrain/pipeline/quality_scorer.py              # NEW — M1 score calculation

company-brain-ai/src/companybrain/agents/augmentation_agent.py            # NEW — M2 main
company-brain-ai/src/companybrain/agents/tools/augmentation/              # NEW directory
company-brain-ai/src/companybrain/agents/tools/augmentation/{read_callers,read_tests,read_git,read_db_schema,query_other_repos,web_search,read_comments,infer_types}.py

company-brain-ai/src/companybrain/store/provenance.py                     # NEW — M3 FactWithProvenance dataclass + merge
company-brain-ai/src/companybrain/store/knowledge_merger.py               # NEW — M3 orchestrator
db/migrations/V15__node_context_provenance.sql                             # NEW

company-brain-ai/src/companybrain/assembly/adaptive_zone.py               # NEW — M4 main
company-brain-ai/src/companybrain/assembly/expansion_strategies.py        # NEW — M4 helpers

tests/unit/test_convention_inference.py                                     # NEW
tests/unit/test_augmentation_agent.py                                       # NEW
tests/unit/test_knowledge_merger.py                                         # NEW
tests/unit/test_adaptive_zone.py                                            # NEW
tests/acceptance/test_messy_repo_handling.py                                # NEW
fixtures/sample-messy-repo/                                                 # NEW — fixture with bad naming
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py        # add naming_quality_score, convention_match, outlier_reason, augmentation_status, KnowledgeConflict, RepoConventions
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke M1 after Stage 0.5; route low-quality entities to M2
company-brain-ai/src/companybrain/assembly/smart_zone.py    # delegate to adaptive_zone for compute_zone
company-brain-ai/src/companybrain/api/routes/query.py       # surface augmentation_status in response
company-brain-ai/src/companybrain/store/postgres_store.py   # write/read provenance JSONB
company-brain-ai/src/companybrain/config.py                 # quality_threshold, augmentation_max_calls, zone_expansion_strategies
```

Does NOT touch ADR-0055/56/57/58/59/60/61/62 owned files.

---

## Acceptance test (the messy-repo smoke test)

`fixtures/sample-messy-repo/` contains a small synthetic repo with:
- A controller `Endpoint17.java` (no @RestController; custom router; vague name)
- A service `Manager.java` with method `process(Map<String,Object> data)` and zero comments
- A repo class `Foo.java` with method `doIt()` that runs jOOQ DSL against `xyz_table`
- One test `EndToEndIT.java` that exercises the full path with descriptive `@Test` names
- A README.md that describes the actual business purpose ("payments reconciliation service")
- An `application.yml` with `app.payment.gateway-url`

```python
async def test_low_quality_entity_triggers_augmentation():
    """Manager.process() has score < 0.4; augmentation must fire."""
    await run_pipeline_harness(repo="fixtures/sample-messy-repo")
    bc = await brain_get_context("Manager.process")
    assert bc.augmentation_status == "applied"
    assert bc.naming_quality_score < 0.4
    # The agent should have used read_test_usage and read_callers
    assert "read_test_usage" in bc.augmentation_tools_used


async def test_augmented_purpose_borrows_from_test_intent():
    """The test's @Test name says 'reconcile_payment_returns_settled'.
    The augmented purpose for Manager.process should mention reconciliation."""
    bc = await brain_get_context("Manager.process")
    assert any(kw in bc.purpose.lower() for kw in ("reconcile", "payment", "settle"))


async def test_db_schema_resolution_overrides_llm_type_guess():
    """Foo.doIt operates on xyz_table. The DDL says xyz_table.amount is NUMERIC(10,2).
    LLM guessed 'string'. After M3 merge, type should be NUMERIC(10,2)."""
    column = await brain_get("xyz_table.amount")
    assert column.type == "NUMERIC(10,2)"
    assert column.provenance["type"]["source"] == "db_schema_resolved"


async def test_human_brain_md_wins_over_extraction():
    """BRAIN.md says 'Manager.process is the legacy reconciliation entry point'.
    Re-running brain index must NOT overwrite this."""
    # Setup: write to BRAIN.md
    Path("fixtures/sample-messy-repo/.brain/BRAIN.md").write_text(
        "## Manager.process\nLegacy reconciliation entry point. Do not refactor.\n"
    )
    await run_pipeline_harness(repo="fixtures/sample-messy-repo")
    bc = await brain_get_context("Manager.process")
    assert "Legacy reconciliation entry point" in bc.purpose
    assert bc.provenance["purpose"]["source"] == "human_brain_md"


async def test_adaptive_zone_expands_for_messy_query():
    """Asking about Manager.process should pull a wider zone because the
    primary entity has score 0.3."""
    response = await brain_query("what does Manager.process do?")
    assert response.telemetry["zone_expansion_strategy"] == "full_module_dump_plus_test_traces"
    assert response.telemetry["zone_entity_count"] >= 15
    # Token budget still bounded
    assert response.telemetry["zone_input_tokens"] < 8000


async def test_cross_repo_pattern_borrows_context():
    """When Manager.process matches a previously-extracted entity in
    another repo (e.g., a previous customer's PaymentReconciler), the
    augmentation agent should borrow context."""
    # seed: extract a "well-named" repo first; verify cross-repo lookup
    # works on the messy repo
    await run_pipeline_harness(repo="fixtures/sample-well-named-payments")
    await run_pipeline_harness(repo="fixtures/sample-messy-repo")
    bc = await brain_get_context("Manager.process")
    assert any(p["source"] == "cross_repo_pattern" for p in bc.provenance.values())


async def test_knowledge_conflict_surfaces():
    """When BRAIN.md says X but db_schema says Y, emit KnowledgeConflict."""
    Path("fixtures/sample-messy-repo/.brain/BRAIN.md").write_text(
        "## xyz_table.amount\nThis is a string column for human-readable display.\n"
    )
    await run_pipeline_harness(repo="fixtures/sample-messy-repo")
    conflicts = await brain_query("KnowledgeConflict entities")
    assert len(conflicts) >= 1
    assert "xyz_table.amount" in conflicts[0].entity_urn
```

---

## Trade-off analysis

The honest trade-offs:

| Concern | Impact | Mitigation |
|---|---|---|
| Augmentation costs $0.05-0.20 per messy run | Real but bounded | Triggered only for 5-15% of entities; hard cap on tool calls |
| Augmentation can pull MISLEADING context | Real risk | Provenance model surfaces the source; verifier (ADR-0056) re-checks |
| Cross-repo borrowing leaks customer A's patterns to customer B | Potentially serious | Strict workspace_id scoping in `query_brain_other_repos`; only applies to FACT TYPES (not raw text); customer can opt out via setting |
| Adaptive zone expansion increases query cost slightly | ~10% query cost increase on messy repos | Token budget cap is constant; only entity COUNT in zone grows |
| Provenance model adds storage overhead | ~30% increase in node_context table size | Acceptable; JSONB indexes are cheap |
| Convention inference might mis-fingerprint a tiny repo | Low signal in repos with < 20 entities of a type | Skip M1 outlier flagging when sample size < threshold |

The net trade is: **+$0.20 cost on messy repos buys you usable extraction instead of garbage**. For a customer with mixed-quality codebases (most enterprises), this is the difference between "the brain works on our pretty modules" and "the brain works on our actual codebase".

---

## Effort estimate

5 days, parallelisable to ~3 calendar days with 3 sessions:

| Workstream | Days |
|---|---|
| M1 — Convention Inference Pass | 1 |
| M2 — Augmentation Agent + 8 tools | 2 |
| M3 — Provenance + merging + V15 migration | 1 |
| M4 — Adaptive Smart Zone | 1 |

---

## Action items

1. [ ] `pipeline/naming_pattern_fingerprint.py` — token frequency + case detection + prefix/suffix detection per entity_type.
2. [ ] `pipeline/quality_scorer.py` — 5-component score; emit `naming_quality_score` per entity.
3. [ ] `pipeline/convention_inference.py` — orchestrate M1; emit `RepoConventions` entity.
4. [ ] `agents/augmentation_agent.py` — sub-agent with 6-call cap.
5. [ ] 8 augmentation tools under `agents/tools/augmentation/`.
6. [ ] `store/provenance.py` — `FactWithProvenance` dataclass + merge function.
7. [ ] `store/knowledge_merger.py` — orchestrator; conflict detection.
8. [ ] V15 migration: `node_context.provenance JSONB`.
9. [ ] `assembly/adaptive_zone.py` + `expansion_strategies.py`.
10. [ ] `models/entities.py` — append: naming_quality_score, convention_match, outlier_reason, augmentation_status, augmentation_tools_used, KnowledgeConflict, RepoConventions.
11. [ ] Wire M1 into orchestrator (post Stage 0.5).
12. [ ] Wire M2 trigger into orchestrator (post initial extraction; per-entity decision).
13. [ ] Wire M3 into all entity-write paths.
14. [ ] Wire M4 into SmartZoneAssembler.
15. [ ] Build `fixtures/sample-messy-repo` and `fixtures/sample-well-named-payments`.
16. [ ] Acceptance suite (7 tests above).
17. [ ] Telemetry: per-run `augmentations_fired`, `cross_repo_borrows`, `knowledge_conflicts_surfaced`, `zone_expansion_strategies_used` distribution.

---

## Companion implementation prompt

`SONNET-IMPLEMENTATION-PROMPT-ADR-0063.md` (write next if you confirm). Will sequence: (a) M3 provenance + V15 first (foundation everything else writes through); (b) M1 + M4 in parallel (pure additions); (c) M2 last (depends on M3 storage path); (d) acceptance gates.
