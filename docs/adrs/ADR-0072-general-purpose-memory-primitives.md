# ADR-0072 — General-Purpose Memory Primitives (the bridge from code-memory to general-agent-memory)

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0063 (provenance + augmentation), ADR-0066 (ExperientialMemory), ADR-0067 (evolution background process)
**Strategic goal:** add the missing 30-40% of primitives that would let our brain extend from CODE memory to GENERAL agent memory, per MEMORY-MATURITY.md gap analysis.

---

## Context

MEMORY-MATURITY.md audited company-brain against the 12 hard problems of general AI memory. Composite score: **60% solved for code-context, 40% for general agent memory**. The two biggest gaps:

- **#3 Selective forgetting** — score 3/10 for code, 2/10 for general
- **#7 Updating memory safely** — score 5/10 for code, 2/10 for general

Plus three smaller-but-real gaps in **#8 long-term consistency**, **#9 memory-distraction guard**, and the temporary-vs-permanent distinction.

Today the brain treats every extracted entity as forever-true at extraction time. There's no mechanism for:
- Detecting that a new memory CONTRADICTS an existing one
- Scoring how SALIENT a memory is right now (vs 6 months ago)
- Declaratively setting "this class of memory expires after X" beyond the simple typed-TTL of ADR-0064
- Asserting and CHECKING invariants the brain maintains over time
- Detecting when retrieving more context HURTS the answer (distraction guard)
- Classifying "is this memory temporary or permanent?" at write time

These are domain-agnostic primitives. Code memory gets them mostly for free (code rarely contradicts itself; pruning a method is a structural delete). General memory NEEDS them. Building them now means: when we extend the brain to legal / medical / scientific / consumer-agent verticals, the architecture is ready instead of needing a rewrite.

This ADR is **strategic-bet shape**: don't ship before you have a clear horizontal expansion thesis (Series A); but design the brain TODAY so the bet is buildable when you make it.

---

## Decision

Six new mechanisms, each a domain-agnostic primitive that fits cleanly into the existing brain architecture. All shippable as parallel sessions; all parallel-safe with existing in-flight ADRs.

---

### M1 — Contradiction Detector

**Problem solved**: #7. *User says "favorite DB = Postgres" then later "favorite DB = ClickHouse". Today the brain overwrites silently. Should it?*

**Mechanism**: at write time for any new memory, run a similarity check against existing memories of the same `entity_urn` AND/OR same `topic_signature`. If a high-similarity match exists with conflicting `value` field, surface as a `Contradiction` entity with structured choice resolution:

```python
@dataclass
class Contradiction:
    entity_type: str = "Contradiction"
    affected_urn: str            # e.g., "user_pref:db"
    prior_memory: FactWithProvenance
    new_memory: FactWithProvenance
    detected_at: datetime
    resolution: Literal[
        "pending",               # awaiting human or policy
        "overwrite_kept",        # newer wins
        "both_kept",             # versioned coexist
        "decay_applied",         # old downweighted, both indexed
        "human_clarified",       # user picked one
    ]
    resolution_policy_id: Optional[str]   # if auto-resolved by policy
```

**Resolution strategies** (declarative, customer-configurable):

```python
class ResolutionStrategy(Enum):
    NEWER_WINS = "newer_wins"             # default for code: latest extraction
    HIGHER_CONFIDENCE_WINS = "higher_confidence_wins"
    HUMAN_OVERRIDE_WINS = "human_override_wins"  # respect human_brain_md per ADR-0063
    KEEP_BOTH_VERSIONED = "keep_both_versioned"  # both indexed; query gets latest by default
    DECAY_OLD_KEEP_BOTH = "decay_old_keep_both"  # old gets 0.3× weight after 30d
    ASK_USER = "ask_user"                 # surface in UI; block writes until resolved
```

**Per-entity-type defaults**:
- Code entities: `NEWER_WINS` (code changes; latest is truth)
- User preferences: `ASK_USER` (the user is authoritative)
- Configuration: `HIGHER_CONFIDENCE_WINS` (DDL beats LLM guess)
- Domain knowledge: `KEEP_BOTH_VERSIONED` (e.g., scientific facts can be revised)

### M2 — Salience Scorer + Decay Function

