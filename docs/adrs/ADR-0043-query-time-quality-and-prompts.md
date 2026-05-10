# ADR-0043: Query-Time Quality, Prompt Architecture & Human-Friendly Responses

**Status:** Accepted
**Date:** 2026-05-10
**Deciders:** Chinmay (product), engineering review
**Companion:** `SONNET-IMPLEMENTATION-PROMPT-ADR-0043.md` for Claude Code brief
**Builds on:** ADR-0018 (smart-zone assembler), ADR-0042 (language-agnostic extraction)

## Context

The brain currently extracts well (110 entities, 21-field BusinessContext per
node, 50-edge taxonomy, structural pre-extraction) and persists end-to-end
(Postgres + Neo4j + Qdrant + .brain/). The fix shipped today (`compressor.py`)
restored `query_text`, `code_snippet`, and `business_context` to the LLM
prompt — but the user's "what tables does X read" query still produced a
hedging "I don't have enough information" answer.

The remaining gaps are at three layers:

1. **Storage layer** — entity records carry rich content but the indices that
   queries hit (Qdrant collections, Postgres metadata JSON, Neo4j properties)
   carry only summary fields. Dense retrieval ranks by `t1_summary` text, not
   by the actual code or SQL, so the most relevant nodes are often missed.
2. **Prompt layer** — the system prompt and user-message rendering are
   adequate for general chat but don't exploit the graph's structure. The LLM
   has to re-derive the call chain from a flat list of nodes instead of being
   handed a pre-computed traversal.
3. **Response layer** — answers are correct but unstructured: no clear call
   chain, no per-node confidence, no actionable next steps, no inline source
   links the user can click.

The goal of this ADR: **close those three gaps** so the brain answers
code-understanding questions at the quality bar of Claude Code or Antigravity
— specifically:

- Cite specific files / methods / SQL / line numbers verbatim.
- Show an explicit call chain from entry-point to leaf with edge types.
- Surface change-impact (blast radius) for "rename X" / "delete Y" questions.
- Distinguish "the graph definitively says X" from "I'm inferring X based on
  Y" with calibrated confidence.
- Be navigable — every node mentioned is a clickable URN.

This ADR consolidates and supersedes the user's earlier list of 12
enhancements (multi-pass relationships, cross-file call graph, annotation
extraction, test coverage, frontend↔backend, ORM tables, method-level
freshness, migration extraction, confidence-weighted dedup, two-pass query
agent, inverse call cache, repo-aware URNs) into a single coherent design.

## Decision

Three coordinated workstreams. None is language-specific.

### WS1 — Storage layer: index what the LLM actually reads

**S1. Multi-granularity embeddings.** Today Qdrant indexes `t1_summary` only.
Add three additional embeddings per node so dense retrieval matches against
real content:

- `t2_card`: the full markdown card the renderer produces (purpose + SQL +
  body + relationships) — this is what the LLM ultimately sees.
- `code_embedding`: code/SQL body with comments stripped — for "find similar
  implementation" queries.
- `business_embedding`: BusinessContext purpose + invariants + failure_modes
  — for "find functions that handle X domain concept" queries.

Each lives in its own Qdrant collection so retrieval can pick the right
index per intent (E10 router from ADR-0042).

**S2. Inverse-edge index (Postgres materialised view).** Pre-compute
`edges_reverse` so "who calls X" / "who reads column Y" / "what tests exercise
function Z" answer in one indexed lookup instead of a graph walk. Refresh
after every Phase 5. Item #11 from the user's list.

**S3. Repo-aware URN scheme — kill `monorepo` fallback entirely.** The bug
that cost us hours today (edges → `monorepo`, nodes → real repo, MATCH found
nothing) recurs whenever a code path constructs a URN without all the
metadata. Solution: every URN constructor REQUIRES `repo` and `entity_type`
as positional args; the `monorepo` default is removed; calls that can't
satisfy the contract emit a `LegacyURN` placeholder and refuse to write.
Item #12 from the user's list.

**S4. Confidence-weighted edge dedup.** When the same edge is emitted by
multiple sources (structural-edge extractor + LLM + import graph), keep the
one with highest confidence + richest evidence. Today's `_dedup_relationships`
keeps first-wins, which silently drops good evidence when a low-confidence
edge happens to come first. Item #9 from the user's list.

