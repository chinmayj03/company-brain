# ADR-0073 — Event-Stream Memory: Continuous, Branch-Aware, Causal-Temporal, Salience-Adaptive, Federated

**Status:** Proposed
**Date:** 2026-05-13
**Strategic significance:** the largest architectural ADR proposed. Unifies five problems the brain WILL hit at scale: staleness, branch divergence, multi-repo federation, causal/temporal reasoning, and noise-at-scale. Without these, the brain becomes the documentation we set out to replace.
**Sequenced:** designed POST-seed, before Series B. The 6 ADRs of W1-W3 (0055-0067) make the brain CORRECT for code-context. This ADR makes the brain SCALE.

---

## Context

The brain today is a **static snapshot at time T**. You run `brain index`; it captures the codebase state; queries answer based on that snapshot until you re-index. Five fundamental problems with this shape, each catastrophic at scale:

### Problem 1 — Memory decays exactly like the documentation it replaces

A 50-engineer org pushes ~100 commits/day. The brain captured Monday morning is wrong by Tuesday lunchtime. By Friday it's lying. Within a quarter it's worse than no brain at all (because users TRUST it). **This is the Confluence death spiral applied to AI memory.**

### Problem 2 — Branch divergence

A developer working on `feature/payments-rename` has different code than a colleague on `feature/auth-refactor`. Both want the brain to answer questions about THEIR branch's code, not the merged main. Today the brain only knows main (or whichever branch was last extracted). Pre-PR memory is impossible.

### Problem 3 — Multi-repo doesn't scale

A 2-repo company already has a sync problem. A 50-repo company is a coordination nightmare. Sourcegraph's lesson: federated indexing is the only way. Today our brain runs per-repo; cross-repo queries don't compose; sync is manual.

### Problem 4 — Causal and temporal reasoning are missing

User asks: *"Why does the lob column exist?"* — that's a causal question. *"What changed last Tuesday?"* — temporal. *"Show me the timeline of how the payments handler evolved."* — both.

Today's brain has CALLS / READS_COLUMN edges (mechanical relationships). It does NOT have:
- **Causal edges**: PR → CodeChange → Incident → Mitigation
- **Temporal range queries**: "entities that existed between dates D1 and D2"
- **Event timelines**: ordered sequence of changes affecting an entity
- **Counterfactuals**: "what would the brain have said 3 months ago?"

These are explicitly listed by the user as bigger problems than staleness. They are.

### Problem 5 — Noise at scale destroys reasoning

50 repos × 100K entities each = 5M entities. Even with perfect retrieval quality (90% precision), 10% noise on 5M memories floods the LLM context. **Distraction from irrelevant correct memories** becomes the dominant failure mode at scale (worse than missing relevant memories).

ADR-0072 M5 (distraction guard) helps but only at the FILTERING layer. The real fix is upstream: don't load 5M entities at retrieval time; load 50.

### Problem 6 — Salience is query-dependent and time-dependent

ADR-0072 M2 proposed time-decay salience. But the harder version: salience is **per-query-context** AND **per-current-time**. The same memory is salient when querying about payments and irrelevant when querying about auth. *"Currently working on payments"* should boost payments memories for THIS USER for THIS WEEK.

---

## Decision

**Stop treating the brain as a static snapshot. Make it a streaming event log with materialized views.**

This is the architectural shift used by event-sourcing systems, modern data lakehouses (Snowflake, Iceberg), and time-series databases. The brain becomes:

```
┌──────────────────────────────────────────────────────────────────┐
│ EVENT STREAM (append-only, partitioned by repo+branch)          │
│ ─ GitCommit / PROpened / PRMerged / Deploy / Incident /         │
│   IncidentResolved / HumanFact / QueryAsked / VerifierCorrection /│
│   AgentAction / SchemaMigration / ConfigChange                   │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼ derived via streaming projections
┌──────────────────────────────────────────────────────────────────┐
│ MATERIALIZED VIEWS (per query intent)                           │
│  ─ EntityState(urn, branch, at_time)                            │
│  ─ CausalChain(entity_urn) → ordered events                     │
│  ─ SalienceScore(urn, query_context, time) → 0–1                │
│  ─ TimelineWindow(urn, since, until) → state diffs              │
│  ─ BranchOverlay(branch, base_branch) → fact deltas             │
│  ─ FederatedIndex(repo[]) → cross-repo entity routing           │
└──────────────────────────────────────────────────────────────────┘
```

