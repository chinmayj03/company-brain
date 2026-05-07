# API Context Builder Pipeline — System Design

> This document designs the end-to-end pipeline that takes a single API endpoint as input, collects all available signals from git history across multiple repositories, combines minimal user annotations, and produces a richly annotated dependency graph with business context — queryable in natural language.

---

## 1. What the Pipeline Does (Plain Language)

An engineer points at an API endpoint: `POST /payments/charge`.

The pipeline automatically:
1. Finds every line of code related to that endpoint — backend handler, middleware, DB queries, frontend call sites, shared types
2. Reconstructs the full history of how that endpoint evolved across commits, PRs, and tickets
3. Asks the engineer to annotate only what the code cannot tell us — the business "why" behind specific commits
4. Feeds everything to an LLM that extracts a structured dependency graph and annotates every node with business context
5. Makes the result queryable: "What breaks in the UI if I change this field?" gets a grounded, accurate answer

---

## 2. Pipeline Overview

```
INPUT: API endpoint path + repos + target branch
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 1: MULTI-REPO CONTEXT COLLECTOR                  │
│                                                         │
│  For each repo (backend, frontend, shared):             │
│  ├─ Find definition sites (route, handler, controller)  │
│  ├─ Find call sites (fetch, axios, gRPC client)         │
│  ├─ Find type/schema definitions                        │
│  └─ Walk git log → collect commits + diffs + PR links   │
│                                                         │
│  Output: CommitTimeline (ordered, multi-repo)           │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 2: USER ANNOTATION LAYER (Minimal Input)         │
│                                                         │
│  Show engineer: commit timeline + diffs                 │
│  Ask: annotate any commit where you know the business   │
│        "why" that the code doesn't explain              │
│                                                         │
│  Annotation is optional per commit.                     │
│  LLM fills in the rest from commit messages + PR text.  │
│                                                         │
│  Output: AnnotatedTimeline                              │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 3: LLM EXTRACTION PIPELINE                       │
│                                                         │
│  Pass 1 — Entity Extraction                             │
│    Extract: endpoints, functions, components, tables,   │
│    columns, types, config, feature flags                │
│                                                         │
│  Pass 2 — Relationship Extraction                       │
│    Extract: which entity depends on which               │
│    Typed edges: CALLS, READS, RENDERS, VALIDATES, etc.  │
│                                                         │
│  Pass 3 — Business Context Synthesis                    │
│    For each node: why does this exist?                  │
│    What are the invariants? What is risky to change?    │
│    (Uses user annotations + PR descriptions + commits)  │
│                                                         │
│  Pass 4 — Conflict & Gap Detection                      │
│    What does the code do that no one explained?         │
│    What did someone explain that contradicts the code?  │
│                                                         │
│  Output: StructuredGraph + ContextAnnotations           │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 4: GRAPH POPULATION                              │
│                                                         │
│  Upsert nodes and edges to the dependency graph         │
│  (PostgreSQL schema defined in SYSTEM_DESIGN.md)        │
│  Attach business context to node_context table          │
│  Compute embedding vectors for semantic search          │
│                                                         │
│  Output: Populated graph for this API subtree           │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 5: QUERY INTERFACE                               │
│                                                         │
│  Graph traversal + LLM synthesis                        │
│  "What breaks in the UI if I rename this field?"        │
│  → Traverse graph → Collect context → LLM answers       │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Stage 1: Multi-Repo Context Collector

### 3.1 Finding the API across repositories

Given `POST /payments/charge`, the collector must find every code artifact related to this endpoint across multiple repos.

**Backend repo — finding the definition:**
```
Search patterns (in order of confidence):
  1. Route definition:  router.post('/payments/charge', ...)
  2. Controller method: @Post('/payments/charge')
  3. Handler function:  chargePayment, handleCharge, processCharge
  4. OpenAPI spec file: paths./payments/charge.post
  5. Test files:        describe('POST /payments/charge')
```

**Frontend repo — finding call sites:**
```
Search patterns:
  1. Exact path string: '/payments/charge', "/payments/charge"
  2. API client method: api.post('payments/charge'), apiClient.charge()
  3. Generated client:  PaymentsApi.charge(), chargeEndpoint.call()
  4. Constant references: CHARGE_ENDPOINT, PAYMENT_CHARGE_PATH
  5. E2E test files:    cy.request('POST', '/payments/charge')
