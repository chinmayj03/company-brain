# ADR-0048 — Two-Agent Batched Extraction (kill the navigator's 26-turn ReAct loop)

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Supersedes (partial):** ADR-0042 (KnowledgeNavigatorAgent ReAct loop), ADR-0047 (per-method chunk_extractor calls)
**Sequenced with:** ADR-0049 (caching + format) lands first; this ADR lands second; ADR-0050 (big-repo recovery) lands third. Full file-ownership table is in ADR-0050 §"Sequencing & Merge Plan" so the three ADRs can ship in three independent Claude Code sessions with no merge conflicts.
**Cost target:** ≤ $0.05 per typical pipeline run (down from observed $0.30–0.50)

---

## Context

A real run on `POST /competitiveness/summary/competitors/payer` against
`network-iq-backend-java` produced these telemetry numbers (raw log
inline, redacted for length):

```
turn=0   input=3887   output=76     cumulative_cost=$0.0034
turn=5   input=6539   output=71     cumulative_cost=$0.0055
turn=10  input=9685   output=121    cumulative_cost=$0.0082
turn=15  input=13551  output=125    cumulative_cost=$0.0113
turn=20  input=17225  output=74     cumulative_cost=$0.0141
turn=26  input=18328  output=57     cumulative_cost=$0.0149   ← still going
…
```

Three structural problems jump out:

1. **26 LLM round-trips before any entity extraction starts.** The
   `KnowledgeNavigatorAgent` runs a ReAct loop where each turn invokes
   one tool (`read_file`, `find_class`, `extract_method`, `search_code`)
   and waits for the next decision. Each turn adds the previous tool
   result to the conversation, so the input grows linearly: 3.9k →
   18.3k tokens by turn 26.

2. **`cache_creation=0 cache_read=0` on every single call.** The
   intended Anthropic prompt-cache hint isn't reaching the wire. Every
   request pays full input cost. With caching on the system prompt
   (50-edge taxonomy + 21-field BusinessContext schema ≈ 3k tokens),
   the second-and-onward call drops those tokens to 0.1× cost.

3. **The navigator reads files that don't carry behavioural logic.**
   Turn 17 = `NiqAPIRequest.java`. Turn 18 = `Filters.java`. Turn 20 =
   `CompetitivenessPayerSummaryDTO.java`. Turn 22 = `PayerCompetitorDTO.java`.
   Turn 24 = `OffsetPaginationMeta.java`. Turn 26 = `VIEW_BY` enum.
   These are pure DTOs / value objects. They're useful to *list* in the
   plan, but they don't need a separate read-and-classify LLM round-trip
   — a structural heuristic identifies them in microseconds.

After the navigator finishes, **Stage 1 chunked extraction fires
another ~50 calls** (one per kept method chunk × 1200 max output
tokens). With prompt caching off, that's another ~$0.10–0.20.

Total observed: **~$0.30–0.50 per run**, **~5 minutes wall time**,
mostly spent in serialised LLM round-trips that could have been
batched or eliminated.

User's framing: *"there should be an agent for code specialty and
other for context, we can combine our phases maintaining all the
extraction quality"* — a clean, two-agent architecture that does
fewer, larger, cached calls.

---

## Decision

Replace the current three-stage flow

```
Handler finder (1 call)
  └→ KnowledgeNavigatorAgent ReAct loop (~25 calls)
       └→ ChunkBatcher + per-method chunk_extractor (~50 calls)
```

with a **two-agent, batched, cache-aware** pipeline:

```
Handler finder (1 call, unchanged)
  └→ SpecialistAgent — single call with cached system prompt
        Input: handler file + filtered repo manifest
        Output: structured extraction plan (files × methods × roles)
  └→ ContextAgent — N batched calls (8 methods/batch, cached)
        Input: 8 method bodies + class header + imports
        Output: entities + edges + business_context for the batch
```

### D1 — SpecialistAgent (replaces the navigator ReAct loop)

**One LLM call. No tools. No loop.** Input is a single, well-formed
prompt:

```
SYSTEM (cached, ~2.5k tokens):
  Role + extraction taxonomy + schema for the plan
  cache_control: ephemeral

USER:
  Entry endpoint: POST /competitiveness/summary/competitors/payer
  Entry handler file (FULL CONTENT): <CompetitivenessController.java, ~5k chars>
  Repo manifest (filtered to candidate files for this endpoint):
    apps/.../CompetitivenessController.java        — controller     5.2 KB
    apps/.../DefaultCompetitivenessService.java    — service       12.8 KB
    apps/.../CompetitivenessRepositoryImpl.java    — repository    26.3 KB
    apps/.../CompetitivenessPlanRepository.java    — repository    34.1 KB
    apps/.../CompetitivenessProvidersRepository.java — repository  18.7 KB
    apps/.../CompetitivenessAsyncService.java      — service        9.4 KB
    apps/.../NiqAPIRequest.java                    — dto            2.1 KB
    … (filtered to ~15 candidates, not all 689 files)

  Return a JSON plan: which files, which methods within each, with role
  + relevance score. Skip pure DTOs / value objects (we'll handle them
  structurally).
```