Eight mechanisms, each addressing one of the problems above. All shippable independently; together they are the brain-at-scale architecture.

---

### M1 — Event-Sourced Storage (the foundation)

Replace today's "entities are records that get UPDATED" with "entities are derived from an append-only event log." Every change to the brain — git commit, PR merge, human edit via BRAIN.md, verifier correction, schema migration — is an EVENT.

```python
@dataclass(frozen=True)
class BrainEvent:
    id: str                          # UUID
    workspace_id: str
    repo: str                        # which repo this event is in
    branch: str                      # which branch (None for cross-branch events)
    event_type: Literal[
        # Code lifecycle
        "GitCommit", "PROpened", "PRMerged", "PRClosed", "BranchCreated", "BranchDeleted",
        # Deployment lifecycle
        "Deploy", "Rollback", "ConfigChange", "SchemaMigration",
        # Incident lifecycle
        "IncidentDeclared", "IncidentMitigated", "IncidentResolved", "PostmortemPublished",
        # Human / agent actions
        "HumanFactWritten", "AgentAction", "VerifierCorrection", "QueryAsked", "FeedbackGiven",
        # External events
        "ExternalDocChange",  # Notion/Confluence updated
    ]
    payload: dict                    # event-specific structured data
    occurred_at: datetime            # when it happened in the world
    recorded_at: datetime            # when the brain saw it
    causal_parents: list[str]        # event IDs that caused this one
    actors: list[str]                # who/what triggered (humans, agents, systems)
```

Storage:
- **Hot path**: Postgres `brain_events` partitioned by (workspace_id, occurred_at month). Hot indexes on (urn affected, occurred_at).
- **Warm path**: events older than 90 days move to S3 Parquet (queryable via DuckDB / Athena).
- **Cold path**: events older than 7 years compressed via Snappy + dropped from hot index (still in S3).

Append-only: never UPDATED, never DELETED. (Right-to-erasure tombstones recorded as new `Erasure` events; verification chain — per ADR-0064 — proves the original events EXISTED but were erased.)

**Storage cost at 50-repo / 100-engineer scale**: ~200 bytes/event × 1M events/day × 365 days = ~70GB/year hot, then compressed to ~10GB/year warm. **Trivial.**

### M2 — Derived Views (the materialized projections)

Entities become DERIVED FACTS, not stored records. Each materialized view is computed from the event stream + cached for query performance:

#### View V1 — `EntityState(urn, branch, at_time) → EntityFacts`

The state of an entity on a specific branch at a specific time. Computed by replaying events filtered to that branch up to `at_time`.

For LIVE queries: cached in Postgres `entity_state_current` table per `(urn, branch)`. Refreshed by an incremental aggregator job on every new event.

For TIME-TRAVEL queries: replay from snapshot + delta events. Snapshots taken weekly per branch; delta replay between snapshot and `at_time`.

Cost: O(events-since-last-snapshot) per query. Typically <50 events. <50ms.

#### View V2 — `CausalChain(entity_urn) → list[BrainEvent]`

The ordered sequence of events that caused the entity's current state. Computed by walking `causal_parents` backward from the most recent event affecting `entity_urn`.

Used to answer: *"Why does the lob column exist?"* → returns `[ExternalDocChange(Notion: "Q3-2024 lob requirement"), GitCommit(add lob column), Deploy(v2.4.0), Incident(P1: lob mismatch), GitCommit(fix lob normalization)]`.

This is **the causal answer** the user wanted. Not "lob is a column" but "here's the trail of decisions that led to lob existing as it does today."

#### View V3 — `SalienceScore(urn, query_context, current_time) → float`

Per-query, per-time relevance scoring. Combines:
- Time-decay (per ADR-0072 M2)
- Query-context affinity (does the query mention entities in same domain / module?)
- Recent-activity boost (entity touched in events within 7 days)
- User-context boost (user is in `payments` team → payments-related entities boost)

Computed at query time (cheap; no materialization needed).

#### View V4 — `TimelineWindow(urn, since, until) → list[StateDelta]`

The deltas to an entity's state over a time window. Used to answer: *"What changed in the lob column last quarter?"*.