```

**Shared/libs repo — finding types and contracts:**
```
Search patterns:
  1. Request/response types: ChargeRequest, ChargeResponse, ChargePayload
  2. Validation schemas: chargeSchema, chargeValidator
  3. Proto definitions: rpc Charge (ChargeRequest) returns (ChargeResponse)
```

Each found file becomes a **context root** — the collector walks its git history.

---

### 3.2 Git History Walk

For each context root file, collect the ordered commit timeline:

```
For each file:
  git log --follow --diff-filter=AMRD -p -- <file>

This gives:
  - Every commit that touched this file (including renames via --follow)
  - The full diff at each commit
  - Commit hash, timestamp, author, message

Then enrich each commit:
  - GitHub API: fetch PR title, body, labels, reviewers, linked issues
  - Jira/Linear API (if connected): fetch ticket summary and description
  - CI API: fetch build status and test results at that commit
```

The result is a **CommitEvent** per commit per file:

```typescript
interface CommitEvent {
  commit_hash: string;
  timestamp: string;
  author: string;
  message: string;
  repo: string;
  file_path: string;
  diff: string;               // unified diff of just this file
  pr_title?: string;
  pr_body?: string;           // PR description (can be very long)
  pr_url?: string;
  linked_tickets?: string[];  // Jira/Linear ticket IDs
  ticket_summaries?: string[]; // Fetched ticket text
  test_status?: 'pass' | 'fail' | 'skip';
}
```

---

### 3.3 Cross-Repo Timeline Merge

Commits from multiple repos are merged into a single chronological timeline. The key insight: **a frontend change and a backend change that shipped together are often in the same PR or happened within 24 hours of each other.** The merger uses timestamp proximity and PR title similarity to cluster related cross-repo commits.

```typescript
interface CommitCluster {
  cluster_id: string;
  approximate_date: string;
  commits: CommitEvent[];      // From all repos, clustered together
  cluster_reason: 'same_pr' | 'same_ticket' | 'time_proximity';
  combined_pr_bodies: string[];
}
```

The output is an ordered list of `CommitCluster` objects — this is the **CommitTimeline**.

---

### 3.4 Current State Snapshot

Alongside the history, the collector produces a snapshot of the API's **current state** on the target branch:

```typescript
interface ApiSnapshot {
  // Backend
  handler_file: string;
  handler_function_name: string;
  handler_code: string;          // Full function body
  middleware_chain: string[];    // Auth, validation, rate-limit middleware
  db_queries: string[];          // SQL/ORM queries found in the handler
  
  // Contract
  request_schema: JSONSchema;    // Parsed from OpenAPI or inferred from code
  response_schema: JSONSchema;
  
  // Frontend
  call_sites: {
    file: string;
    component_name: string;
    line: number;
    context_snippet: string;     // 10 lines around the call
  }[];
  