**Problem solved**: #3. *"Last week I was in Tokyo" — true but not relevant today. The brain remembers; relevance fades.*

**Mechanism**: each memory gets a `salience: float ∈ [0.0, 1.0]` recomputed nightly via a decay function:

```python
def compute_salience(memory) -> float:
    # Base score: how strongly this was retrieved last 30 days
    retrieval_score = log(1 + queries_citing_this_in_30d) / log(100)

    # Recency boost: more recent writes more salient
    age_days = (now - memory.last_updated).days
    recency = exp(-age_days / 90)  # half-life 90 days

    # Type-class baseline (some memory classes are inherently long-lived)
    baseline = TYPE_BASELINES.get(memory.entity_type, 0.3)
    # Code: 0.8 (long-lived); user preferences: 0.6; ephemeral notes: 0.2

    # Human override: pinned memories (per ADR-0052 P6) never decay
    if memory.pinned:
        return 1.0

    # User-explicit forgetting: zero
    if memory.flagged_forget:
        return 0.0

    return min(1.0, baseline + 0.5 * retrieval_score + 0.3 * recency)
```

**Used in retrieval**: SmartZoneAssembler and the RRF fusion (ADR-0065) multiply each candidate's RRF score by its salience. Low-salience memories sink in rankings; very-low-salience memories drop from results entirely (configurable threshold).

**Used in pruning**: ADR-0067 evolution prune step adds a rule: drop memories with `salience < 0.1` AND `age_days > 60` AND `not pinned` AND `no inbound edges`. Cleans the long tail of irrelevance.

### M3 — Declarative Forgetting Policies

**Problem solved**: #3 + #4. *Today every entity_type has hardcoded TTL (ADR-0064). Customers want to declare "for OUR org, code is 7y but customer-name PII expires in 24h."*

**Mechanism**: `forgetting_policies.yaml` per workspace (in `.brain/`):

```yaml
# .brain/forgetting_policies.yaml
policies:
  - name: "PII tight retention"
    matches:
      entity_type: "*"
      pii_categories: ["email", "ssn", "credit_card"]
    retention_days: 1
    require_audit_event: true       # always log deletion

  - name: "Code immortal"
    matches:
      entity_type: "Method"
      verified: "confirmed"
    retention_days: 2555             # 7 years
    pruning_priority: low

  - name: "Hallucinated short window"
    matches:
      verified: "hallucinated"
    retention_days: 30

  - name: "Test fixtures"
    matches:
      file_path_glob: "**/test/**"
    retention_days: 90

  - name: "User-pinned forever"
    matches:
      pinned: true
    retention_days: never
    pruning_priority: never_prune

  - name: "Deprecation override"
    matches:
      tags: ["deprecated"]
    retention_days: 180              # 6 months grace period

defaults:
  retention_days: 365
  on_conflict: "shortest_wins"        # if multiple policies match, pick most aggressive
```

Loaded at brain startup; reload on file change. Policies override the hardcoded defaults from ADR-0064. **Auditable, version-controlled, customer-curated.**

### M4 — Identity / Causal / Behavioral Invariants

**Problem solved**: #8. *Agent remembers a startup idea, forgets the constraints, contradicts prior architecture decisions. Need INVARIANTS the brain enforces over time.*

**Mechanism**: a new entity type `Invariant` that asserts a fact the brain checks periodically:

```python
@dataclass
class Invariant:
    entity_type: str = "Invariant"
    name: str                          # "all customer_id fields are UUID v4"
    statement: str                     # natural-language assertion
    check_query: Optional[str]         # optional: structured query that should return TRUE
    check_kind: Literal[
        "structural",                  # assertion on graph shape (e.g., all Methods have ApiEndpoint inbound)
        "factual",                     # assertion on entity values (e.g., all customer_id types = uuid)
        "behavioral",                  # assertion on agent actions (e.g., agent never deletes pinned memories)
    ]
    last_checked: datetime
    last_status: Literal["holds", "violated", "unverifiable"]
    violations: list["InvariantViolation"]
    severity: Literal["INFO", "WARN", "CRITICAL"]
```

**Three types serve different needs**:

- **Structural** (we have partial via ADR-0055 SharedInvariant): "all reads of plan_info filter is_current=true"
- **Factual**: "all `customer_id` columns are `UUID` type" — verifiable via brain.query
- **Behavioral**: "the agent never overwrote a `human_brain_md`-sourced fact" — verifiable via audit log