#### View V5 — `BranchOverlay(branch, base_branch) → dict[urn, BranchedFact]`

For each branch, what entities have facts that DIFFER from the base branch (usually main). Stores DELTAS, not full copies.

Storage trick: 95% of entities on `feature/x` are identical to `main`. Only store the 5% delta. At query time, fall through to base branch for unchanged entities.

For 50 active branches with ~50 unique deltas each: 50 × 50 = 2,500 branched facts ON TOP OF the base brain's 100K entities. Negligible storage cost.

#### View V6 — `FederatedIndex(repos[]) → entity routing`

Per-org index of (entity_qname / table_name / endpoint_path) → list of (repo, urn). Enables: *"which repos use the `customers` table?"* → cross-repo answer.

Maintained by a per-org background job that watches each repo's brain for new entities; replicates a SUMMARY (not the full entity) into the federated index.

Storage: ~100 bytes per entity × 5M entities = 500MB for an org with 50 repos × 100K entities each. **Easily fits in memory.**

---

### M3 — Continuous Refresh via Event Hooks

Brain receives events via webhooks + polling, NOT via manual `brain index` runs:

**Git events** (GitHub / GitLab / Bitbucket webhooks):
- `push` → `GitCommit` events emitted; affected files re-extracted within 60s
- `pull_request.opened` → `PROpened` event; brain extracts the diff and overlays on branch view
- `pull_request.merged` → `PRMerged` event; branch overlay merged into base
- `branch.deleted` → `BranchDeleted` event; overlay garbage collected after 7 days

**Doc events**:
- Notion webhook (per ADR-0070) → `ExternalDocChange` event
- Confluence webhook → same
- Slack messages mentioning entities → optional event ingestion

**Deploy / incident events**:
- CI/CD systems (GitHub Actions, Argo) post `Deploy` events
- PagerDuty / Datadog / Sentry post `IncidentDeclared` / `IncidentResolved` events
- Each links via `causal_parents` to recent commits / deploys

**Internal events**:
- Verifier corrections (ADR-0056) → `VerifierCorrection` events
- Agent actions (post-MCP-call) → `AgentAction` events
- Queries (per ADR-0066) → `QueryAsked` events

**Brain freshness invariant**: any event affecting an entity is reflected in materialized views within `T_freshness_seconds` (configurable; default 60s for code, 300s for docs).

**Staleness detection**: brain monitors itself. For each entity, compares `last_extracted_at` against `latest_event_timestamp_for_affected_files`. If gap > T_freshness, the entity is marked `stale=true`. Stale entities at query time trigger re-extraction (with budget cap per ADR-0064).

**No more "brain index"** as a manual step. The brain is ALWAYS up to date (within T_freshness). `brain index` becomes a cold-start command for new repos only.

---

### M4 — Pre-PR Working-Tree Overlay

A developer working on uncommitted changes wants the brain to know about THEIR working tree, not just committed branches.

**Mechanism**: an IDE plugin (or `brain refresh-working-tree` CLI) extracts the dev's `git diff` against their branch, stores it as a `__working_tree__:<user>:<machine>` virtual branch overlay (V5).

Query path: when called with `?ref=working_tree:<user>:<machine>`, the SmartZoneAssembler layers the working-tree overlay ON TOP OF the dev's current branch overlay ON TOP OF the base branch. Three-layer fallthrough.

**Privacy**: working-tree overlays are PRIVATE to the user (workspace_id + user_id scoping). They're auto-cleaned when the dev commits or `git stash` clears the diff.

**PII scrubbing** (per ADR-0064) runs on working-tree extractions FIRST — devs frequently have hardcoded test secrets in WIP code; we don't want those leaking to the brain.

This is what gives developers **"the brain knows what I'm working on RIGHT NOW"** — pre-commit, pre-PR.

---

### M5 — Federated Multi-Repo Architecture

For 50-repo companies, there is no single brain. Each repo gets its own brain INSTANCE; cross-repo queries are FEDERATED.

**Per-repo brain instance**: same code, separate Postgres + Neo4j + Qdrant data. Light: ~50MB image; deployable per-repo via Docker compose or Kubernetes deployment.