  // Types
  shared_types: {
    name: string;
    definition: string;
  }[];
}
```

---

## 4. Stage 2: User Annotation Layer

### 4.1 Design Principle: Anchor annotations to commits, not free text

Instead of asking "describe this API," we show the engineer the commit timeline and ask them to annotate specific commits where the code alone doesn't explain the business reasoning. This is:
- **Lower friction** — engineer looks at what actually changed, not a blank page
- **Higher quality** — annotation is grounded in a specific change, not vague recollection
- **Incrementally collectible** — engineer can annotate commit 1-4 now, return for 5-8 later
- **Verifiable** — the LLM can check if the annotation is consistent with the diff

### 4.2 Annotation UI (VS Code Extension)

```
┌────────────────────────────────────────────────────────┐
│  POST /payments/charge — Commit History                │
│  Showing 12 commits across backend + frontend repos    │
├────────────────────────────────────────────────────────┤
│                                                        │
│  ● Jan 15, 2024  [abc1234]  backend                   │
│    "Add charge endpoint"  — Priya                      │
│    Changed: src/routes/payments.ts (+82 -0)            │
│    PR: "Stripe integration — initial charge flow"       │
│    [Expand diff ▼]    [Add annotation ✎]               │
│                                                        │
│  ✎ Jan 18, 2024  [def5678]  backend + frontend        │
│    "Fix amount validation"  — Raj                      │
│    Changed: payments.ts (+12 -4), ChargeForm.tsx (+6 -2)│
│    PR: "Amount must be in cents not dollars"           │
│    ┌──────────────────────────────────────────────┐   │
│    │ Your annotation:                              │   │
│    │ "Stripe requires amounts in smallest unit.   │   │
│    │  We had a bug where we were passing $10.00   │   │
│    │  and Stripe charged 10 cents. Customer       │   │
│    │  complaints triggered this fix."             │   │
│    └──────────────────────────────────────────────┘   │
│                                                        │
│  ● Mar 02, 2024  [ghi9012]  backend                   │
│    "Add idempotency key support"  — Priya              │
│    [Expand diff ▼]    [Add annotation ✎]               │
│                                                        │
│  Context quality: ████████░░ 78%                       │
│  Commits annotated: 4 of 12                            │
│  [LLM can infer the rest — continue ▶]                 │
└────────────────────────────────────────────────────────┘
```

The "Context quality" bar reflects how much of the API history has been either human-annotated or has rich PR/ticket descriptions that the LLM can use. It is not a blocker — the engineer can proceed at any quality level.

### 4.3 Annotation Data Model

```typescript
interface CommitAnnotation {
  commit_hash: string;
  workspace_id: string;
  author_email: string;         // Who annotated, not who committed
  annotation_type: 
    | 'business_context'        // Why was this change made?
    | 'invariant'               // A rule that must always be true
    | 'risk_flag'               // "Be careful if you change this"
    | 'deprecation_note'        // "This is being phased out"
    | 'external_dependency';    // "This syncs with Stripe's behavior"
  text: string;
  created_at: string;
  applies_to_fields?: string[]; // Optional: specific fields this annotation covers
}
```

---

## 5. Stage 3: LLM Extraction Pipeline

### 5.1 Context Window Strategy

A busy API with 3 years of history can have hundreds of commits, each with diffs spanning hundreds of lines. This does not fit in a single LLM context window. The strategy:

```
Full CommitTimeline
        │
        ▼
┌───────────────────────────────────────┐
│  CHUNKING STRATEGY                    │
│                                       │
│  Recent 6 months: verbatim diffs      │
│  6-18 months:     summarised diffs    │
│  > 18 months:     summary only        │
│                                       │
│  Annotated commits: always verbatim   │
│  (user annotation = high signal)      │
└───────────────────────────────────────┘
        │
        ▼
  Multiple parallel LLM calls
  (one per commit cluster or file)
        │
        ▼
  Merge extracted entities
  (dedup by name/signature)
        │
        ▼
  Single synthesis call
  (entities + relationships + context)
