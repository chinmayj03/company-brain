# ADR-0053 — Claude-Code Quality Patterns for Extraction + Navigation + Prompts

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Skills invoked:** `/engineering:architecture` + `/engineering:system-design` + `/product-management:brainstorm`
**Scope:** Prompt-engineering, tool-design, and verification patterns from Claude Code's internals applied to company-brain's extraction + navigation + query layers. Independent of ADR-0051/0052's harness migration — these patterns can land on the existing pipeline.

---

## Context

ADR-0048/49/50 cut cost. ADR-0051/0052 redesign the orchestration. Neither
addresses **extraction quality** — whether the agent correctly identifies
entities, draws the right edges, and avoids hallucinating queries that
don't exist.

Claude Code is a coding agent that produces verifiable, citable code
edits at scale. The patterns it uses to stay accurate transfer almost
directly to a codebase-extraction agent. This ADR catalogs them and
proposes which to adopt.

The user's three concerns map to three pattern groups:

| User concern | Pattern group | Where Claude Code uses it |
|---|---|---|
| Extraction quality | Prompt rigor + verifier loop + negative examples | Tool descriptions; the file-state-tracking discipline |
| Navigation accuracy | Tool-use loop with deterministic shortcuts + cap | Glob/Grep first; capped agent loops |
| System + user prompts | Persona, negative space, format-first, schema-driven | Every Claude Code system prompt |

---

## What Claude Code does that company-brain doesn't (that improves quality)

### 1. **Tool descriptions are essays, not labels**

Claude Code's `Edit` tool description is ~600 words. It explains:
- When to use Edit vs Write vs MultiEdit
- The `old_string` uniqueness rule (and how to handle ambiguity)
- File-state tracking ("you must Read before Edit; the harness blocks otherwise")
- Tabs vs spaces preservation
- 5+ negative examples ("DON'T pass relative paths")