**Federated query planner**: a thin coordinator service that:
1. Parses the query → identifies which repos likely have relevant entities (uses federated index V6)
2. Fans out parallel sub-queries to relevant repo brains
3. Merges results via RRF (per ADR-0065)
4. Returns unified answer with per-repo citations

**Example**: user asks *"which repos depend on the `customers` table?"* → planner queries the federated index for `customers` → identifies 8 repos → fans out 8 parallel queries → merges → returns *"acme-payments-api (uses customers.email), acme-billing-worker (uses customers.id), …"*.

**Routing intelligence**: planner learns from past queries which repos have which entity types. Most queries hit 1-3 repos, not all 50.

**Org-level entities**: shared libraries, core domain entities (Payer, Customer), org-wide ADRs live in a special `__org__` brain that all repo brains can query. Avoids duplicating common knowledge.

**Cost model**: query cost grows with NUMBER OF REPOS QUERIED, not org size. Smart routing keeps it bounded.

---

### M6 — Causal Edges as First-Class

ADR-0055 introduced shared invariants and patterns. ADR-0072 M4 introduced behavioral invariants. M6 here generalizes: **CausalEdge** as a first-class edge type connecting any two events.

```python
@dataclass
class CausalEdge:
    source_event_id: str
    target_event_id: str
    relationship: Literal[
        "caused",                # event A directly caused event B
        "contributed_to",        # event A was one of multiple causes
        "mitigated_by",          # event B reduced impact of event A
        "rolled_back",           # event B reverted event A
        "investigated_in",       # event B is a postmortem of event A
        "blocked_by",            # event A waiting on event B
    ]
    confidence: float            # how certain we are about this causal link
    source: Literal[
        "git",                   # parsed from git commit message ("Fixes #123")
        "ticket_link",           # Linear/Jira ticket links commits
        "incident_postmortem",   # postmortem links commits
        "human_assertion",       # someone explicitly said via BRAIN.md
        "llm_inferred",          # LLM inferred from temporal + textual proximity
    ]
```

**Causal-chain queries**:
- *"What caused the P1 incident on Jan 9?"* → walks BACKWARD from `IncidentDeclared(Jan 9)` event via `caused` edges → returns deploy + commits + config changes that contributed.
- *"What's the blast radius if we revert this deploy?"* → walks FORWARD from `Deploy(commit_X)` via `caused` edges → finds all subsequent fixes that built on top.

**Building causal edges**:
- **Cheap (deterministic)**: parse git commit messages for `Fixes #123`, `Closes #456`, `Reverts abc123` patterns. Add commit→ticket and revert→commit edges.
- **Mid (heuristic)**: temporal-proximity rules. Deploy at T → incidents within 24h after T are CANDIDATE caused-by; raise causal confidence if deploy touched the failing endpoint.
- **Expensive (LLM-inferred)**: for high-stakes claims, an Augmentation Agent (per ADR-0063 M2) reads commit + incident + postmortem and infers causality with confidence < 1.0.

Periodically (per ADR-0067 evolution), low-confidence causal edges are re-validated by VerifierAgent.

---

### M7 — Query-Context-Aware Salience

ADR-0072 M2 had time-decay salience. M7 generalizes: salience is computed PER QUERY using:

```python
def compute_salience(memory, query_context):
    base = TYPE_BASELINES.get(memory.entity_type, 0.3)

    # Time decay (from ADR-0072 M2)
    age_days = (now - memory.last_event_at).days
    recency = exp(-age_days / 90)

    # Query-context affinity: does the query mention entities in same domain / module?
    affinity = cosine(query_context.embedding, memory.context_embedding)

    # Recent-event boost: entity touched by an event in last 7 days?
    has_recent_event = any(e.occurred_at > now - 7d for e in memory.recent_events)
    event_boost = 0.2 if has_recent_event else 0.0

    # User-context boost: user is in domain X → entities in domain X boost
    user_domain_boost = 0.15 if user_active_domain == memory.domain else 0.0

    # Pinned / human-curated never decays
    if memory.pinned: return 1.0

    return min(1.0, base * recency + 0.3 * affinity + event_boost + user_domain_boost)
```

Used by the RRF retrieval ranker (per ADR-0065) as a multiplier on RRF scores. **The same memory has different salience for different queries from different users at different times.** That's the right shape for per-query relevance.

---

### M8 — Hierarchical Retrieval (anti-noise at scale)

