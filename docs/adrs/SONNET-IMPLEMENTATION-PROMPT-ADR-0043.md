# Implementation prompt for ADR-0043 — Query-Time Quality, Prompts, Responses

> Paste this into a fresh Claude Code session inside the `company-brain` repo.
> The prompt is self-contained — Claude Code does not need this conversation's
> history to act on it.

---

You are implementing ADR-0043 (`docs/adrs/ADR-0043-query-time-quality-and-prompts.md`).
Read that ADR first, then this prompt, then begin.

The work is in 5 PRs. Ship them in order; each is independently valuable.

## Working agreement (re-stated for emphasis)

1. **Language-agnostic.** No Java-specific regex / suffix checks in the
   orchestrator path. Same flow for Java, Python, TypeScript, Go, etc.
   Frameworks are recognised by the LLM, not by per-framework branches.
2. **Cite or drop.** Every factual claim the LLM emits in the answer must
   end with a URN. Train this in the system prompt; enforce at parse-time
   by stripping un-cited sentences below the summary.
3. **Idempotent storage.** Every new index / materialised view / cached
   answer is rebuildable from primary `.brain/` JSON. Nothing creates
   data that can only live in Postgres or Neo4j.
4. **Cost guard.** All new LLM passes respect `BRAIN_JOB_BUDGET_USD` (env,
   default $0.50). The intent router has its own cap (`max_query_cost_usd`,
   default $0.02 per query) so a runaway agent can't burn an account.
5. **Skip flags.** Every new pass / index / template gets a
   `BRAIN_SKIP_<name>` env flag. `make dev` should run with all flags off
   (full quality); `make demo-fast` runs with everything skip-able skipped.

---

## PR-0043-1 — Hotfix: kill the `monorepo` URN fallback (WS1.S3)

The bug that caused "104 nodes, 0 edges in Neo4j" today is structural:
multiple URN constructors fall back to `repo="monorepo"` when their caller
forgot to thread the real repo through. Fix by making the parameter required.

### Files to change

- `src/companybrain/store/identity.py` — the canonical `to_urn()` and any
  helpers. Make `repo` and `entity_type` positional (no defaults). Any call
  that can't supply them now raises `RepoUnknownForUrn` (new exception)
  instead of silently using `monorepo`.
- `src/companybrain/graph/neo4j_writer.py::_external_id_to_urn` — already
  has the name→URN index from commit `395df9e09`. Remove the
  `repo="monorepo"` fallback at the bottom; instead log the offending
  external_id and return None so the edge is dropped (visible) rather than
  silently orphaned.
- `src/companybrain/cli_helpers/brain_rebuild.py` and `brain_enrich.py` —
  thread the repo name through (read it from `entity.repo` of the first
  loaded BrainEntity).
- Java side: `PipelineService.applyResult()` already constructs URNs
  client-side, but if any path uses a default workspace_repo, audit
  it and require explicit repo too.

### Tests

- `tests/unit/store/test_identity.py` — assert `to_urn(...)` raises when
  `repo` is missing; assert no string `"monorepo"` appears in any URN
  produced by the test fixtures.
- `tests/integration/test_no_orphan_edges.py` — after rebuild on the
  Java fixture, assert `MATCH ()-[r]->() WHERE NOT EXISTS { MATCH ({id: r.source_id}) }` returns 0 rows.

### Done when

- `grep -rn '\"monorepo\"' src/companybrain/` returns 0 hits.
- No edge in Postgres or Neo4j has a source_id / target_id whose repo
  segment is `monorepo`.

---

## PR-0043-2 — Response layer: typed schema, citations, confidence (WS3)

Biggest visible quality lift in one PR. No data changes.

### Files to add / change

- `src/companybrain/models/query_response.py` (new) — Pydantic models:

  ```python
  class CallChainStep(BaseModel):
      ord: int
      urn: str
      name: str
      role: Literal["entry", "controller", "service", "repository",
                    "query", "external", "frontend", "test", "other"]
      edge_in: Optional[str]   # the edge type that led to this step
      annotations: list[str] = []
      one_liner: str

  class SqlBlock(BaseModel):
      source_urn: str          # which entity owns this SQL
      language: Literal["sql", "jpql", "jooq", "cypher", "mongo", "other"]
      body: str                # verbatim, < 1500 chars

  class Citation(BaseModel):
      urn: str
      name: str
      why_relevant: str
      confidence: float        # node confidence

  class RiskAssessment(BaseModel):
      level: Literal["low", "medium", "high"]
      reason: str
      blast_radius_count: int
      sample_affected: list[Citation]   # top 5

  class Confidence(BaseModel):
      level: Literal["high", "medium", "low"]
      rationale: str

  class QueryResponse(BaseModel):
      summary: str             # 1-3 sentences, plain prose
      call_chain: list[CallChainStep] = []
      sql_quotes: list[SqlBlock] = []
      affected_entities: list[Citation] = []
      change_risk: Optional[RiskAssessment] = None
      confidence: Confidence
      caveats: list[str] = []
      follow_up_questions: list[str] = []
      raw_markdown: str        # rendered version for clients that want one blob
  ```