**S5. Pre-computed answer cards for hot nodes.** For the top 50 nodes by
in-degree (typically API endpoints + services), pre-compute and cache:

- Full call chain (3 hops downstream + 2 hops upstream)
- Tables read / written
- Tests exercising
- Annotations (transactional / cached / etc.)
- Recent commits touching the node

Stored as a `node_card` JSONB column on the node. Query path skips graph
traversal for these nodes. This is item #11 generalised — not just inverse
calls but the whole "answer card".

### WS2 — Prompt layer: hand the LLM a pre-traversed graph

**P1. Intent router (LLM-as-classifier).** Item #10 from user's list. Before
retrieval, one ~$0.001 LLM call returns:

```json
{
  "intent": "impact-analysis | trace-flow | explain-purpose | find-callers | find-tests | schema-question | risk-assessment | general",
  "anchor_entities": ["lob", "getPayerCompetitors"],
  "edge_types_needed": ["READS_COLUMN", "CALLS"],
  "max_hops": 3,
  "include_test_coverage": true,
  "include_blast_radius_summary": true
}
```

The router's output directly drives SmartZoneAssembler — different intents
fetch different subgraphs.

**P2. Pre-traversed context (the "Pre-Walk" block).** Today's renderer
produces independent T2 entity cards. The new renderer ALSO produces a
`Pre-Walk` block: a topologically-sorted markdown narrative that follows the
edges from anchor → leaves so the LLM doesn't have to reassemble the chain:

```markdown
## Pre-Walk: getPayerCompetitors call chain

1. **getPayerCompetitors** (ApiEndpoint, controller layer)
   - signature: GET /v1/payers/{payerId}/competitors
   - guarded by: @PreAuthorize("hasRole('ANALYST')")
   - calls → CompetitivenessService.getPayerCompetitors

2. **CompetitivenessService.getPayerCompetitors** (Function, service layer)
   - calls → CompetitivenessRepository.getPayerCompetitors
   - delegates to async fetch → fetchAllCompetitors

3. **CompetitivenessPlanRepository.getPayerCompetitors** (DatabaseQuery)
   - reads columns: plan_info.lob, plan_info.payer, plan_info.payer_plan_id,
     comp_providers.payer_id
   - SQL: SELECT … FROM plan_info WHERE … (verbatim in next block)
```

The LLM consumes this top-down narrative directly. It still has the per-node
T2 cards below for verbatim citation.

**P3. System prompt: persona + protocol.** Replace today's terse system
prompt with a structured one that:

- Establishes the persona ("a senior engineer who reviews codebases for
  change-impact and architectural questions")
- Lays out the response protocol:
  1. Answer in 1–3 sentences directly addressing the question.
  2. Show the **Call chain** as a numbered list with edge types.
  3. Quote any **SQL / queries** verbatim in fenced blocks.
  4. List **affected entities** as clickable URNs.
  5. State **change-risk / blast-radius** if the question is about
     modification.
  6. Surface a **Confidence** rating (high / medium / low) with rationale.
  7. Add **Caveats** only if the graph is genuinely missing data.
- Has 3 few-shot examples spanning impact-analysis, trace-flow,
  explain-purpose intents.

**P4. User-message templating per intent.** Today the user message is just
the raw question + the rendered context. Switch to per-intent templates:

```
[intent-router output]
You are answering an IMPACT-ANALYSIS question about renaming `lob`.
Anchor entities: lob, getPayerCompetitors.
Required edge types in the answer: READS_COLUMN, WRITES_COLUMN, CALLS.

[user question]
"What breaks if I rename the lob column?"

[pre-walk]
…

[T2 entity cards]
…

[blast-radius summary]
…
```

Per-intent instructions sit at the top so the LLM knows what to optimise for.

### WS3 — Response layer: human-friendly with citations

**R1. Structured response schema.** Replace the free-form `answer: str` with
a typed response:

```python
class QueryResponse(BaseModel):
    summary: str                       # 1-3 sentences
    call_chain: list[CallChainStep]    # ordered
    sql_quotes: list[SqlBlock]         # verbatim with source URN
    affected_entities: list[Citation]  # name, urn, why_relevant
    change_risk: Optional[RiskAssessment]  # for impact questions
    confidence: Confidence             # high/med/low + rationale
    caveats: list[str]                 # genuine gaps only
    follow_up_questions: list[str]     # what the user might ask next
```

The frontend renders each section with the right widget (call-chain → flow
diagram, sql_quotes → syntax-highlighted code block, affected_entities →
clickable URN list).

**R2. Inline citations everywhere.** Every claim ends with `[<urn>]` so the
frontend can wire click-to-jump. The LLM is instructed:
*"Every factual claim about the codebase MUST end with the URN of the
node that supports it. If you can't cite a node, drop the claim."*

**R3. Calibrated confidence.** Confidence is not free-form — the LLM is given
a rubric:

- **high**: every cited node has confidence ≥ 0.9 and the call chain is
  fully connected
- **medium**: some nodes ≥ 0.7 OR call chain has a gap of 1 hop
- **low**: nodes < 0.7 OR multiple hops missing OR conflicting evidence

The rubric mirrors the relationship-extractor's confidence scale so the
whole pipeline speaks the same language.

**R4. Follow-up questions (agentic prompt).** The LLM produces 2–3
follow-up questions the user is likely to ask next, derived from the
answer's gaps. These render as one-click chips in the UI. Example after a
"how does X work" answer:

- "What tests cover X?"
- "What changes if I deprecate X?"
- "Who owns X?"

This converts a one-shot Q&A into a guided exploration flow — the
experience that makes Claude Code feel conversational.

**R5. Two-pass agent for hard questions (optional, cost-gated).** Item #10
from user's list. For intents `impact-analysis` and `trace-flow`, run a
two-pass loop:

- Pass 1 (cheap): retrieve + draft answer + identify "I'd need X to be
  more sure"
- Pass 2 (focused): fetch the specific X (e.g. the migration file
  defining `lob`'s nullability) and produce the final answer.

Cost-gated — only triggered when Pass 1's confidence < 0.7. Caps at one
extra retrieval round per question.

### Cross-cutting

**X1. Multi-pass relationship extraction (item #1).** Already in ADR-0042
(E8). No additional design here.

**X2. Cross-file call graph (item #2).** Already in ADR-0042 (E1). No
additional design here.

**X3. Annotation / test / frontend / ORM / migration extraction (items
3–6, 8).** Already in ADR-0042 (E2, E7, E6, E3, E5). No additional
design here.

**X4. Method-level freshness (item #7).** Already in ADR-0042 (E4). No
additional design here.

This ADR depends on ADR-0042 for the extraction-side enhancements; ADR-0043
is the matching set of changes on the storage / prompt / response side.

## Options Considered

### Option A — Implement everything in this ADR (full WS1+WS2+WS3)

| Dimension          | Assessment |
|--------------------|------------|
| Complexity         | High — touches storage, prompts, response model, frontend rendering |
| Cost (per query)   | +$0.005–0.01 (intent router + occasional 2-pass) |
| Cost (per index)   | +$0.02 (3 extra embeddings per node, one-time per change) |
| Quality lift       | Estimated 4–5x — moves from "I don't have enough info" to specific cited answers |
| Time to ship       | 4 PRs, ~3 days each in sequence |
| Team familiarity   | Familiar — same prompt-engineering patterns we've shipped |

Pros: closes ALL gaps the user surfaced; aligns with Claude Code / Antigravity quality bar.
Cons: 4 PRs of work; some user-facing schema churn (response shape changes).

### Option B — Ship only WS3 (response layer) first

| Dimension          | Assessment |
|--------------------|------------|
| Complexity         | Low — only renderer + system prompt |
| Cost lift          | $0 |
| Quality lift       | ~1.5–2x — answers become structured + cited but data quality unchanged |
| Time to ship       | 1 PR |

Pros: cheapest, fastest visible improvement.
Cons: doesn't fix the actual content gaps (still missing READS_COLUMN edges,
still single-pass); ceiling is low.

### Option C — Ship only WS1 (storage / dedup / inverse-edge)

| Dimension          | Assessment |
|--------------------|------------|
| Complexity         | Medium |
| Cost lift          | +$0.02 per index, $0 per query |
| Quality lift       | 2–3x for navigational queries; minimal for impact-analysis |
| Time to ship       | 2 PRs |

Pros: fixes the persistence-side bugs that cost us hours today (URN
fallback, edge dedup losing evidence).
Cons: prompt + response still suboptimal.

## Trade-off Analysis

The user's stated goal — "close to Claude Code / Antigravity for code
understanding" — only happens with **all three workstreams**. Option B
buys polish, Option C buys correctness, but neither alone gets to "feels
agentic and grounded".

The recommended sequence is:

1. **WS1.S3 (URN fix)** ships first as a hotfix — it's the source of latent
   bugs that will keep biting. PR-0043-1.
2. **WS3 (response layer)** ships second — biggest visible quality lift,
   one PR, no schema migration. PR-0043-2.
3. **WS1 remainder + WS2** ships in parallel as PR-0043-3 (storage) and
   PR-0043-4 (prompts).

This keeps each PR ≤ 1 day of review and ships visible value at every step.

## Consequences

**What becomes easier**

- "What breaks if I rename X" answers get a real call chain + cited columns.
- Hot-path nodes (top 50 by in-degree) have answers in <100ms (no graph walk).
- The LLM stops fabricating — every claim has a URN citation it can defend.
- Operators can debug retrieval by reading the per-intent template
  (no more guessing why a specific node didn't make it into context).

**What becomes harder**

- The response schema is breaking for any caller that consumes raw
  `answer: str`. Mitigation: keep `summary` as the str field, additive
  fields underneath; existing UI works on day 1.
- Three extra Qdrant collections per workspace = ~$0 storage but more
  ops moving parts. Mitigation: shared embedding model + batched upserts.
- Intent router adds ~200ms latency to every query. Mitigation: cache by
  (question_hash, workspace_id) for 5 minutes; common questions are free.

**What we'll need to revisit**

- Multi-pass agent (R5) is initially capped at 2 passes — long-tail hard
  questions might need ReAct-style 3+ pass loops. Track answer quality;
  open a follow-up ADR if Pass-2-still-not-confident rate > 10%.
- The 50-edge taxonomy from ADR-0042 + the response schema both name
  edge types — when the taxonomy grows, both must update in lock-step.
  Add a single source-of-truth `edge_types.py` module.

## Worked example: the lob-rename question

The user's running test case is *"How does getPayerCompetitors fetch
competitor data, what tables and columns does it read, and what would
break if I rename the lob column?"* This is the canonical
**impact-analysis** intent. With ADR-0043 fully shipped, the response
should look like:

```json
{
  "summary": "Renaming `plan_info.lob` will break 4 repository methods on the read path and 1 frontend filter. The lob column is the primary discriminator for line-of-business; every payer-competitor query filters on it.",
  "call_chain": [
    {"ord":1, "urn":"urn:cb:dev:code:network-iq-backend-java:component:getPayerCompetitors", "name":"getPayerCompetitors", "role":"controller", "edge_in":null, "annotations":["GetMapping"], "one_liner":"GET /v1/payers/{payerId}/competitors"},
    {"ord":2, "urn":"urn:cb:dev:code:…service:getPayerCompetitors", "name":"CompetitivenessService.getPayerCompetitors", "role":"service", "edge_in":"CALLS", "annotations":["Transactional"], "one_liner":"delegates to repository, applies viewBy filter"},
    {"ord":3, "urn":"urn:cb:dev:code:…repo:getPayerCompetitors",   "name":"CompetitivenessPlanRepository.getPayerCompetitors", "role":"repository", "edge_in":"CALLS", "annotations":[], "one_liner":"jOOQ DSL chain reading plan_info + comp_providers"},
    {"ord":4, "urn":"urn:cb:dev:code:…sql:plan_info_select",        "name":"plan_info SELECT", "role":"query", "edge_in":"CALLS", "annotations":[], "one_liner":"reads lob, payer, payer_plan_id, is_payer_level, is_current"}
  ],
  "sql_quotes": [
    {"source_urn":"urn:cb:dev:code:…repo:getPayerCompetitors", "language":"jooq",
     "body":"dsl.select(PLAN_INFO.PAYER_PLAN_ID, PLAN_INFO.PRODUCT_NAME, PLAN_INFO.PAYER, PLAN_INFO.LOB, PLAN_INFO.IS_PAYER_LEVEL).from(PLAN_INFO).where(planParts.condition().and(PLAN_INFO.IS_CURRENT.eq(true)))"}
  ],
  "affected_entities": [
    {"urn":"urn:cb:dev:data:…plan_info.lob",                  "name":"plan_info.lob",                "why_relevant":"primary column being renamed", "confidence":1.0},
    {"urn":"urn:cb:dev:code:…repo:getPayerCompetitors",       "name":"getPayerCompetitors (repo)",   "why_relevant":"reads lob directly in WHERE clause", "confidence":0.95},
    {"urn":"urn:cb:dev:code:…repo:fetchAllCompetitors",       "name":"fetchAllCompetitors",          "why_relevant":"reads lob for filter", "confidence":0.9},
    {"urn":"urn:cb:dev:code:…dto:NiqAPIRequest.lob",          "name":"NiqAPIRequest.lob",            "why_relevant":"DTO field marshalled from query param", "confidence":1.0},
    {"urn":"urn:cb:dev:code:…fe:CompetitorTable",             "name":"CompetitorTable",              "why_relevant":"frontend filter chip references lob", "confidence":0.7}
  ],
  "change_risk": {
    "level":"high", "reason":"lob is the primary line-of-business discriminator; renaming requires coordinated DB migration + ORM model + DTO field + frontend filter changes",
    "blast_radius_count":12, "sample_affected":[/*top 5*/]
  },
  "confidence": {"level":"high", "rationale":"every cited node has confidence >= 0.9; SQL is verbatim from the repository's jOOQ DSL"},
  "caveats": [],
  "follow_up_questions": [
    "What test files exercise plan_info.lob?",
    "Show me the migration that introduced lob",
    "Is lob nullable, and what's the default value?"
  ]
}
```

The frontend renders this as: a 1-paragraph summary at the top, a vertical
call-chain widget, the SQL block syntax-highlighted, an "Affected Entities"
list with click-to-jump URN chips, a red "high-risk change" banner, and three
follow-up question chips.

**Three reasons today's brain can't produce this answer yet:**

1. The relevant nodes (`CompetitivenessPlanRepository`) don't carry
   `query_text` because the original extraction predates today's
   "repo bypasses skeleton-prefilter" fix (commit `52c547337`). A FRESH
   `brain enrich` is required to populate them.
2. There's no READS_COLUMN edge from the repository method to
   `plan_info.lob` because the SQL→table edge extraction (commit
   `52c547337`) only runs at extraction time, not on `rebuild-from-json`.
   Same fix: fresh enrich.
3. The current renderer drops fields the LLM needs (fixed by today's
   `compressor.py` commit) and the system prompt doesn't ask for the
   structured response schema (fixed by PR-0043-2 in this ADR).

**Immediate action that gets you 70% of the way there without ADR-0043
shipping**: run a fresh enrich now (the extraction-side fixes from
today's session are already on `main`), then ask the lob question
again. The answer will be substantially richer because it'll have
real SQL bodies and READS_COLUMN edges to cite.

```bash
cd ~/Documents/Claude/Projects/company-brain/company-brain-ai && \
.venv/bin/brain enrich \
  --repo /Users/chinmayjadhav/Documents/network-iq-backend-java \
  --workspace-id 00000000-0000-0000-0000-000000000001
```

The remaining 30% (structured response + intent router + pre-walk + UI
widgets) is what ADR-0043 ships across PRs 1–5.

## Action Items

1. [ ] PR-0043-1: hotfix URN scheme (WS1.S3) — kill `monorepo` fallback,
       require `repo` + `entity_type` in every URN constructor.
2. [ ] PR-0043-2: response layer (WS3) — typed `QueryResponse`, structured
       renderer, follow-up questions, citation enforcement, calibrated
       confidence.
3. [ ] PR-0043-3: storage layer remainder (WS1.S1 + S2 + S4 + S5) —
       multi-granularity embeddings, inverse-edge MV, confidence-weighted
       dedup, hot-node answer cards.
4. [ ] PR-0043-4: prompt layer (WS2) — intent router, pre-walk renderer,
       persona system prompt, per-intent user-message templates.
5. [ ] PR-0043-5: end-to-end acceptance tests on the three fixture repos
       (Java, Python, TypeScript) from ADR-0042. Asserts the lob-rename
       question gets a specific cited answer.
6. [ ] Once shipped: update the Cowork demo Makefile so the new query
       output format renders correctly in the UI.