At 5M memories org-wide, FLAT retrieval (dump 5M into RRF) doesn't work. Solution: **hierarchical drill-down**.

Three layers:

**Layer 1 — Federated routing** (M5): which repos to query? Returns 1-3 of 50 repos.

**Layer 2 — Domain routing**: within each repo, which DOMAIN does the query touch? Uses ADR-0059's DomainEntity. Returns 1-3 of 10-15 domains. ("Payments domain only.")

**Layer 3 — Module routing**: within each domain, which MODULES (top-level packages)? Returns 2-5 of 20-50 modules. ("payments/api/ + payments/repository/")

**Layer 4 — Entity retrieval**: within selected modules, RRF over the actual entities. ~500-2000 entities, manageable.

Each layer is fast (mostly index lookups). Total retrieval cost stays sub-second even at 5M entities.

**Routing model**: trained on past queries' actual citations (per ADR-0066 query trajectories). Learns: *"questions mentioning 'payment' usually cite entities from payments domain."* Accuracy improves with usage; bootstraps from keyword + embedding similarity.

**Fallback**: if confidence in routing is low (e.g., novel query), fall through to broader scope. Optimizes for precision, falls back to recall.

This is the **librarian model** — don't read every book; consult the catalog index, narrow to a section, then to specific books.

---

## Storage implications (the honest math)

For a 50-repo / 100-engineer org running this architecture:

| Component | Size | Cost @ AWS prices |
|---|---|---|
| Event stream (hot, 90 days) | 70GB | $7/month (RDS) |
| Event stream (warm, 1 year) | 80GB | $2/month (S3) |
| Materialized views (Postgres) | 200GB | $25/month |
| Vector index (Qdrant) | 50GB (compressed embeddings) | $50/month (managed) |
| Federated index | 500MB | $0 (memory) |
| Branch overlays (50 active branches × 50 deltas) | 100MB | $0 |

Total: **~$85/month for a 50-repo / 100-engineer org**. That's $1/engineer/month for pure infrastructure. Compare to $30-100/month for Cursor / GitHub Copilot per seat — we're cheap.

LLM costs are SEPARATE — those scale with QUERIES (~$0.01-0.05 each), bounded by usage.

---

## Cost guardrails (preventing runaway spend)

- **Per-repo budget cap**: workspace can set `BRAIN_REPO_HOURLY_BUDGET=$1.00`. Re-extraction defers until next budget window if exceeded.
- **Stale-but-cheap policy**: if the entity is < 24h stale AND budget is near cap, serve stale + tag the answer "may be 24h stale".
- **Pre-extraction batching**: events accumulate for ~60s before triggering re-extraction (so 10 commits in 60s become 1 re-extraction, not 10).
- **Off-peak indexing**: heavy re-indexing happens during low-traffic windows (configurable; default 2-6am local).

---

## File ownership for THIS PR (parallel-safe with all existing ADRs)

Substantial new directory structure:

```
company-brain-ai/src/companybrain/events/                     # NEW DIRECTORY (M1)
company-brain-ai/src/companybrain/events/__init__.py
company-brain-ai/src/companybrain/events/event_store.py      # event-stream Postgres writer
company-brain-ai/src/companybrain/events/event_types.py      # all 14 event types
company-brain-ai/src/companybrain/events/event_replay.py     # for time-travel queries
company-brain-ai/src/companybrain/events/snapshot_manager.py # weekly per-branch snapshots
company-brain-ai/src/companybrain/events/causal_edges.py     # M6

company-brain-ai/src/companybrain/views/                      # NEW DIRECTORY (M2)
company-brain-ai/src/companybrain/views/entity_state.py      # V1
company-brain-ai/src/companybrain/views/causal_chain.py      # V2
company-brain-ai/src/companybrain/views/salience_view.py     # V3
company-brain-ai/src/companybrain/views/timeline_window.py   # V4
company-brain-ai/src/companybrain/views/branch_overlay.py    # V5
company-brain-ai/src/companybrain/views/federated_index.py   # V6 (org-level)

company-brain-ai/src/companybrain/refresh/                    # NEW DIRECTORY (M3)
company-brain-ai/src/companybrain/refresh/git_webhook.py
company-brain-ai/src/companybrain/refresh/notion_webhook.py
company-brain-ai/src/companybrain/refresh/deploy_webhook.py
company-brain-ai/src/companybrain/refresh/incident_webhook.py
company-brain-ai/src/companybrain/refresh/staleness_monitor.py
company-brain-ai/src/companybrain/refresh/incremental_extractor.py

company-brain-ai/src/companybrain/working_tree/               # NEW DIRECTORY (M4)
company-brain-ai/src/companybrain/working_tree/overlay.py
company-brain-ai/src/companybrain/working_tree/cli_refresh.py
ide/vscode-extension/src/working_tree.ts                       # IDE-side trigger

company-brain-ai/src/companybrain/federation/                 # NEW DIRECTORY (M5)
company-brain-ai/src/companybrain/federation/coordinator.py
company-brain-ai/src/companybrain/federation/repo_router.py
company-brain-ai/src/companybrain/federation/cross_repo_merger.py

company-brain-ai/src/companybrain/retrieval/hierarchical/     # NEW DIRECTORY (M8)
company-brain-ai/src/companybrain/retrieval/hierarchical/layer1_repo_router.py
company-brain-ai/src/companybrain/retrieval/hierarchical/layer2_domain_router.py
company-brain-ai/src/companybrain/retrieval/hierarchical/layer3_module_router.py
company-brain-ai/src/companybrain/retrieval/hierarchical/learned_routing.py

company-brain-ai/src/companybrain/api/routes/webhooks.py      # NEW — webhook receivers
company-brain-ai/src/companybrain/api/routes/federation.py    # NEW — coordinator API

db/migrations/V20__event_stream_storage.sql                    # NEW
db/migrations/V21__causal_edges.sql                            # NEW
db/migrations/V22__federated_index.sql                         # NEW

tests/unit/test_event_stream.py
tests/unit/test_causal_chain.py
tests/unit/test_branch_overlay.py
tests/unit/test_federated_routing.py
tests/acceptance/test_continuous_freshness_e2e.py
tests/acceptance/test_pre_pr_working_tree.py
tests/acceptance/test_multi_repo_federation.py
tests/acceptance/test_causal_chain_query.py
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py    # add BrainEvent + CausalEdge dataclasses
company-brain-ai/src/companybrain/api/routes/query.py   # accept ?ref=<branch> + ?at_time=<iso>
company-brain-ai/src/companybrain/assembly/multi_graph_retrieval.py  # delegate to hierarchical retrieval
company-brain-ai/src/companybrain/api/routes/admin.py   # webhook secret management
company-brain-ai/src/companybrain/cli.py                # `brain refresh-working-tree`, `brain federate add-repo`
config.py                                                # webhook secrets, freshness thresholds, hierarchical-routing toggles
```

Does NOT touch any file owned by ADR-0055-0072 implementations.

---

## Acceptance test (the integration that proves it)