```

**Chunking rules:**
- Each LLM call processes one `CommitCluster` plus the current file snapshot
- Max tokens per call: 80K (leaves room for system prompt and output)
- If a cluster is too large, split by file (one call per repo per cluster)
- Always include the API's current state snapshot in every call for grounding

---

### 5.2 Pass 1 — Entity Extraction

**System prompt (condensed):**
> You are a code analyst. Given a git diff and the current file state, extract every named entity that is part of this API's implementation. Return structured JSON only. Do not explain. Extract: endpoints, functions, classes, database tables, database columns, request fields, response fields, frontend components, config keys, feature flags, shared types.

**Input per call:**
```
Current API snapshot: [handler code, schema, call sites]
Commit cluster: [commits with diffs, PR descriptions, ticket text, user annotations]
```

**Output per call:**
```json
{
  "entities": [
    {
      "type": "function",
      "name": "chargePayment",
      "file": "src/services/payment.service.ts",
      "repo": "backend",
      "signature": "chargePayment(userId: string, amount: number, currency: string): Promise<ChargeResult>",
      "first_appeared_commit": "abc1234",
      "last_modified_commit": "def5678"
    },
    {
      "type": "db_column",
      "name": "transactions.amount_cents",
      "repo": "backend",
      "note": "Renamed from amount_dollars in commit def5678"
    },
    {
      "type": "frontend_component",
      "name": "ChargeForm",
      "file": "src/components/payments/ChargeForm.tsx",
      "repo": "frontend",
      "renders_field": "amount"
    }
  ]
}
```

Entities from all parallel calls are merged by `(type, name, repo)` key, deduplicating and taking the union of metadata.

---

### 5.3 Pass 2 — Relationship Extraction

With the entity list fixed, a second pass extracts the edges between them.

**Input:**
```
Entity list (from Pass 1)
Current API snapshot (handler code + call sites)
Commit diffs (summarised)
```

**Output:**
```json
{
  "relationships": [
    {
      "from": "chargePayment",
      "from_type": "function",
      "edge_type": "READS_COLUMN",
      "to": "transactions.amount_cents",
      "to_type": "db_column",
      "confidence": 0.95,
      "evidence": "ORM query in chargePayment line 34: this.db.transactions.findOne({amount_cents})"
    },
    {
      "from": "ChargeForm",
      "from_type": "frontend_component",
      "edge_type": "CALLS_ENDPOINT",
      "to": "POST /payments/charge",
      "to_type": "api_endpoint",
      "confidence": 0.99,
      "evidence": "axios.post('/payments/charge', payload) in ChargeForm.tsx line 87"
    },
    {
      "from": "ChargeForm",
      "from_type": "frontend_component",
      "edge_type": "RENDERS_FIELD",
      "to": "charge.amount",
      "to_type": "schema_field",
      "confidence": 0.88,
      "evidence": "Input field name='amount' in ChargeForm render method"
    }
  ]
}
```

---

### 5.4 Pass 3 — Business Context Synthesis

This is the highest-value pass. For each extracted entity, the LLM synthesises a business context block using: commit history, PR descriptions, ticket text, and user annotations.

**Input per entity:**
```
Entity: transactions.amount_cents (db_column)
Related commits: [abc1234 (created column), def5678 (renamed from amount_dollars)]
Related annotations: ["Stripe requires amounts in smallest unit. We had a bug..."]
Related PR text: ["Amount must be in cents not dollars — customer complaints triggered this fix"]
```

**Output per entity:**
```json
{
  "entity": "transactions.amount_cents",
  "business_context": {
    "purpose": "Stores the transaction amount in integer cents (smallest currency unit) to avoid floating-point precision errors and to match Stripe's API contract.",
    "history_summary": "Originally stored as amount_dollars (float). Renamed in January 2024 after a production bug where $10.00 was passed to Stripe as 10 cents, causing incorrect charges. Customer complaints triggered the fix.",
    "invariants": [
      "Value must always be a positive integer (cents, not dollars)",
      "Must match Stripe's amount field exactly — do not convert before sending to Stripe",
      "Zero is not a valid value — rejected at the validation layer"
    ],
    "change_risk": "HIGH — changing this field affects ChargeForm display logic, Stripe API calls, and all transaction reporting queries. Coordinate with the payments team before modifying.",
    "owner_team": "payments-team",
    "source_confidence": "high"  // High = user annotation present; medium = PR text; low = inferred
  }
}
```

---

### 5.5 Pass 4 — Gap and Conflict Detection

The final pass identifies what the LLM could not explain and where annotations contradict the code.

**Output:**
```json
{
  "gaps": [
    {
      "entity": "chargePayment",
      "gap_type": "unexplained_behaviour",
      "description": "The function retries failed charges up to 3 times with exponential backoff. No commit message, PR description, or annotation explains why 3 retries specifically, or what failure modes trigger retry vs. immediate failure.",
      "suggested_question": "Why does chargePayment retry exactly 3 times? Is this a Stripe recommendation or an internal decision?"
    }
  ],
  "conflicts": [
    {
      "entity": "charge.amount",
      "conflict_type": "annotation_vs_code",
      "description": "User annotation on commit ghi9012 says 'amount is validated client-side only.' However, the current handler code includes server-side Zod validation: z.number().int().positive(). Either the annotation is outdated or there was a later addition.",
      "resolution_needed": true
    }
  ]
}
```

Gaps and conflicts are surfaced to the engineer as follow-up questions, closing the minimal-input loop.

---

## 6. Stage 4: Graph Population

After the four LLM passes, the extracted graph is written to the PostgreSQL store defined in `SYSTEM_DESIGN.md`.

```
Entities → nodes table
  - node_type: 'Function' | 'SchemaField' | 'DatabaseColumn' | 'FrontendComponent' | etc.
  - external_id: "<repo>/<file>/<name>"
  - metadata: {signature, file_path, repo, first_appeared_commit}

Relationships → edges table
  - edge_type: 'CALLS_ENDPOINT' | 'READS_COLUMN' | 'RENDERS_FIELD' | etc.
  - confidence: from LLM output
  - source: 'llm_extraction'
  - metadata: {evidence_snippet}

Business context → node_context table
  - context_type: 'business_context'
  - body: JSON.stringify(business_context_block)
  - Encrypted at rest (AES-256-GCM as per ADR-003)

