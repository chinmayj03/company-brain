# First-run UI walkthrough — POST endpoint, business context quality

> You're seeing the product for the first time. This walks you through every
> surface that's built today, what each shows, what powers it, and what the
> UI does NOT yet expose.
>
> **Goal:** understand the product manually before automating any of it,
> then evaluate business context extraction quality on a POST endpoint.

---

## 1. The honest feature map

### Surfaces that DO exist

| Where | What you see | Backend powering it |
|---|---|---|
| **API Explorer** (`/api-explorer`) | Form to trigger the pipeline; live stage progress; result panel | Java `/v1/pipeline/start` → Python orchestrator → Stages 0a–5 |
| **Service Map / Dashboard** (`/`) | Force-directed dependency graph; click nodes to inspect | Postgres `nodes` + `edges` via Java REST API |
| **Entity detail panel** (click any node) | Metadata, file:line, signature, related entities, business context | Postgres `nodes.metadata` + `node_context` rows |
| **Blast Radius Panel** (visible on entity detail) | What breaks if this entity changes; 2-hop graph | Java `BlastRadiusService` (Postgres recursive lookup) |
| **Annotation Editor** | Add manual business context / invariants / risk flags to any node | Inserts into `node_context` with `context_type='user_annotation'` |
| **Commit Timeline** (visible on entity detail) | Git commits that touched this entity | `git_collector.py` output stored in `node_context` |
| **Ask page** (`/ask` or `/query`) | Natural-language Q&A grounded in the graph | Python `/query` → retrieve nodes → Anthropic |

### Surfaces that DON'T exist in React but are accessible

| Where | What you see | URL |
|---|---|---|
| **Neo4j Browser** | The structural call graph (function-level) | `http://localhost:7474` (user `neo4j` / pass `password`) |
| **Qdrant Web Dashboard** | Vector collections (will be empty until ADR-0015 ships) | `http://localhost:6333/dashboard` |
| **Langfuse** | Every LLM call: provider, model, tokens, cost, latency, prompt | `http://localhost:3001` |
| **Postgres direct** | Raw entities + business context | `make db-shell` |
| **Swagger / FastAPI docs** | All Python AI endpoints | `http://localhost:8000/docs` |
| **Java actuator** | Spring Boot health / metrics | `http://localhost:8080/actuator/health` |

### Surfaces that are DESIGNED but NOT built (Stage 1 ADRs)

| Concept | Where it lives in design | Status today |
|---|---|---|
| Smart-zone assembled context (T0/T1/T2 with token budget) | ADR-0018 | Designed only; `/query` uses simpler retrieve-then-prompt |
| Brain diff view (`git diff .brain/`) | Mentioned in `MIGRATION-…md` | Not built; `.brain/` directory itself is ADR-0012 |
| URN identity inspector (one URN, three stores) | ADR-0013 | Not built; Postgres uses `external_id`, Neo4j uses URN |
| MCP stdio server (Claude Code attachable) | ADR-0019 | Not built; you'd integrate via the Python `/query` REST endpoint instead |
| First-class assumption / business_context as graph nodes | ADR-0017 | Not built; today these live as `node_context` rows |
| Function-level entities in semantic graph | ADR-001 (root) | Not built in Postgres; partially in Neo4j via cb-api's CoreTsExtractor |

### Internal to pipeline (you BENEFIT, you don't SEE)

| Component | Where it runs | How you'd inspect it |
|---|---|---|
| L1/L2/Main-Memory context hierarchy | `pipeline/context_hierarchy.py` + `shared_context_accumulator.py` + `context_manager_agent.py` | Tail `logs/ai.log` and search for `L2 context after extraction:` — you'll see the domain glossary, service registry, patterns |
| T0/T1 memory tokens | `pipeline/memory_tokenizer.py` (Stage 3.5) | They're stored in `nodes.metadata.t0` and `.t1`; SQL: `SELECT name, metadata->>'t0' AS t0, metadata->>'t1' AS t1 FROM nodes LIMIT 5` |
| Intent contexts (FunctionContext per function) | `pipeline/intent_synthesizer.py` (Stage 1.5) | `nodes.metadata.functionContext` JSON. Click an entity in the UI → look at metadata blob |
| Anthropic prompt caching | `llm/anthropic_provider.py` | Visible in Langfuse: input tokens drop ~90% after first call in a session |

---

## 2. Manual walkthrough — your POST endpoint, end to end

This is the path to take on your first run. Don't skip steps. Each one teaches you what the next is doing.

### Step 0 — Have one POST endpoint in mind