```python
async def test_freshness_within_60_seconds_of_git_push():
    """Push a commit; brain reflects the change within 60 seconds."""
    await git_push(repo="test-repo", file="src/foo.py", new_content="def new_method(): pass")
    await asyncio.sleep(60)
    entity = await brain.query("entity 'foo.new_method'")
    assert entity is not None
    assert entity.last_event_at >= push_timestamp


async def test_branch_divergence_returns_branch_specific_answer():
    """Two branches with different code; queries with ?ref= return different answers."""
    await git_push(repo="...", branch="main", file="...", content="version A")
    await git_push(repo="...", branch="feature/x", file="...", content="version B")
    answer_main = await query("...", ref="main")
    answer_feature = await query("...", ref="feature/x")
    assert "version A" in answer_main.summary_md
    assert "version B" in answer_feature.summary_md


async def test_pre_pr_working_tree_visible():
    """Dev refreshes working tree; their uncommitted changes appear in queries."""
    await write_file_uncommitted("...", "experimental_method")
    await cli_run("brain refresh-working-tree --user=alice --machine=laptop")
    answer = await query("...", ref=f"working_tree:alice:laptop")
    assert "experimental_method" in answer.summary_md
    # but the same query without working_tree ref doesn't see it:
    main_answer = await query("...", ref="main")
    assert "experimental_method" not in main_answer.summary_md


async def test_causal_chain_for_lob_column():
    """Asking 'why does the lob column exist?' returns a causal trail."""
    chain = await brain.causal_chain("urn:cb:network-iq:column:plan_info.lob")
    # Should return events in order: ExternalDocChange → GitCommit → Deploy → Incident → Fix
    event_types = [e.event_type for e in chain]
    assert "ExternalDocChange" in event_types  # the Notion PRD
    assert "GitCommit" in event_types
    assert "Deploy" in event_types


async def test_federated_query_finds_entity_across_repos():
    """Multi-repo: 'which repos depend on the customers table?' returns all of them."""
    await setup_repos(n=5, table_name="customers")
    response = await query("which repos depend on the customers table?")
    assert len(response.cited_repos) == 5


async def test_time_travel_query_returns_3mo_ago_state():
    """Query with ?at_time=2026-02-13 returns the codebase state from that date."""
    answer_now = await query("how many CTEs in getPayerCompetitors?", at_time=None)
    answer_3mo = await query("how many CTEs in getPayerCompetitors?", at_time="2026-02-13")
    assert answer_now != answer_3mo  # state has evolved


async def test_hierarchical_retrieval_doesnt_load_5M_entities():
    """At simulated 5M entities, query response time stays under 500ms via routing."""
    await seed_5M_synthetic_entities()
    start = time.perf_counter()
    response = await query("payments domain question")
    assert (time.perf_counter() - start) < 0.5
    # And only loaded entities from the payments domain:
    assert all(e.domain == "payments" for e in response.cited_entities)


async def test_staleness_triggers_reextraction():
    """An entity older than freshness threshold gets re-extracted on query."""
    await create_entity(last_extracted_days_ago=10, freshness_threshold_days=7)
    response = await query("...")
    assert response.entity.last_extracted_at > now - 1m  # was just refreshed
```

---

## Effort estimate

8 weeks (40 person-days), parallelisable to ~10-12 calendar days with 6-8 sessions:

| Workstream | Days | Notes |
|---|---|---|
| M1 — Event store + 14 event types + V20 migration | 5 | Foundation; everything depends on this |
| M2 — 6 materialized views + snapshot/replay | 8 | Core query path lives here |
| M3 — Webhook receivers + 5 event sources + staleness monitor | 5 | Integration-heavy |
| M4 — Working-tree overlay + IDE plugin trigger + CLI | 4 | Pre-PR feature |
| M5 — Federated coordinator + repo router + V22 migration | 6 | Multi-repo at scale |
| M6 — Causal edges + 3 inference strategies + V21 migration | 5 | Causal reasoning |
| M7 — Query-context-aware salience scoring | 2 | Cheap, high-leverage |
| M8 — 4-layer hierarchical retrieval + learned routing | 5 | Anti-noise at scale |

---

## Sequencing recommendation

This is a **POST-SEED, PRE-SERIES-A** investment. Specifically:

| Wave | When | What |
|---|---|---|
| Demo + seed close | Now → 4 weeks | Wave 0-3 of IMPLEMENTATION-ORDER-V3 (existing roadmap) |
| **Series A prep** | Months 2-4 post-seed | **THIS ADR (0073) + ADR-0072** — these are the architectural primitives Series A investors will ask about |
| Pre-Series B | Months 6-12 post-seed | Refine + ship at scale |

**Why post-seed**: this ADR doesn't help close the seed round. It's PURE infrastructure. Investors at seed care about "does the demo work?" — not "is your event-sourcing architecture clean?" Spending 8 weeks on this BEFORE the seed = shipping nothing visible.

**Why pre-Series A**: by Series A you need to answer "how does this scale to a 100-repo enterprise?" If you don't have a credible architectural answer (event-sourcing, federation, causal reasoning), Series A becomes much harder.

**Implementation pattern**: ship M1 + M2 (event store + views) FIRST as the foundation; everything else extends. M3-M8 can ship in parallel after M1-M2 land.

---

## Strategic value (the user's framing)

The user's two messages identified this combined problem: **without continuous freshness + branch awareness + federation + causal/temporal/salience + noise-at-scale handling, the brain becomes worthless within a quarter, exactly like the documentation it replaced.**

This ADR is the architectural answer. With M1-M8:

- ✅ **Brain stays fresh within 60s of any git push** (M3 + M1 events)
- ✅ **Pre-PR memory works** (M4 working-tree overlay)
- ✅ **Multi-repo scales** (M5 federation)
- ✅ **Causal reasoning is first-class** ("why does X exist?" answerable; M6 causal edges)
- ✅ **Time-travel queries** ("what did the codebase look like 3 months ago?"; M2 V4 + V1)
- ✅ **Branch divergence is normal, not exceptional** (M2 V5 branch overlay)
- ✅ **Salience adapts to query + time + user** (M7)
- ✅ **5M entities scale to sub-second queries** (M8 hierarchical routing)
- ✅ **Contradictions are branch-scoped, not overwriting** (M2 V5 + ADR-0072 M1)

**Without this ADR**, the brain at 50-repo / 1-year scale is:
- ❌ Stale (Confluence death spiral)
- ❌ Single-branch only (devs can't trust it for THEIR branch)
- ❌ Per-repo siloed (can't answer cross-repo questions)
- ❌ No causal chain (can't answer "why")
- ❌ Snapshot-only (no time-travel)
- ❌ Overwriting (loses branch-specific facts)
- ❌ Flat retrieval crushed by noise
- ❌ Salience doesn't adapt

That's NOT the brain we sold investors. **This ADR is what makes the demo's promise honest at scale.**

---

## Action items (the 8 mechanisms, sequenced)

### Foundation — must land first
1. [ ] V20 migration: `brain_events` table + indexes + partitioning by month
2. [ ] V21 migration: `causal_edges` table
3. [ ] V22 migration: `federated_index` + `branch_overlay` tables
4. [ ] `events/event_store.py` + 14 event types — write path
5. [ ] `events/event_replay.py` — read path with snapshot acceleration

### M2 — Materialized views (parallel after foundation)
6. [ ] `views/entity_state.py` — V1
7. [ ] `views/causal_chain.py` — V2
8. [ ] `views/salience_view.py` — V3
9. [ ] `views/timeline_window.py` — V4
10. [ ] `views/branch_overlay.py` — V5
11. [ ] `views/federated_index.py` — V6 (org-level)

### M3 — Continuous refresh
12. [ ] `refresh/git_webhook.py` — GitHub + GitLab + Bitbucket
13. [ ] `refresh/notion_webhook.py` (per ADR-0070 connector)
14. [ ] `refresh/deploy_webhook.py` — GitHub Actions / Argo / etc.
15. [ ] `refresh/incident_webhook.py` — PagerDuty / Datadog / Sentry
16. [ ] `refresh/staleness_monitor.py` — proactive freshness checking
17. [ ] `refresh/incremental_extractor.py` — extract only diff'd files

### M4 — Pre-PR working tree
18. [ ] `working_tree/overlay.py` — overlay management
19. [ ] `working_tree/cli_refresh.py` — `brain refresh-working-tree` CLI
20. [ ] IDE plugin trigger (VS Code per ADR-0052 P7)

### M5 — Federation
21. [ ] `federation/coordinator.py` — query planner
22. [ ] `federation/repo_router.py` — which repos to query
23. [ ] `federation/cross_repo_merger.py` — merge results via RRF
24. [ ] `cli.py` add `brain federate add-repo` / `list-repos`

### M6 — Causal edges
25. [ ] `events/causal_edges.py` — model + 3 inference strategies (deterministic, temporal-proximity, LLM-inferred)
26. [ ] Causal-chain query support in `views/causal_chain.py`

### M7 — Query-context-aware salience
27. [ ] Update `views/salience_view.py` with query-context, recent-event, user-domain boosts

### M8 — Hierarchical retrieval
28. [ ] `retrieval/hierarchical/layer1_repo_router.py`
29. [ ] `retrieval/hierarchical/layer2_domain_router.py`
30. [ ] `retrieval/hierarchical/layer3_module_router.py`
31. [ ] `retrieval/hierarchical/learned_routing.py` — learns from past query trajectories

### Cross-cutting
32. [ ] `api/routes/webhooks.py` — webhook receivers
33. [ ] `api/routes/query.py` — accept `?ref=<branch>` + `?at_time=<iso>`
34. [ ] Acceptance test suite: 8 tests above
35. [ ] Telemetry: per-event-type rate, freshness-violation count, federated-query-count, hierarchical-routing-confidence
36. [ ] Documentation: `docs/EVENT-STREAM-MEMORY.md` — operator guide