- `src/companybrain/api/routes/query.py` — change response_model to
  `QueryResponse`. Backward-compat: keep `summary` populated as the
  primary `answer:`-equivalent field.
- `src/companybrain/api/prompts/query_system.py` (new) — host the system
  prompt as a constant. New prompt body:

  ```
  You are a senior software engineer reviewing an unfamiliar codebase.
  You have a knowledge graph of the codebase available as the KNOWLEDGE
  BASE below. Answer questions ONLY using nodes and edges in that graph.

  ━━━ RESPONSE PROTOCOL ━━━
  Output a single JSON object matching the QueryResponse schema. Sections:

  1. summary — 1-3 sentences directly answering the question. Plain prose,
     no jargon.
  2. call_chain — ordered list of nodes from entry-point to leaf. Include
     the edge type that connects each pair. Stop at SQL / external service
     / frontend boundary.
  3. sql_quotes — quote any SQL / JPQL / jOOQ DSL / Cypher VERBATIM in the
     body field. source_urn = the entity whose query_text or code_snippet
     this came from. Never paraphrase SQL.
  4. affected_entities — every node you mention in summary or call_chain
     appears here as a Citation with why_relevant.
  5. change_risk — populate ONLY for impact-analysis questions. blast_radius_count
     should be the size of the unique downstream node set.
  6. confidence — pick from the rubric:
     - high: every cited node has confidence >= 0.9 AND call_chain has no gaps
     - medium: nodes >= 0.7 OR exactly one call-chain gap
     - low: any node < 0.7 OR multiple gaps OR conflicting evidence
     rationale: 1 sentence explaining the rating.
  7. caveats — list ONLY genuine missing data ("the migration file for
     `lob` is not in the graph"). Do NOT pad with caveats about caveats.
  8. follow_up_questions — 2-3 questions the user is likely to ask next.
     Examples after a "how does X work" answer: "What tests cover X?",
     "What changes if I deprecate X?".

  ━━━ CITATION RULES ━━━
  Every factual claim about the codebase must end with the URN of the
  node that supports it, in square brackets: e.g. "lob is read by the
  payer-competitor query [urn:cb:dev:code:repo:component:getPayerCompetitors]".

  If you cannot cite a node for a claim, drop the claim. Better to say
  less than to fabricate.

  ━━━ FORBIDDEN ━━━
  - Inventing entity names not in the KNOWLEDGE BASE.
  - Quoting SQL that isn't verbatim in any node's query_text.
  - Hedging when you have the data ("you might want to check…" — just
    cite the node and state it).
  - Free-form markdown outside the JSON envelope.
  ```

- Add 3 few-shot examples spanning impact-analysis, trace-flow, and
  explain-purpose intents — each shows the expected JSON output.

- `src/companybrain/api/responses/markdown_renderer.py` (new) — turn a
  `QueryResponse` into the `raw_markdown` field for clients that want
  a single string. Sections: Summary / Call chain / SQL / Affected /
  Risk / Confidence / Caveats / Next questions.

### Tests

- `tests/api/test_query_response_schema.py` — fuzz the LLM with 20
  questions across the 3 fixtures; assert every response parses against
  `QueryResponse`; assert every citation in `summary`/`call_chain` is in
  `affected_entities`.
- `tests/api/test_no_uncited_claims.py` — regex-strip un-cited sentences
  from `summary` and assert nothing left contains code-shaped tokens
  (camelCase identifier, file path, table name).

### Done when

- A query against the Java fixture for "what tables does
  getPayerCompetitors read" returns a `QueryResponse` with
  `affected_entities` listing `plan_info.lob` and friends, `sql_quotes`
  containing the verbatim SELECT, `confidence.level == "high"`.
- Every UI consumer reads `summary` for headline + the structured fields
  for widgets.

---

## PR-0043-3 — Storage: multi-granularity index + dedup + hot-node cards (WS1)

### Files to change

- `src/companybrain/retrieval/qdrant_client.py` — add 3 new collection
  names per workspace: `__t2_card`, `__code`, `__business`. Each writes
  a different embedding source.
- `src/companybrain/store/fanout.py` — when writing an entity, embed
  three texts and upsert to the three collections.
- `src/companybrain/retrieval/hybrid_search.py` — add a `index` parameter
  (default `t1_summary`) and route to the right collection by intent.