Embeddings → computed after population
  - Embed each node's name + context using text-embedding-3-large
  - Store in pgvector column on nodes table
  - Used for semantic search ("find all nodes related to payment amounts")
```

---

## 7. Stage 5: Query Interface

### 7.1 Query Pipeline

```
User query: "What breaks in the UI if I rename amount_cents to amount_in_cents?"
                │
                ▼
  Step 1: Entity Recognition
  LLM identifies "amount_cents" → maps to node "transactions.amount_cents"
                │
                ▼
  Step 2: Graph Traversal
  Blast radius query from node "transactions.amount_cents"
  Returns: chargePayment (READS_COLUMN) → POST /payments/charge (EXPOSES) 
         → ChargeForm (CALLS_ENDPOINT) → amount input field (RENDERS_FIELD)
                │
                ▼
  Step 3: Context Retrieval
  Fetch business_context for all nodes in blast radius
  Fetch recent commits that touched these nodes
                │
                ▼
  Step 4: Answer Synthesis (LLM)
  Input: query + blast radius nodes + their business context + recent history
  Output: grounded, specific answer
                │
                ▼
  "Renaming amount_cents to amount_in_cents affects:
   1. chargePayment() in payment.service.ts — reads this column directly
   2. The ORM model in transaction.model.ts — column name must match
   3. ChargeForm.tsx — sends {amount_cents} in the POST body; field name 
      must match the API's Zod schema
   4. All reporting queries in reports.service.ts — 3 queries reference 
      this column by name.
   
   ⚠️  The payments team (Priya) should be consulted. This column was 
   deliberately named in cents after a production bug in Jan 2024 — 
   see the business context annotation for full history."
```

### 7.2 Query Types the System Supports

| Query Type | Example | Mechanism |
|---|---|---|
| Impact analysis | "What breaks if I change this field?" | Blast radius traversal + context synthesis |
| Ownership lookup | "Who do I talk to before changing this?" | OWNS edge traversal |
| History explanation | "Why was this implemented this way?" | node_context retrieval + LLM summarise |
| Risk assessment | "Is this safe to change?" | Invariants + risk flags from context |
| Cross-repo mapping | "What frontend components use this API?" | CALLS_ENDPOINT + RENDERS_FIELD traversal |
| Onboarding guide | "Explain this API to me as if I'm new" | Full subtree retrieval + LLM explain |
| Semantic search | "Find all nodes related to payment amounts" | pgvector similarity search |

---

## 8. Incremental Updates

When new commits land on the target branch, the pipeline should process only the delta — not rebuild from scratch.

```
New commit webhook (GitHub) arrives
          │
          ▼
Diff processor: which context roots does this commit touch?
  - Is the changed file a handler, call site, type, or schema?
  - If yes → trigger Stage 1 for just the changed files
          │
          ▼
Re-run Stage 3 for affected clusters only
  - Pass 1: extract any NEW entities not already in the graph
  - Pass 2: update edges for changed functions
  - Pass 3: update business context if PR description adds new info
  - Pass 4: re-check for new gaps or resolved conflicts
          │
          ▼
Upsert changes to graph
  - New nodes: insert
  - Changed edges: update last_seen + confidence
  - Removed code: mark edges as pruned (not deleted)
  - Invalidate Redis cache for affected node subtree