The candidate list comes from a deterministic pre-filter using the
hybrid searcher we already have, capped at ~20 files. The agent gets
the WHOLE entry handler in context (not a 600-char preview), so it can
follow the call chain mentally in one pass instead of one tool-call per
hop.

**Output shape (compact, deterministic):**

```json
{
  "plan": [
    {
      "file": "apps/.../CompetitivenessPlanRepository.java",
      "role": "repository",
      "methods": ["getPayerCompetitors", "getPayerPlans", "getMetrics"],
      "relevance": 1.0,
      "reason": "called by service.getPayerCompetitors via planRepo delegation"
    },
    …
  ],
  "skip_dto": ["NiqAPIRequest", "PayerCompetitorDTO", "Filters"]
}
```

**Cost: 1 call × ~8k input × Haiku = ~$0.008 (input) + ~$0.005 (output) ≈ $0.013.**

Replaces the 26-turn loop's ~$0.15.

### D2 — ContextAgent (replaces per-method chunk_extractor)

**Batched, cached, role-aware.** The plan from D1 is grouped into
batches of up to 8 methods (typically same-class siblings, like the
existing `ChunkBatcher` already does). Each batch is one LLM call:

```
SYSTEM (cached, ~3k tokens):
  Edge taxonomy (50 types) + 21-field BusinessContext schema +
  output JSON format
  cache_control: ephemeral

USER:
  Class header (imports + class declaration + field list, ~800 chars)
  Method 1 body (~600 chars)
  Method 2 body (~600 chars)
  …
  Method 8 body (~600 chars)

  Return entities + edges + business_context for ALL methods in this
  batch. One JSON object per method.
```

This is what `chunk_extractor.py` already does for batches today, but
with three improvements:

1. **Cache the system prompt.** Pass `cache_control: {"type":
   "ephemeral"}` on the system message. After the first batch, the
   ~3k system tokens cost 0.1× per call.

2. **Increase batch size**: 8 methods per call instead of the current 3-4.

3. **Drop per-method follow-up calls**: today's chunk_extractor makes
   up to 3 follow-up calls per chunk to refine edges; remove those —
   the batched call has enough context.

**Cost per batch**: ~2.5k input (cached: ~250 effective) × Haiku +
~1500 output × Haiku = **~$0.003 per batch after cache warms up**.

For a 60-method endpoint: 60 ÷ 8 = 8 batches × $0.003 = **$0.024**.

Replaces the current ~$0.075 per-method extraction cost.

### D3 — Structural DTO fast-path (no LLM)

DTOs and pure value objects identified by SpecialistAgent's
`skip_dto` list don't get a ContextAgent call at all. They're emitted
as `Class` entities with auto-generated metadata via a deterministic
extractor — same approach as `_entities_from_trivial_pojo` already in
`entity_extractor.py:1348`, just driven by the plan instead of
heuristics.

This is where the bulk of the navigator's wasted turns went today.
Killing it saves ~10 LLM round-trips per run.

### D4 — Cache-control wired into AnthropicProvider