- `src/companybrain/pipeline/_dedup.py` — new helper:
  `dedup_relationships_by_confidence(rels)` — for each (from, edge_type, to)
  triple, keep the rel with highest confidence; if tied, keep the one with
  longest evidence string. Replace all callers of the existing first-wins dedup.
- Java side: add `node_card` JSONB column to `nodes` table and a
  background job (or post-Phase 5 hook) that pre-computes the card for
  the top 50 in-degree nodes per workspace. Card schema:

  ```json
  {
    "computed_at": "2026-05-10T…",
    "downstream_chain": [...],
    "upstream_callers": [...],
    "tables_read":   [{"table":"plan_info","columns":["lob","payer"]}],
    "tables_written": [],
    "tests_covering": [...],
    "annotations":  ["Transactional", "Cacheable"],
    "recent_commits": [...]
  }
  ```

- `src/companybrain/api/routes/query.py` — when the intent router picks
  one of the top-50 nodes as the anchor, hydrate from `node_card`
  instead of doing a graph walk. ~100ms vs ~800ms.
- Add `edges_reverse` materialised view (Postgres) and refresh hook in
  `PipelineService` after Phase 5.

### Tests

- `tests/store/test_dedup.py` — given two edges (same triple, conf 0.6
  and 0.95), assert dedup keeps the 0.95 one and its evidence string.
- `tests/integration/test_node_card.py` — after enrich, assert the top
  3 in-degree nodes have populated `node_card` with non-empty
  `downstream_chain`.
- `tests/integration/test_inverse_edge_index.py` — after enrich, assert
  `edges_reverse` row count == `edges` row count (modulo pruned edges)
  and a sample query is faster than the equivalent graph walk.

### Done when

- 3 Qdrant collections per workspace; intent router picks the right one.
- `node_card` populated for the top 50 nodes; query path uses it.
- Confidence-weighted dedup never drops the higher-confidence edge.

---

## PR-0043-4 — Prompt layer: intent router + pre-walk + per-intent templates (WS2)

### Files to add / change

- `src/companybrain/api/intent_router.py` (new):

  ```python
  ROUTER_SYSTEM = """\
  Classify the user's question about the codebase into one of:
    impact-analysis  — "what breaks if I change X"
    trace-flow       — "how does X work / what does X call"
    explain-purpose  — "what is X / why does X exist"
    find-callers     — "who uses X"
    find-tests       — "what tests cover X"
    schema-question  — "what tables / columns / fields"
    risk-assessment  — "is it safe to do X"
    general          — anything else

  Return JSON with: intent, anchor_entities (proper-noun terms from the
  question), edge_types_needed (subset of the 50-edge taxonomy),
  max_hops (1-5), include_test_coverage (bool), include_blast_radius (bool).

  Examples follow.
  """
  ```

  Cost cap: ~$0.001. Cache by (question_hash, workspace_id) for 5 min.

- `src/companybrain/assembly/pre_walk.py` (new) — after SmartZoneAssembler
  produces tiered nodes, walk the edges in topological order from the
  intent's `anchor_entities` and produce a markdown narrative (see
  ADR-0043 §WS2.P2 for format). Stop at SQL leaves / external services /
  frontend boundaries / max_hops.
- `src/companybrain/api/prompts/user_message.py` (new) — per-intent
  templates that wrap the question + pre-walk + entity cards. Each
  template names the protocol the LLM should use:

  ```python
  TEMPLATES = {
    "impact-analysis": """\
  You are answering an IMPACT-ANALYSIS question.
  Anchor entities: {anchors}
  Required edges in answer: READS_COLUMN, WRITES_COLUMN, CALLS, USES.
  Always populate `change_risk` in the response.

  USER QUESTION:
  {question}

  PRE-WALK (follow this graph traversal):
  {pre_walk}

  ENTITY CARDS:
  {t2_cards}

  BLAST RADIUS (downstream nodes affected by changes to anchors):
  {blast_summary}
  """,
    # …other intents…
  }
  ```

- `src/companybrain/assembly/smart_zone.py` — accept `policy: SmartZonePolicy`
  derived from the intent router; the policy tells the assembler which
  hops / edge types / dense-index to use.
- `src/companybrain/api/routes/query.py` — call intent router → build
  policy → assemble → render with new system + user templates.

### Tests

- `tests/api/test_intent_router.py` — 20 questions, assert intent matches
  expected.
- `tests/assembly/test_pre_walk.py` — given a fixture graph, assert
  pre-walk markdown contains expected nodes in topological order.
- `tests/integration/test_per_intent_templates.py` — for each intent,
  end-to-end test that the right policy is applied and the right
  template is used.

### Done when

- Intent router classifies correctly on 18/20 fuzz cases.
- Each intent uses its own template + policy.
- The lob-rename query produces an answer with non-empty
  `change_risk.blast_radius_count` and `sample_affected` listing
  `plan_info.lob`-bearing nodes.