```

The incremental cost is low: a typical commit touches 1-3 files. The LLM processing is bounded to those files' clusters, not the full API history.

---

## 9. Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA FLOW                                    │
│                                                                      │
│  GitHub API ──────────────────┐                                      │
│  (commits, PRs, blame)        │                                      │
│                               │                                      │
│  Jira / Linear API ───────────┼──► Context Collector                │
│  (ticket summaries)           │         │                            │
│                               │         │ CommitTimeline             │
│  Engineer Annotation ─────────┘         │                            │
│  (via VS Code extension)                │                            │
│                               ┌─────────▼──────────┐                │
│                               │  Chunker           │                │
│                               │  (by cluster/file) │                │
│                               └────────┬───────────┘                │
│                                        │                            │
│                         ┌──────────────┼──────────────┐             │
│                         │              │              │             │
│                         ▼              ▼              ▼             │
│                     LLM Pass 1    LLM Pass 2    LLM Pass 3         │
│                     (entities)  (relationships)  (context)          │
│                         │              │              │             │
│                         └──────────────┴──────────────┘             │
│                                        │                            │
│                               LLM Pass 4 (gaps/conflicts)          │
│                                        │                            │
│                         ┌──────────────▼─────────────┐              │
│                         │      Graph Population       │              │
│                         │   nodes + edges + context   │              │
│                         │   + pgvector embeddings     │              │
│                         └──────────────┬──────────────┘              │
│                                        │                            │
│                    ┌───────────────────┤                            │
│                    │                   │                            │
│             ┌──────▼──────┐   ┌────────▼────────┐                  │
│             │  VS Code     │   │  Web Dashboard  │                  │
│             │  Query API   │   │  Service Map    │                  │
│             └─────────────┘   └─────────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. LLM Model Selection

Different passes have different cost/quality tradeoffs.

| Pass | Recommended Model | Reason |
|---|---|---|
| Pass 1 — Entity Extraction | Claude Haiku / GPT-4o-mini | Structured JSON extraction, high volume, cost-sensitive |
| Pass 2 — Relationship Extraction | Claude Sonnet | Needs code reasoning; more subtle than entity extraction |
| Pass 3 — Business Context Synthesis | Claude Opus / GPT-4o | Highest reasoning demand; runs once per entity, not per commit |
| Pass 4 — Gap Detection | Claude Sonnet | Analytical reasoning, not generation |
| Query Interface (Stage 5) | Claude Opus | User-facing; quality matters most |

Use Haiku aggressively for Pass 1 (many parallel calls across commit clusters). Reserve Opus for synthesis and user-facing queries. Estimated cost per API endpoint processed: $0.20–$0.80 depending on history depth.

---

## 11. Key Design Decisions and Trade-offs

### Decision 1: Four separate LLM passes vs. one mega-prompt

**Why four passes?** A single prompt asking for "entities, relationships, context, and gaps" produces lower quality output than four focused prompts. Each pass knows exactly what it needs to produce and can be independently evaluated and retried if it fails. The cost of extra calls is offset by quality and debuggability.

**Trade-off:** More API calls, more latency (can be parallelised within each pass), slightly higher cost.

### Decision 2: Anchor annotations to commits, not free text

**Why commit-anchored?** Free-text documentation goes stale instantly. An annotation on commit `def5678` is permanently true — it explains why that specific change was made. The relationship between annotation and change is immutable and can be verified by anyone reading the diff.

**Trade-off:** Requires showing the engineer the commit diff, not just a blank text field. Small UX investment, large quality gain.

### Decision 3: LLM fills gaps rather than blocking on user input

**Why?** Requiring complete annotation before proceeding kills adoption. Most PRs have enough text in their descriptions and commit messages for the LLM to infer intent. User annotations are highest signal; PR text is medium; commit messages are low. The LLM uses all three, ranked by signal quality.

**Trade-off:** Some inferred context will be wrong. The gap detector in Pass 4 surfaces uncertain inference for human review.

### Decision 4: Embeddings stored in pgvector alongside structured graph

**Why?** Semantic search ("find all nodes related to payment amounts") requires vector similarity. Storing embeddings in Postgres via pgvector avoids a second vector database (Pinecone, Weaviate). The tradeoff is lower query throughput than a dedicated vector store, which is acceptable at our scale.

---

## 12. Open Questions

1. **Code execution vs. static analysis:** The entity extractor identifies DB queries from ORM calls by reading code. But complex dynamic queries (query builders, runtime-constructed strings) may be missed. Should we add a query profiler (runtime instrumentation) to catch these? This would require the agent to instrument the application — a higher-trust requirement.

2. **Frontend framework diversity:** Finding call sites in React, Vue, and Angular requires different AST patterns. The initial implementation should handle React. How much framework diversity is needed before launch?

3. **Handling large diffs:** A file-wide refactor (rename a variable throughout a 2,000-line file) produces a massive diff that is mostly noise. The chunker should detect and summarise bulk-rename commits differently from feature commits. What heuristic distinguishes them? (Candidate: if > 60% of lines change but no functions are added/removed, classify as refactor.)

4. **Annotation incentive:** Engineers annotate commits because it benefits their future selves and their team. But annotation takes time. Should the product offer an "auto-annotate" mode where the LLM drafts an annotation from PR text and the engineer approves/edits? This reduces the annotation effort to a review task.

5. **Multi-language support:** The current design assumes TypeScript/JavaScript. Python (FastAPI, Django), Java (Spring), and Go (gin, chi) have different patterns for route definitions and ORM queries. What is the minimum viable language set for launch?