Audit `AnthropicProvider.chat_json` and confirm:

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT,
     "cache_control": {"type": "ephemeral"}},
    {"role": "user", "content": user_msg},
]
```

The cache hint must be set on the **last block of the cached prefix**
(usually the system message). Today `cache_creation=0 cache_read=0`
across every call in the log → the hint isn't reaching the request.
Fix this once and EVERY downstream call benefits, not just the new
two-agent path.

---

## Options Considered

### Option A — Optimise the existing ReAct loop (cap turns, prune context)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Cost reduction | ~30% (caps cost growth, doesn't fix structure) |
| Quality | Risk: hard turn-cap drops legitimate exploration |
| Effort | 1 day |

Pros: small change, no architecture shift.
Cons: still ≥10 round-trips, still no cache, still no parallelism.
Doesn't address the user's framing of "two agents".

### Option B — Two-agent batched extraction (this ADR)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — new SpecialistAgent + refactor ChunkBatcher caller |
| Cost reduction | ~85% ($0.30 → $0.04) |
| Quality | Equal or better — Specialist sees full handler in one pass |
| Effort | 3-4 days |

Pros: principled separation, cacheable, parallelisable, predictable
cost. Matches the user's mental model.
Cons: requires re-doing the navigator → CodeUnit conversion.

### Option C — Single mega-call (everything in one shot)

Rejected outright. The big-repo failure mode ("doesn't scale to large
classes / fan-out controllers") is a hard requirement to solve, not a
trade-off to accept. ADR-0050 covers the size-bounding mechanisms
(adaptive batching pre-flight + bisection-on-truncation + region-split
+ map-reduce summary) so the batching win from this ADR composes with
a mathematical guarantee that no call truncates silently regardless of
repo size.

---

## Trade-off Analysis

The fundamental change is **swapping serial round-trips for batched,
cached calls**. The win comes from three multiplicative effects:

1. **Round-trip elimination** — 26 navigator turns → 1 specialist call.
   Each round-trip on Haiku has ~700ms latency floor; 25 fewer round-
   trips = 17 seconds saved.

2. **Prompt caching** — 50-edge taxonomy + schema (~3k tokens) cached
   once per session. Today: paid 50× at full price. After: paid 1× at
   full price + 49× at 10% price.

3. **Batched extraction** — 8 methods per call replaces 8 calls. The
   per-call overhead (system prompt, instructions, output framing)
   is amortised across the batch.

The cost of the change: a one-time refactor of the entry-point flow
in `code_tracer.py` and a new `SpecialistAgent` class. The
`KnowledgeNavigatorAgent` stays in the codebase as a fallback for
edge cases where the specialist's structured output doesn't validate.

**Quality risk**: today's ReAct loop can adapt if it goes down a wrong
path (it sees tool results and re-plans). The Specialist gets one
shot. Mitigation: the input includes the full entry handler + a
filtered repo manifest, which is more context than any single
navigator turn ever sees. Empirically, this is enough — the navigator
log shows it was largely doing depth-first traversal of imports, which
is something the Specialist can plan from the manifest alone.

**Recommendation: Option B**, with Option A's turn-cap kept as a
safety net for the legacy ReAct path.

---

## Consequences

**What becomes easier**

- Predictable cost per pipeline run (8-12 LLM calls, all Haiku).
- Predictable wall time (~30 seconds vs 5+ minutes).
- Telemetry: `cache_read_tokens > 0` on every call after the first.
- Adding new entity types: edit the system prompt once, every batch
  call benefits via cache.

**What becomes harder**

- Specialist's output schema must be kept in sync with the downstream
  CodeUnit shape; previously the navigator returned NavigatorNodes
  which were converted by `_knowledge_to_code_units`. Now the
  specialist's plan IS the source of truth.
- Debugging a wrong file selection means inspecting one structured
  response instead of 25 tool-call logs.

**What we'll need to revisit**

- If the SpecialistAgent's plan misses a file in <5% of cases, add a
  cheap deterministic post-step (BFS over imports of files in the
  plan) rather than re-introducing the ReAct loop.
- ContextAgent batch size of 8 is a guess; tune it once the
  end-to-end runs land.

---

## Action Items

1. [ ] Add `SpecialistAgent` class in
       `src/companybrain/agents/specialist_agent.py`. Single-call
       planner; structured JSON output; no tools.
2. [ ] Refactor `code_tracer._trace_java` to call SpecialistAgent
       instead of KnowledgeNavigatorAgent. Keep KnowledgeNavigatorAgent
       behind a feature flag (`BRAIN_USE_LEGACY_NAVIGATOR=true`).
3. [ ] Wire `cache_control: ephemeral` into `AnthropicProvider`'s
       message construction. Add an assertion in tests that
       `cache_read_tokens > 0` after the second call.
4. [ ] Bump `ChunkBatcher` batch size 4 → 8 and remove the
       per-chunk follow-up calls in `chunk_extractor.py`.
5. [ ] Add `_entities_from_dto_plan(skip_dto_list)` helper that emits
       structural Class entities for the planner's `skip_dto` list
       without an LLM call.
6. [ ] Telemetry: log `total_llm_calls`, `cache_read_total`,
       `cache_creation_total`, `total_input_tokens`, `total_output_tokens`,
       `total_cost_usd` per pipeline run. Surface in the job summary.
7. [ ] Acceptance test:
       `tests/acceptance/test_two_agent_extraction_cost.py`
       — assert `total_llm_calls < 15`
       — assert `total_cost_usd < 0.10`
       — assert `cache_read_total > 5_000` (cache is firing)
       — assert `getPayerCompetitors` extracted with non-empty
         `query_text` containing `lob` (extraction quality unchanged)

---

## Companion implementation prompt

A self-contained Claude Code prompt for landing this ADR will live at
`docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0048.md`. It must include:

- The exact JSON schemas for both agents.
- The cache_control wiring location in `AnthropicProvider`.
- The `BRAIN_USE_LEGACY_NAVIGATOR` flag guard so the legacy path can
  be re-enabled with no code change if the new one regresses.
- The acceptance test from action item #7.