**Periodic checking**: ADR-0067 evolution background process gets a 4th sub-pass: `invariant_checker.py`. Runs nightly. Violations surface as `InvariantViolation` entities + alerts in the UI + `KnowledgeConflict` entries (per ADR-0063).

**For agent-OS-shaped use** (future): an agent that wakes up after 6 months can `brain.invariants.list_violated()` to know what's drifted since last session. **This is the "behavioral consistency" the user named.**

### M5 — Memory Distraction Guard (retrieval-time relevance filter)

**Problem solved**: #9. *Too much retrieval HURTS reasoning — model gets distracted, anchored to stale info, less generalizable.*

**Mechanism**: after RRF retrieval (ADR-0065), before passing to the LLM, run a deterministic + LLM-hybrid relevance filter:

```python
def distraction_guard(query: str, candidates: list[Entity]) -> list[Entity]:
    # 1. Deterministic filter: drop candidates with salience < 0.2 unless human-pinned
    candidates = [c for c in candidates if c.salience >= 0.2 or c.pinned]

    # 2. Per-candidate utility prediction (cheap Haiku call):
    #    "Given query Q, would entity E *help* the answer? YES/NO/MAYBE"
    #    Run in single batch; cap at 20 candidates.
    utility = batch_predict_utility(query, candidates[:20])

    # 3. Drop candidates where utility=NO; keep MAYBE only if no other YES candidates
    yes = [c for c, u in zip(candidates, utility) if u == "YES"]
    maybe = [c for c, u in zip(candidates, utility) if u == "MAYBE"]
    return yes if len(yes) >= 5 else (yes + maybe)
```

**Cost**: ~$0.001 per query. **Saves**: cleaner context → better answers + cheaper Sonnet generation (less tokens to process).

**Empirical validation gate**: ship behind feature flag; A/B test 100 queries with vs without; only enable by default if measured answer quality (per ADR-0066 user feedback) IMPROVES.

### M6 — Temporary-vs-Permanent Classifier

**Problem solved**: #7. *"I'll be in Tokyo this week" should expire; "I live in Tokyo" shouldn't. Today the brain has no clue which is which.*

**Mechanism**: at write time, a small classifier (Haiku, $0.0005 per call) predicts the memory's `lifetime_class`:

```python
class LifetimeClass(Enum):
    EPHEMERAL = "ephemeral"        # ≤ 1 day (e.g., "running this command now")
    SHORT_TERM = "short_term"      # 1-30 days (e.g., "I'm at conference X this week")
    MEDIUM_TERM = "medium_term"    # 30-365 days (e.g., "currently working at company X")
    LONG_TERM = "long_term"        # > 1 year, but mutable (e.g., "favorite color")
    PERMANENT = "permanent"        # never expires (e.g., "born in 1990")
    SYSTEM_FACT = "system_fact"    # not user-specific (e.g., "AWS region us-east-1 is in Virginia")
```

**Classifier prompt** (one-shot):

```
Classify this memory's lifetime:
"I'll be in Tokyo for a conference next week"
→ SHORT_TERM (Tokyo trip ≤ 30 days)

"I live in Tokyo"
→ MEDIUM_TERM (residence; can change but unlikely soon)

"I was born in Tokyo"
→ PERMANENT (immutable historical fact)

"What's the meeting agenda for tomorrow?"
→ EPHEMERAL (next-day-only relevance)

Memory to classify: {{memory.value}}
Returns: one of {EPHEMERAL, SHORT_TERM, MEDIUM_TERM, LONG_TERM, PERMANENT, SYSTEM_FACT}
```

**Used by**: M3 declarative forgetting policies + M2 salience scoring (decay rate per class).