Company-brain's `extract_methods_from_class` description today is **one
sentence** ("Extract entities + edges + business context for the methods
in a class"). The model is left to guess every edge case.

**Adoption (Q1)**: rewrite each major tool/agent prompt as a 500-1000
word essay covering: what to do, what NOT to do, common pitfalls, ≥3
worked examples (good + bad), explicit confidence rubric.

### 2. **Negatively framed rules beat positively framed ones**

Claude Code's prompt is full of "Do NOT...", "NEVER...", "Always avoid...".
The model attends to hard prohibitions more reliably than soft suggestions.

Company-brain's extraction prompt today says "extract relationships"
without saying what shouldn't be extracted. Result: it draws CALLS edges
to `@RequiredArgsConstructor` (Lombok), `@Slf4j` (Lombok logger field),
`@Override` (annotation marker, not a method).

**Adoption (Q2)**: every extraction prompt grows a `<do_not_extract>`
section with concrete anti-patterns:

```xml
<do_not_extract>
- Lombok annotations as edges (@RequiredArgsConstructor, @Slf4j, @Data, @Builder)
- @Override / @Deprecated / @SuppressWarnings (markers, not relationships)
- Constructor calls to value classes (DTOs, Records) — they're data, not behaviour
- Spring `@Bean` factory methods as INSTANTIATES edges (lifecycle, not flow)
</do_not_extract>
```

### 3. **Few-shot examples per output type**

Claude Code's tool prompts include ≥1 worked example per non-trivial
output shape. The model copies the example's structure.

Company-brain's prompts describe the JSON schema but show no examples.
Result: the model invents fields, misuses confidence, or omits required
keys.

**Adoption (Q3)**: every extraction prompt ships with 3 examples:
1. **Happy-path** — a typical method body → expected output verbatim.
2. **Edge case** — interface method, no body → expected output (just signature).
3. **Negative** — Lombok-generated → expected `[]` (nothing extracted).

Examples are kept under a `<examples>` block at the end of the system
prompt so they cache cheaply (ADR-0049 C1).

### 4. **Explicit confidence rubric, not free-form scoring**

Claude Code's hover-information UI surfaces a citation per fact. The
underlying generation prompt has rules like "only emit a fact if you can
quote the source line".

Company-brain emits `confidence: 0.0–1.0` per entity but the model picks
arbitrarily — observed values cluster at 0.8 regardless of evidence.

**Adoption (Q4)**: pin confidence to evidence:

```
1.00 — Quoted verbatim from source (set when you copy a method signature)
0.90 — Single strong signal (one annotation OR one direct call)
0.80 — Two corroborating signals
0.70 — Inferred from naming convention with one structural cue
0.50 — Guessed from naming convention alone
DO NOT EMIT entities below 0.5 confidence.
```

### 5. **Verifier sub-agent reading the source after extraction**

Claude Code's flow: assistant emits a tool_use, harness executes, result
gets verified before the next turn. The model NEVER acts on facts it
hasn't checked.

Company-brain emits entities with quoted SQL and never goes back to
verify the SQL exists in the source. Hallucinated `query_text` is the #1
cause of `/query` returning wrong answers.

**Adoption (Q5)**: a deterministic post-extraction verifier (no LLM):
- For each entity with `query_text`, grep the source file. If the
  literal substring isn't found, drop the entity OR mark `verified=false`.
- For each edge with `evidence`, grep similarly.
- Optionally: a cheap Haiku verifier sub-agent for borderline cases.
  ("This entity claims X. Source says: ... . Does the claim hold?
  Yes/No.")

Cost: ~$0.001 per run. Prevents the bad-quote class of `/query` failures.

### 6. **Tool ordering signals priority**

Claude Code lists `Read`, `Grep`, `Glob` first in the tool catalog (cheap
discovery), then `Edit`, then `Bash` (expensive/risky). The model defaults
to top-listed tools.

Company-brain's tool catalog (in the future harness) hasn't been
designed yet. **Adopt**: deterministic-first, LLM-call-last:

```
1. find_file_by_name        (deterministic, ~ms)
2. extract_method_signatures(deterministic, tree-sitter, ~ms)
3. grep_code                (deterministic, ~ms)
4. read_file                (deterministic, FileCache, ~µs)
5. spawn_research           (LLM, ~$0.005)
6. spawn_extractor          (LLM, ~$0.020)
```

The system prompt nudges: "prefer deterministic tools; only use LLM
sub-agents when structural reasoning is required."

### 7. **Anti-loop heuristics**

Claude Code's harness detects loops: same tool with same args twice,
"I should try X" repeated. After 3 retries it forces a different approach
or stops.

Company-brain's KnowledgeNavigatorAgent has gone 26 turns calling
`read_file` on increasingly unrelated DTOs. **Adopt**:

- Cap tool-use loops at 10 iterations (configurable).
- After 3 reads of pure-DTO files, force a re-plan.
- After 2 failed `find_class` calls, broaden the search OR ask the
  parent agent for help.

### 8. **Conversation discipline (don't preface, don't recap)**

Claude Code's behavior rules: "Answer the question first. Don't say
'I'll do X' — just do X. Don't recap when the user can scroll up."

Company-brain's `/query` responses today open with "Based on the
extracted brain context, here's what I found about getPayerCompetitors..."
— wasted tokens.

**Adoption (Q8)**: rewrite the query system prompt to forbid prefacing.
Lead with the answer, structured: `summary_md` first, `sql_quotes` second,
`affected_entities` third.

### 9. **Skills loaded by description match (no manual selection)**

Claude Code matches the user's intent against skill descriptions
automatically. Skills don't load until they match.

Company-brain's framework support (Spring/FastAPI/NestJS) is hardcoded
in `_trace_java`/`_trace_python`/`_trace_typescript` today. **Already
proposed in ADR-0051 P3 — confirm it ships per that ADR.**

### 10. **Plan mode for high-cost operations**

Claude Code has `--plan` mode: produce a detailed plan, render it, wait
for user approval before executing.

Company-brain runs the SpecialistAgent + ContextAgent + storage in one
shot. **Adopt**: `BRAIN_PLAN_ONLY=true` env or `--plan` CLI flag → run
SpecialistAgent only, render the plan, exit. User runs again without
the flag to execute. Saves $0.04 of wasted ContextAgent cost on bad
plans.

### 11. **Memory file with file-state tracking**

Claude Code's `CLAUDE.md` is loaded into context and treated as
"trusted user-curated context". The model defers to it on conflicts.

Company-brain's `.brain/BRAIN.md` (proposed in ADR-0051 P3) — when it
ships, the system prompt should explicitly say: **"if BRAIN.md
contradicts what you observe, BRAIN.md wins. The user has curated it
specifically for this repo."**

### 12. **System prompt structure: identity → behaviors → tools → output**

Claude Code's prompt opens with identity ("You are Claude Code"), then
behaviors (concise, no emojis), then tool catalog, then output format
rules.

Company-brain's prompts today open with task description, then schema,
then tools. **Reorder to**: identity → goal → constraints → output schema
(BEFORE the task) → tools → task. The model attends to constraints more
when they're stated up-front.

---

## Decision

Adopt 12 patterns above as a single coordinated quality pass. Sequence
them as **three landable PRs**, each independently shippable:

### PR-A — Prompt rewrites (Q1, Q2, Q3, Q4, Q8, Q12) — ~3 days

Rewrite the four big extraction prompts (SpecialistAgent, ContextAgent,
RelationshipExtractor, ContextSynthesizer) and the query prompt:
- Identity-first opening
- Constraints + `<do_not_extract>` block
- Schema before task
- 3 worked examples per prompt
- Explicit confidence rubric
- Conversation-discipline directives for `/query`

Tests: golden-output regression suite — 20 fixed (prompt, expected
output) pairs. PR is green when ≥18/20 match within Levenshtein
distance < 5%.

### PR-B — Verifier + anti-loop (Q5, Q7) — ~3 days

- `pipeline/verifier.py` — deterministic verifier: greps source for each
  emitted `query_text` / `evidence`. Drops or marks unverified.
- Optional `agents/verifier_agent.py` — Haiku sub-agent for borderline
  cases (~$0.001/run).
- Add anti-loop telemetry to `agents/knowledge_navigator_agent.py`:
  - Track tool-call hash (name + args canonicalised)
  - Track iteration count per file
  - Force replan or stop on threshold breach

Tests: synthetic prompt that emits a fake `query_text`; verifier must
drop it. Synthetic agent that loops; harness must break out.

### PR-C — Plan mode + tool-priority + memory-trusted (Q6, Q10, Q11) — ~2 days

- `--plan` flag on `make demo run-cli` and `brain extract` CLI: runs
  SpecialistAgent only, prints plan, exits 0. Subsequent run without
  `--plan` consumes the cached plan if SHA matches.
- Reorder tool registry by cost-tier (deterministic first); the
  system prompt's tool-list section reflects the same order.
- `.brain/BRAIN.md` system-prompt injection: lead with "BRAIN.md is
  ground truth. If it contradicts your observation, BRAIN.md wins."

Tests: plan mode acceptance test (plan rendered, no LLM call to
ContextAgent); BRAIN.md priority test (synthetic BRAIN.md says "skip
ClassX"; agent emits no entities for ClassX even when it appears in
the plan).

---

## Options Considered

### Option A — All 12 patterns in three PRs (this ADR)

| Dimension | Assessment |
|---|---|
| Complexity | Low-medium per PR |
| Quality lift | High — addresses the 3 known classes of extraction error (hallucinated quotes, drift entities, wasted navigator turns) |
| Cost | Slight increase per run (verifier + plan mode telemetry) — < $0.001 |
| Effort | ~8 days total, parallelisable as three PRs |

The chosen design.

### Option B — Just rewrite prompts (PR-A only)

| Dimension | Assessment |
|---|---|
| Quality lift | Medium — fixes hallucination and drift but not loops/verification |
| Effort | ~3 days |

Rejected as insufficient — without the verifier, prompt rewrites alone
let the model still emit unverifiable claims.

### Option C — Wait for ADR-0051 harness migration, do all this then

| Dimension | Assessment |
|---|---|
| Quality lift | Same as Option A but delayed 6+ weeks |
| Risk | Quality issues compound during the harness rewrite |

Rejected. These patterns are orthogonal to the harness; landing them
first means the harness inherits a higher-quality baseline.

---

## Trade-off Analysis

The user's framing was three-fold: **quality**, **navigation accuracy**,
**prompts**. The patterns map cleanly:

- **Quality**: PR-A (rewrites with negative examples + confidence rubric)
  + PR-B (verifier post-pass).
- **Navigation accuracy**: PR-B (anti-loop) + PR-C (tool-priority
  ordering).
- **Prompts**: PR-A entirely + PR-C (BRAIN.md priority directive).

The cost of adopting these is small (~$0.001/run for the verifier; a
prompt rewrite is one PR per agent). The benefit is qualitative — fewer
hallucinated quotes, fewer drift entities, faster navigator
convergence.

The risk is **prompt regression**: rewriting a prompt can drop quality
on cases the original handled well. Mitigation: golden-output
regression suite (20 fixtures) gating each prompt change.

---

## Consequences

**What becomes easier**

- Trusting `/query` answers — verifier guarantees quotes are real.
- Adding new entity types — the prompt template's `<examples>` block
  shows the pattern.
- Debugging a wrong extraction — the confidence rubric tells you the
  model's evidence level; verifier output tells you which claims got
  dropped.
- Reasoning about navigator cost — anti-loop guarantees max 10 turns.

**What becomes harder**

- Updating prompts — every prompt change must regenerate the golden-
  output fixtures. Fix: a `make regenerate-fixtures` target that runs
  the prompt against a test repo, captures output as the new baseline.
- Sub-agent prompts grow — each one is now 1000+ tokens. Mitigation:
  cache the system prompt (ADR-0049 C1 already shipped).

**What we'll need to revisit**

- Prompt versioning: when a prompt changes, the cache key should change
  (otherwise stale responses serve forever). Add `prompt_version` to
  the cache key.
- Confidence rubric calibration: the 0.5/0.7/0.8/0.9/1.0 thresholds
  are guesses today. After 100 runs, recompute from actual evidence
  distributions.

---

## Action Items

### PR-A — Prompt rewrites

1. [ ] Rewrite `agents/specialist_agent.py` system prompt: identity-first,
       `<do_not_extract>`, 3 examples, confidence rubric.
2. [ ] Rewrite `agents/context_agent.py` system prompt: same shape;
       extra emphasis on "quote query_text verbatim".
3. [ ] Rewrite `pipeline/relationship_extractor.py` system prompt: 50-edge
       taxonomy with one example per common edge type.
4. [ ] Rewrite `pipeline/context_synthesizer.py` system prompt: 21-field
       BusinessContext with concrete examples per field.
5. [ ] Rewrite `api/routes/query.py` query system prompt: conversation
       discipline (no prefacing, no recap), schema-first output,
       `summary_md` field for raw markdown answer.
6. [ ] Add `tests/regression/golden_outputs/` with 20 (input, expected)
       fixtures across the 5 prompts.
7. [ ] Add `make regenerate-fixtures` target.
8. [ ] CI gate: PR is red if > 2 fixtures regress (Levenshtein > 5%).

### PR-B — Verifier + anti-loop

9. [ ] `pipeline/verifier.py`:
       - For each entity with `query_text`, `Path(file).read_text()`
         (FileCache), check substring exists. Drop or mark
         `verified=false`.
       - Same for edge `evidence` strings.
       - Telemetry: `verifier.dropped_count`, `verifier.unverified_count`.
10. [ ] (Optional) `agents/verifier_agent.py` — Haiku sub-agent for
        borderline cases (model says "X reads column Y", verifier finds
        X but not Y, sub-agent decides).
11. [ ] `agents/knowledge_navigator_agent.py` anti-loop:
        - `_seen_calls: dict[str, int]` keyed by canonical (tool, args).
        - `_dto_read_count` — counter; triggers replan at 3.
        - `_iteration_cap = 10` — hard stop.
12. [ ] Acceptance test: synthetic prompt emits fake `query_text`;
        verifier drops it.
13. [ ] Acceptance test: synthetic looping agent; harness breaks out
        within 10 iterations.

### PR-C — Plan mode + tool priority + BRAIN.md priority

14. [ ] `--plan` flag on `brain extract` CLI: runs SpecialistAgent only,
        renders plan as markdown table, exits 0. Cache plan keyed by
        (repo_sha, endpoint, method) for re-use on the next non-plan
        run.
15. [ ] `Makefile.demo run-cli` honours `PLAN=1` env to pass through.
16. [ ] Reorder tool registry: deterministic-first.
17. [ ] When `.brain/BRAIN.md` exists, prepend
        "BRAIN.md is ground truth — if it contradicts your observation,
        BRAIN.md wins" to the system prompt.
18. [ ] Acceptance test: plan mode emits plan + exits without ContextAgent
        invocation.
19. [ ] Acceptance test: BRAIN.md says "skip ClassX"; agent emits no
        entities for ClassX even when ClassX appears in the plan.

### Cross-cutting

20. [ ] Add `prompt_version` to LLM cache keys (so prompt changes
        invalidate cache cleanly).
21. [ ] Telemetry: per-run breakdown of verifier drops + anti-loop
        breakouts in the job summary.
22. [ ] Update `docs/PROMPT-DESIGN.md` (new) — pattern catalog +
        rationale + how to add a new prompt that follows the patterns.

---

## Brainstorming notes (preserved for posterity)

The product-management:brainstorm part of the user's request — what
else could improve quality if cost weren't a concern?

**Multi-pass extraction with self-critique**: extract once with Haiku,
critique with Sonnet ("what's wrong with this extraction?"), re-extract
with Haiku informed by the critique. ~$0.05/run. Likely best-in-class
quality but expensive.

**Active learning from feedback**: when the user marks a `/query` answer
as wrong (`/feedback wrong`), capture (question, answer, correction)
and feed into the system prompt as a few-shot example. Compounds over
time.

**Cross-repo pattern recognition**: when extracting repo A, surface
patterns observed in repo B with similar structure ("this looks like
your CompetitivenessRepository — same jOOQ DSL pattern"). Requires a
cross-workspace embedding store.

**Speculative execution**: while ContextAgent extracts file 1,
SpecialistAgent already plans file 2's manifest update. Hides latency.

**Diff-based extraction**: only re-extract methods whose tree-sitter
hash changed. Already partially via L2 cache; promote to per-method
granularity.

**Verifier-as-a-service**: expose `/verify` endpoint so external
systems can ask "does the brain's claim that X reads column Y hold?"
— reuses the PR-B verifier.

These are deferred — they're additive on top of PR-A/B/C and need
separate ADRs to scope.