Pick a real POST endpoint from your `network-iq-backend-java` repo. Note:
- The path: e.g. `/api/competitiveness/score`.
- The handler class: e.g. `NiqController`.
- The handler method: e.g. `computeScore`.
- One line of *what it does* in plain English: e.g. "computes a competitiveness score for a payer based on uploaded data."

Keep that English description handy — at the end you'll compare the brain's
business context output against your own one-liner. Quality assessment is
that simple: did the brain land on roughly your sentence, with details you
forgot or didn't realise were in the code?

### Step 1 — Discover endpoints in your repo (no LLM cost)

```bash
make -f Makefile.demo discover
```

Confirms the regex finds your POST endpoint. If it doesn't show up, the
extractor's regex won't either — you'll have to either fix the regex in
`code_tracer.py` or pick a differently-routed endpoint.

### Step 2 — Trigger the pipeline from API Explorer

Open `http://localhost:5173`. Click **API Explorer** in the navigation.

Fill in:
- **Repo path:** `/Users/chinmayjadhav/Documents/network-iq-backend-java`
- **Branch:** `main`
- **Endpoint path:** your POST path (e.g. `/api/competitiveness/score`)
- **HTTP method:** `POST`

Click **Run pipeline**.

#### What you'll see in real time

A timeline panel updates every ~2 s with the current stage. Mentally map
each stage to its job:

| Stage | Emoji | What it's doing | Approx duration |
|---|---|---|---|
| `0a` | 🔍 | Code tracing — NavigatorAgent walks call chain (LLM) | 30–90 s |
| `0b` | (parallel) | Git history collection (no LLM) | 10–60 s |
| `0c` | ⚡ | Freshness check — which files have changed (no LLM) | <1 s |
| `1` | 🧠 | Entity extraction — one LLM call per code unit | 30–120 s |
| `1.4` | 🔍 | Dependency expansion — find missed collaborators | 10–60 s |
| `1.5` | 💡 | Intent synthesis — business meaning per function (LLM) | 30–90 s |
| `1.6` | 🔌 | Import-graph CALLS edges (no LLM) | <2 s |
| `2` | 🔗 | Relationship extraction (LLM) | 20–60 s |
| `3` | 📖 | **Business context synthesis** (LLM) | 30–90 s |
| `3.5` | 🧩 | T0/T1 memory token generation (no LLM) | <1 s |
| `4` | 🔎 | Gap detection (LLM) | 10–30 s |
| `5` | 💾 | Persist to Postgres + Neo4j (via cb-api) | 5–15 s |

**For your POST endpoint specifically, watch Stage 1 closely.** The
extractor should pull out:
- The handler method itself (`ApiEndpoint`)
- The request body DTO and its fields (`SchemaField` rows like
  `RequestDto.fieldName`)
- Any `@Valid` validation logic (becomes `Function` entities with
  assumptions in their metadata)
- Service classes the handler delegates to (`Class` + `Function`)
- Database tables / queries the persistence layer touches (`DatabaseTable`,
  `DatabaseQuery`, `DatabaseColumn`)
- External service calls (`ExternalService`)

If Stage 1 returns <8 entities for a POST endpoint that has a real
controller + service + repo, the NavigatorAgent didn't reach the bottom of
the chain. Either it ran out of turns or your service uses a non-standard
pattern. Check `logs/ai.log` — search for `L2 context after extraction:` to
see what it found.

#### What the result panel shows when it's done

```
Pipeline complete
─────────────────
entity_count:   17
edge_count:     34
gap_count:      3
code_units:     6
git_commits:    127
files_traced:
  src/main/java/.../NiqController.java
  src/main/java/.../NiqService.java
  src/main/java/.../NiqRequestDto.java
  src/main/java/.../NiqRepository.java
  src/main/java/.../NiqRuleSet.java
  src/main/java/.../PayerDataLoader.java
stages:
  0a Code Tracing             6 units
  0b Git History            127 commits
  1  Entity Extraction       17 entities (raw 22, filtered 5)
  1.4 Dependency Expansion    2 candidates expanded
  1.5 Intent Synthesis       11 functions synthesized (3 high-risk)
  1.6 Import-Graph Edges     14 structural edges
  2  Relationship Extraction 34 edges (14 structural + 20 LLM)
  3  Context Synthesis       17 contexts
  3.5 Memory Tokenization    17 entities tokenized
  4  Gap Detection           3 gaps
  5  Graph Population        done
```

Note the `job_id` — paste it into `/tmp/cb-demo-last-ui-job` (the Makefile
will use it for the compare step).