**For code (today's domain)**: most code entities classify as MEDIUM_TERM or LONG_TERM. Configuration entities are MEDIUM_TERM. Generated code is SHORT_TERM. Test fixtures are SHORT_TERM. The classifier is mostly for the GENERAL memory expansion (Product 4 / Series B).

---

## File ownership for THIS PR (parallel-safe with all existing ADRs)

```
company-brain-ai/src/companybrain/memory/general/                        # NEW DIRECTORY
company-brain-ai/src/companybrain/memory/general/contradiction_detector.py    # M1
company-brain-ai/src/companybrain/memory/general/resolution_strategies.py    # M1
company-brain-ai/src/companybrain/memory/general/salience.py                  # M2
company-brain-ai/src/companybrain/memory/general/forgetting_policies.py      # M3
company-brain-ai/src/companybrain/memory/general/invariant_checker.py        # M4
company-brain-ai/src/companybrain/memory/general/distraction_guard.py        # M5
company-brain-ai/src/companybrain/memory/general/lifetime_classifier.py      # M6

db/migrations/V19__memory_primitives.sql                                       # NEW

tests/unit/test_contradiction_detector.py
tests/unit/test_salience_decay.py
tests/unit/test_forgetting_policies.py
tests/unit/test_invariant_checker.py
tests/unit/test_distraction_guard.py
tests/unit/test_lifetime_classifier.py

tests/acceptance/test_memory_primitives_e2e.py
fixtures/sample-general-memory/                                                 # NEW — non-code memory fixtures (user prefs, conversations, agent traces)
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py        # Contradiction, Invariant, InvariantViolation entity types + LifetimeClass enum
company-brain-ai/src/companybrain/store/postgres_store.py   # write Contradiction on detection; salience field on every entity
company-brain-ai/src/companybrain/assembly/multi_graph_retrieval.py  # invoke distraction_guard after RRF
company-brain-ai/src/companybrain/pipeline/brain_evolution.py  # add invariant_checker as 4th sub-pass
company-brain-ai/src/companybrain/api/routes/admin.py       # NEW: /api/v2/contradictions, /api/v2/invariants endpoints
config.py                                                    # tunables: salience_decay_half_life_days, distraction_min_salience, etc.
```

---

## Acceptance test

```python
async def test_contradiction_detected_on_conflicting_facts():
    """Add two memories about the same entity_urn with conflicting values;
    Contradiction entity must be created."""
    await brain.write_fact("user:42:db_pref", "Postgres", source="initial_llm")
    await brain.write_fact("user:42:db_pref", "ClickHouse", source="initial_llm")
    contradictions = await brain.query("Contradiction where affected_urn='user:42:db_pref'")
    assert len(contradictions) == 1
    assert contradictions[0].resolution in ("pending", "overwrite_kept", "decay_applied")


async def test_salience_decay_lowers_old_memory_ranking():
    """A memory created 90 days ago with no recent retrieval should rank
    lower than a fresh similar memory."""
    old = await create_memory(content="X", days_ago=90)
    new = await create_memory(content="X")
    ranking = await brain.search("X")
    assert ranking[0].urn == new.urn  # newer wins via salience


async def test_forgetting_policy_drops_pii_after_1_day():
    """A memory with a detected email and PII policy retention_days=1 is
    deleted by the next evolution cycle."""
    await create_memory(content="contact alice@example.com", pii_categories=["email"])
    await advance_clock(days=2)
    await run_evolution_cycle()
    assert await brain.query("memory containing alice@example.com") == []


async def test_invariant_violation_detected():
    """Define an invariant; add a memory that violates it; checker catches it."""
    await brain.invariants.add(name="all customer_ids are UUIDv4",
                                check_query="entities where type=customer_id and not regex_match('^[0-9a-f-]+$')")
    await create_memory(entity_type="customer_id", content="abc123")  # violates
    await run_invariant_checker()
    violations = await brain.query("InvariantViolation since=last_hour")
    assert len(violations) == 1


async def test_distraction_guard_drops_low_utility_candidates():
    """Add 20 candidates of which 15 are clearly irrelevant to the query;
    distraction guard drops them before LLM."""
    candidates = make_mixed_candidates(query="lob column rename", n=20, relevant=5)
    filtered = await distraction_guard(query="...", candidates=candidates)
    assert len(filtered) <= 7   # 5 YES + maybe 2 MAYBE


async def test_lifetime_classifier_distinguishes_temp_from_permanent():
    """Tokyo conference vs Tokyo residence vs born in Tokyo."""
    assert await classify_lifetime("I'll be in Tokyo next week") == "SHORT_TERM"
    assert await classify_lifetime("I live in Tokyo") == "MEDIUM_TERM"
    assert await classify_lifetime("I was born in Tokyo") == "PERMANENT"
```

---

## Effort estimate

3 weeks total (15 person-days), parallelisable to 5-6 days with 6 sessions:

| Session | Mechanism | Days |
|---|---|---|
| A | M1 Contradiction Detector | 3 |
| B | M2 Salience Scorer + Decay | 2 |
| C | M3 Declarative Forgetting Policies | 2 |
| D | M4 Invariant Checker | 3 |
| E | M5 Distraction Guard | 2 |
| F | M6 Lifetime Classifier | 1 |
| G | V19 migration + integration glue | 2 |

---

## Sequencing recommendation

**Don't ship before Series A.** Per MEMORY-MATURITY.md, the strategic move is:
1. Ship Wave 0-3 of IMPLEMENTATION-ORDER-V3 (already covered) — 3-4 weeks
2. Demo + close seed round
3. THEN ship ADR-0072 (this) as a Series-A-prep architectural investment

The risk of shipping now: it doesn't help the SEED demo (code memory wins seed; general memory wins Series B). Spending ~3 weeks on this before the seed demo means delaying the demo for no investor value.

The risk of NOT shipping it before Series A: at Series A, investors ask *"how does this generalize beyond code?"* and you don't have a credible architectural answer. **Ship right after seed close, before Series A pitch.** That's the optimal window.

---

## Strategic value

After M1-M6 land, company-brain isn't a "code memory tool" anymore — it's a **memory infrastructure** that handles structured facts with provenance, contradictions, decay, invariants, and lifetime classification. **Code is just one application.** That positioning unlocks:

- **Legal contract memory** vertical (clauses + edits + invariants like "this contract has no auto-renewal clause")
- **Medical records** vertical (patient state + contradictions + decay)
- **Scientific paper** memory (claim graphs + retraction handling = contradiction)
- **Compliance documentation** (policies + invariants + audit chain — already strong)
- **Personal AI companion** (lifetime classification + decay + forgetting policies = exactly what consumer agent memory needs)
- **Multi-agent coordination** (shared memory across agents with consistency invariants)

That portfolio is a Series-B story. **Without these primitives, company-brain is "code memory."** With them, it's "memory infrastructure for AI."

The user's framing in the prompt — *"the next major leap after reasoning is memory"* — this ADR is what positions us to play in that leap.

---

## Action items

1. [ ] `memory/general/contradiction_detector.py` + resolution strategies (M1)
2. [ ] `memory/general/salience.py` decay function (M2)
3. [ ] `memory/general/forgetting_policies.py` YAML loader + matcher (M3)
4. [ ] `memory/general/invariant_checker.py` + 3 invariant types (M4)
5. [ ] `memory/general/distraction_guard.py` Haiku-batched utility predictor (M5)
6. [ ] `memory/general/lifetime_classifier.py` 6-class classifier (M6)
7. [ ] V19 migration: contradictions, invariants, invariant_violations, salience field on entities, lifetime_class on entities
8. [ ] Wire M5 into ADR-0065's MultiGraphRetrieval (post-RRF, pre-zone-build)
9. [ ] Wire M4 invariant_checker into ADR-0067 evolution as 4th sub-pass
10. [ ] Integrate M1 contradictions into ADR-0063's KnowledgeConflict UI surface
11. [ ] Per-mechanism acceptance tests (6 above)
12. [ ] End-to-end test: a non-code memory fixture (user-preferences scenario) flows through the full primitives stack
13. [ ] Telemetry: per-cycle `contradictions_detected`, `salience_decay_drops`, `invariant_violations`, `distraction_guard_filtered_count`

---

## Companion implementation prompt

A `SONNET-IMPLEMENTATION-PROMPT-ADR-0072.md` will follow when scheduled (post-seed). Sequenced: M1 + M2 first (foundation); then M3 + M4 (build on salience); then M5 + M6 (refinements). 5-6 parallel sessions over ~5 working days.

---

## Closing thought

The honest version of "where company-brain stands": we solve a fraction of the general-memory problem brilliantly (code) and have credible plans for most of the rest. ADR-0072 is what closes the credibility gap. **Ship it after seed close, before Series A. Don't ship it before. Don't skip it.**