---

## PR-0043-5 — Acceptance tests + Cowork UI rendering

### Files to add

- `tests/acceptance/test_lob_rename.py` — runs the lob-rename question
  against the Java fixture and asserts:
  - `confidence.level` ∈ {"high", "medium"}
  - `affected_entities` contains a citation whose name contains "lob"
  - `sql_quotes` contains a SqlBlock whose body contains "lob"
  - `call_chain` has ≥ 3 steps including a Repository step
  - `follow_up_questions` is non-empty
- `tests/acceptance/test_per_language.py` — same assertions on
  `tests/fixtures/python_sqlalchemy/` and `tests/fixtures/typescript_drizzle/`.
- Cowork UI changes (or Storybook fixtures if UI is separate): widgets
  for CallChainStep (vertical flow), SqlBlock (syntax-highlighted
  monaco), Citation (clickable URN chip), RiskAssessment (red/amber/green
  banner), Confidence (badge), follow-up questions (chip row).

### Done when

- `make test-acceptance` passes on all three fixtures.
- A demo run of the Cowork UI shows the new structured response widgets
  for the lob-rename question.

---

## Cross-PR cross-cutting

### Cost guard (re-stated)

```python
# config.py
brain_job_budget_usd:        float = 0.50
brain_query_budget_usd:      float = 0.02
brain_query_cache_ttl_sec:   int   = 300
```

Every new LLM call increments `_session_cost_usd`. If
`_session_cost_usd > brain_query_budget_usd`, the agent skips Pass 2 and
returns Pass 1 with a "low" confidence + caveat about budget exhaustion.

### Edge-types single source of truth

Move the edge-type taxonomy from `relationship_extractor.py`'s system prompt
to `src/companybrain/edges/taxonomy.py`:

```python
@dataclass(frozen=True)
class EdgeType:
    name: str           # "CALLS"
    category: str       # "behavior"
    canonical_verb: str # "calls"
    description: str    # "synchronous in-process invocation"

EDGE_TYPES: list[EdgeType] = [...]  # 50 entries from ADR-0042 §2.4
```

The relationship extractor's prompt + the response-schema validator + the
intent router's `edge_types_needed` enum all import from this module. When
the taxonomy grows, one file changes.

### Logging contract (re-stated)

Every new pass / router / cache-hit emits:

```python
log.info(
  f"{stage} OK",
  edges_in=N, edges_out=M,
  llm_calls=K, llm_input_tokens=X, llm_output_tokens=Y,
  cost_usd=Z, duration_ms=D,
  policy=intent.dict() if intent else None,
)
```

So a single `grep "cost_usd=" /tmp/run.log | awk '{sum+=$NF}END{print sum}'`
gives total run cost.

### Documentation

- Update `README.md` — supported languages table, query examples with
  before/after snapshots.
- Update `docs/POST-MERGE-RUNBOOK.md` — new BRAIN_SKIP_* flags, new
  budget env vars, debugging the intent router.
- Update `docs/MIGRATION-mono-to-multirepo-to-company.md` — ADR-0042
  + ADR-0043 land in Stage 2.

## Definition of done (whole ADR)

1. All 5 PRs merged; ADR-0043 status flips Proposed → Accepted with the
   merge SHA in front-matter.
2. The lob-rename question on the Java fixture returns a high/medium
   confidence cited answer with a populated call chain.
3. Same question on the Python and TypeScript fixtures returns
   equivalent answers.
4. Total cost per query stays under $0.02 (router + retrieval +
   answer); cost per enrich stays under $0.20.
5. No regressions in the existing pipeline tests.
6. Cowork UI renders the structured response with the new widgets.

## Non-goals (do NOT do these here)

- Reasoning about non-text artifacts (images, design files).
- Multi-turn conversational memory (ChatGPT-style "as we discussed").
  This is one-shot Q&A with follow-up suggestions; multi-turn is a
  separate ADR.
- Custom user-installable prompts. The system prompt is fixed across
  the workspace; per-user style preferences are out of scope.
- Realtime collaboration on answers. Stateless Q&A.

## Suggested PR breakdown (re-stated)

1. **PR-0043-1: URN hotfix** (1 day) — kills the latent bug class.
2. **PR-0043-2: response layer** (2-3 days) — biggest visible quality lift.
3. **PR-0043-3: storage layer** (3-4 days) — multi-index, dedup, node cards.
4. **PR-0043-4: prompt layer** (3-4 days) — intent router, pre-walk, templates.
5. **PR-0043-5: acceptance + UI** (2 days) — fixture tests + Cowork widgets.

Each PR ships independently; users see incremental improvement at every step.