### Step 3 — Visualize the dependency graph

Click **Service Map** (or whatever your dashboard is called) in the
navigation.

Search for the controller class name (e.g. `NiqController`). The graph
centers on it with neighbours within ~2 hops.

**What you'll see:**
- The `ApiEndpoint` node (the POST handler) at the center.
- Edges going to: `Function` (service methods), `Class` (services / repos).
- Edges from those to: `DatabaseTable`, `DatabaseQuery`, `SchemaField`.
- Possibly external nodes: `ExternalService`, `ConfigKey`.

**Look at the edge types.** Hover or click an edge — `CALLS`, `EXPOSES`,
`READS_TABLE`, `WRITES_COLUMN`, `IMPORTS`, `CONSUMES_FIELD`. These come
from a mix of structural (import-graph) and LLM extraction. ADR-0010
documents the confidence rubric; the UI shows confidence as edge thickness.

### Step 4 — Inspect business context for one entity (THE QUALITY CHECK)

This is where you grade the product.

Click your POST endpoint's `ApiEndpoint` node. The entity detail panel
opens. You should see four sections:

1. **Metadata** — entity_type, file, line, signature, confidence.
2. **Properties** (from JSONB metadata) — `t0`, `t1`, `functionContext` if
   it's been promoted to function-level intent. Read these.
3. **Business context** (from `node_context` rows where `context_type IN
   ('llm_synthesis', 'business_context')`) — the prose explanation of
   *why* this entity exists.
4. **Linked context** — git commits, PRs, tickets that touched this entity.

#### What good business context looks like

> *"Computes a competitiveness score for a payer network based on uploaded
> claims data. Reads from `niq_payer_data` and `niq_rule_set` tables;
> writes nothing. Authenticated via the standard JWT bearer (no special
> roles). Risk-of-change: HIGH — downstream BI dashboards depend on the
> response shape and a partner integration with InsurerCo expects the
> `score` field at the top level. The score is always 0–100 inclusive
> (verified by tests in NiqServiceTest::testScoreBounds)."*

Signals of high-quality output:
- ✅ References real tables (`niq_payer_data`) — extracted from the code
- ✅ Names a specific risk reason — pulled from PR descriptions or commit
       messages, not invented
- ✅ Cites a test that validates an invariant — the assumption miner
       produced this
- ✅ Captures the *why* (downstream BI / partner integration), which is
       not in the code itself but in commit / PR text

Signals of low-quality output:
- ❌ "Processes input and returns a response" — generic, means the
       extractor only saw the controller method body
- ❌ References tables that don't exist in your schema — hallucination,
       usually means LLM ran without enough grounding (low entity_count)
- ❌ "May have downstream effects" — vague, the risk-reason synthesis
       didn't have signal

#### If quality is low, why?

In order of likelihood:

1. **Few entities extracted (entity_count < 8).** The Navigator didn't
   reach the bottom of the chain. Try Stage 1.4's expansion — sometimes
   it adds the missing service class. If still low, the controller
   pattern isn't matched by `code_tracer.py`'s regex.
2. **Thin git history.** New endpoints with one initial commit have
   nothing to mine. The extractor falls back to code-only synthesis,
   which is generic.
3. **Boilerplate commit messages.** `"fix bug"` / `"update tests"` give
   no signal. The synthesis is starved.
4. **The endpoint is genuinely simple.** Sometimes a POST endpoint is
   just `@RequestMapping → service.save() → repo.save()`. There's no
   business context to synthesize. The output reflects that.

Improving quality:
- Use **Annotation Editor** to add a user annotation. Click "Add
  context" on the entity, write the business "why" in plain English.
  This is stored as `context_type='user_annotation'` and the `Ask`
  page weighs it higher than LLM synthesis.
- Run the pipeline again on a related endpoint. L2 shared context grows
  — the second endpoint sees the domain glossary and service registry
  the first one built.
- Edit the entity's `t1` token directly in Postgres if you want the
  Ask page to surface a corrected summary. (Later, ADR-0017 promotes
  this to a first-class node so it's edit-friendly in the UI.)

### Step 5 — See the structural call graph in Neo4j Browser

Open `http://localhost:7474` in your browser. Login: `neo4j` / `password`.

In the query box, run:

```cypher
MATCH (n) WHERE n.scope = 'dev' RETURN n LIMIT 50
```

You'll see the function-level call graph that **the React UI doesn't
visualize** — the cb-api Bun extractor populated this from tree-sitter
parses. Click any node, hover any edge.

Then run:

```cypher
// Find the call chain from your POST endpoint to any DB write
MATCH path = (handler:Function)-[*1..6]->(table:Module {kind:'table'})
WHERE handler.qualified_name CONTAINS 'computeScore'   // your method name
RETURN path LIMIT 5
```

If the path is `handler → service.save → repo.persist → users_table`,
**that's the precise call chain the structural extractor identified**.
This is what ADR-001's function-level extraction is for; you're seeing
the Bun side of it (Postgres-side function nodes are ADR-0021 future
work).

### Step 6 — Look at LLM call telemetry in Langfuse

Open `http://localhost:3001`. You'll see every LLM call from the run:

- **Trace** per pipeline run — each LLM call is a span.
- **Cost** per call — confirms what the Anthropic dashboard shows.
- **Prompt + response** — click any span to see the actual prompt sent
  to Claude and the response back. This is invaluable for debugging
  bad business context: read the actual Stage 3 prompt and response.

If a Stage 3 (context synthesis) call returned generic text, you'll see
exactly why — usually because the prompt didn't have enough grounding
(thin entity context, missing git commits).

### Step 7 — Ask the brain a question via the Ask page

Click **Ask** in the navigation. Try these queries in order, paying
attention to which ones produce good answers:

1. **Plain factual.**
   `what does POST /api/competitiveness/score do`
   *Expectation:* one-paragraph answer, references the controller and
   service classes by name. Quality bar: should match your one-liner from
   Step 0 plus extra detail.

2. **Why / business.**
   `why does the competitiveness score handler exist and who uses it`
   *Expectation:* references commit messages, PR titles, possibly
   external consumers. Quality bar: should mention details NOT in code
   (partner integrations, dashboards) if your git history has them.

3. **Impact / blast radius.**
   `what breaks if I change the score field in the response`
   *Expectation:* lists consumer entities. If gap detection identified
   downstream consumers, they should appear. If the answer is "I don't
   know", your blast radius graph is thin — fix by extracting more
   endpoints.

4. **Schema-level.**
   `what fields does the request body have and what validations apply`
   *Expectation:* lists DTO fields with types. If validation methods
   were extracted, their constraints appear as assumptions.

5. **Cross-cutting.**
   `what tables does the competitiveness flow read or write`
   *Expectation:* lists `niq_payer_data`, `niq_rule_set`, etc. Quality
   bar: should match your Neo4j Cypher result from Step 5.

### Step 8 — Verify Postgres and Neo4j actually have the data

Open `make db-shell`:

```sql
-- All entity types extracted in this workspace
SELECT node_type, count(*) FROM nodes
WHERE workspace_id = '00000000-0000-0000-0000-000000000001'
GROUP BY node_type ORDER BY 2 DESC;

-- Business contexts (Stage 3 synthesis output) for your POST endpoint
SELECT n.name AS entity, nc.context_type,
       substr(encode(nc.body, 'escape'), 1, 200) AS context_preview
FROM nodes n
JOIN node_context nc ON nc.node_id = n.id
WHERE n.workspace_id = '00000000-0000-0000-0000-000000000001'
  AND n.name LIKE '%computeScore%'    -- your handler method name
  AND nc.context_type IN ('llm_synthesis', 'business_context');

-- T0 + T1 memory tokens for an entity
SELECT name, node_type,
       metadata->>'t0' AS t0_token,
       substr(metadata->>'t1', 1, 200) AS t1_preview
FROM nodes
WHERE workspace_id = '00000000-0000-0000-0000-000000000001'
  AND name LIKE '%computeScore%';
```

These three queries are the ground truth. Whatever the React UI shows or
the Ask page answers, it's derived from these rows. If something looks
wrong in the UI, run these to confirm whether the underlying data is
right (UI rendering bug) or wrong (extractor bug).

---

## 3. POST endpoints have one specific quality risk worth flagging

POST handlers often have a request body DTO. The extractor must:
1. Recognise the `@RequestBody` parameter.
2. Find the DTO class.
3. Extract its fields as `SchemaField` entities.
4. Find any `@Valid` validation on those fields.
5. Synthesize what the validation means in business terms.

The current extractor does (1) and (2) reliably; (3) is good for fields
that have annotations or types; (4) is partial — only catches simple
`@NotNull`, `@Size`, `@Pattern` annotations; (5) is LLM-driven and depends
heavily on the *names* of the validation classes.

Things to verify on your POST endpoint:

- After Step 2, run this in psql:
  ```sql
  SELECT name FROM nodes
  WHERE node_type = 'SchemaField'
    AND name LIKE '%RequestDto%'   -- substitute your DTO name
    AND workspace_id = '00000000-0000-0000-0000-000000000001';
  ```
  Each request body field should appear as `<DtoName>.<fieldName>`. If
  fields are missing, the DTO class wasn't extracted in Stage 1 — either
  the file wasn't traced or the LLM dropped fields.

- For each field, look at the entity detail panel. The metadata should
  include `validation_constraints` (an array). If empty, no validation
  was extracted. Manual annotation may be needed for a good demo.

---

## 4. The "is this really working?" smell test

After Step 1–8 you should be able to answer YES to all of these:

- [ ] The pipeline completed without an error in the result panel.
- [ ] Postgres has ≥ 10 nodes for your endpoint and connected entities.
- [ ] Neo4j browser shows the structural call graph including your
      handler method.
- [ ] Clicking the `ApiEndpoint` node in the React UI opens a detail
      panel with non-empty business context.
- [ ] The business context references actual classes / tables / DTOs
      from your code (not just generic words).
- [ ] The Ask page answers "what does this endpoint do" with citations
      that match the entities you extracted.
- [ ] Langfuse shows ≤ ~30 LLM calls for the run and total cost
      ≤ $0.50.
- [ ] T0 / T1 tokens are populated on `nodes.metadata` for the main
      entities.

If any of these fails, the product isn't working end-to-end. Run
`make -f Makefile.demo diag` and inspect.

---

## 5. What you're NOT seeing today (and where it is in the roadmap)

| Missing | Why | Maps to ADR |
|---|---|---|
| A "smart-zone preview" alongside the Ask answer (T0/T1/T2 with token budget) | Smart-zone assembler not built | ADR-0018 |
| `.brain/` git-trackable JSON files in your repo | BrainStore not built | ADR-0012 |
| One canonical URN that joins Postgres ↔ Neo4j ↔ Qdrant | Identity not unified | ADR-0013 |
| Brain diff per PR | Not built | future ADR |
| Function-level `function_node` entities in the React graph | Postgres side not built; Neo4j has them | ADR-001 (root) + future ADR-0021 |
| First-class assumption nodes with `RELIES_ON` edges | Stored as `node_context` rows | ADR-0017 |
| Cross-repo blast radius | Single-repo today | Stage 2 ADRs |
| Streaming Ask responses (token-by-token) | Buffered today | future ADR-0030 |
| Claude Code MCP integration (Cmd+K from your editor) | MCP server not built | ADR-0019 |

When demoing, frame these honestly: "today the brain answers grounded NL
questions and visualizes the dep graph; in two more weeks of work it
also surfaces blast radius per PR and supports cross-repo." That's
defensible. Pretending the missing pieces exist is not.

---

## 6. Recommended order for your first session

1. Read this doc once front-to-back (15 min).
2. `make -f Makefile.demo doctor` and `guard` (5 min).
3. `make -f Makefile.demo up-all` + 4 service terminals + `health` (10 min).
4. `make -f Makefile.demo discover` and pick your POST endpoint (2 min).
5. **Step 2** — trigger via API Explorer, watch all stages (5 min).
6. **Step 3** — explore the dep graph (5 min).
7. **Step 4** — open one entity, evaluate business context (10 min).
   This is where you decide whether the quality is acceptable for demo.
8. **Step 5** — Neo4j Browser exploration (5 min).
9. **Step 6** — Langfuse exploration (5 min).
10. **Step 7** — five questions on the Ask page (10 min).
11. **Step 8** — three SQL queries to ground-truth what you saw (5 min).
12. **Smell test** checklist (5 min).
13. `make -f Makefile.demo cost` (1 min).

Total: ~90 minutes for the first run. Subsequent runs are faster — most
of the time is spent looking at outputs, not waiting for the pipeline.

---

## 7. After this run

Three follow-ups in priority order:

1. **If quality is acceptable:** start implementing Stage 1 ADRs. Begin
   with ADR-0011 (structural-first ordering — biggest cost reduction)
   and ADR-0029 (reliability hardening). After those two ship, you have
   a demo-stable product.
2. **If quality is borderline:** add user annotations via the
   Annotation Editor on the 5–10 entities that matter most for your
   demo. This is the cheapest quality win.
3. **If quality is bad:** the issue is almost certainly entity_count <
   8 from Stage 1. Either the Navigator didn't reach the chain or your
   endpoint is genuinely simple. Try a more complex endpoint (one with
   a 4–5 layer call chain).
