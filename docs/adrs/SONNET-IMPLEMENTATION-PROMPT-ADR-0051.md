# Implementation Prompt — ADR-0051 (agentic harness foundation, phases P1–P4)

**You are landing the foundational agentic harness in this repo. This is one of TWO Claude Code sessions for the harness migration. Your session covers phases P1–P4 of ADR-0051 (~22 days of work, expect to ship 4 PRs sequentially under one coordinated branch). The other session will land ADR-0052 (P5–P7: slash commands, MCP, marketplace, IDE) on top of your work.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` start-to-finish. This prompt orchestrates; the ADR is the spec.
2. Read `docs/adrs/ADR-0052-comprehensive-feature-adoption.md` §"Phase plan" for the full feature inventory you're delivering across P1–P4.
3. **Prerequisite ADRs (must be on `main` first):** ADR-0048, ADR-0049, ADR-0050. Verify:
   ```bash
   for adr in 0048 0049 0050; do
     git log --oneline main | head -100 | grep -q "ADR-${adr}" \
       && echo "✓ ADR-${adr} merged" \
       || echo "✗ ADR-${adr} MISSING — block on it before continuing"
   done
   ```
   If any are missing, **stop**. Land them first (their prompts are at `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-004{8,9}.md` and `…-ADR-0050.md`).
4. Create a coordinated branch: `git checkout -b feature/adr-0051-harness-foundation` from `main`.
5. You will land **four sub-PRs off this branch** in sequence, one per phase. Each sub-PR is rebased onto the previous one's merged state.

---

## File ownership for THIS coordinated branch (do not touch anything else; ADR-0052's session owns the extensions)

You exclusively own and may CREATE / MODIFY:

```
src/companybrain/harness/                           # NEW DIRECTORY
src/companybrain/harness/__init__.py
src/companybrain/harness/loop.py                    # P1 — HarnessLoop
src/companybrain/harness/tools/                     # P1 — tool registry (one file per tool)
src/companybrain/harness/system_prompt.py           # P1
src/companybrain/harness/subagent.py                # P2
src/companybrain/harness/skills.py                  # P3
src/companybrain/harness/memory.py                  # P3 — BRAIN.md reader/writer
src/companybrain/harness/hooks.py                   # P4
src/companybrain/harness/permissions.py             # P4
src/companybrain/harness/progress.py                # P4 — TodoList + SSE
src/companybrain/harness/compaction.py              # P4
src/companybrain/harness/session.py                 # P4
src/companybrain/harness/cost.py                    # P4 — per-tool-call cost tracker

frameworks/                                          # NEW DIRECTORY
frameworks/spring-boot/SKILL.md                     # P3
frameworks/fastapi/SKILL.md                         # P3
frameworks/nestjs/SKILL.md                          # P3
frameworks/django/SKILL.md                          # P3
frameworks/rails/SKILL.md                           # P3
frameworks/nextjs/SKILL.md                          # P3

.brain-template/                                     # NEW — copied into a repo on first init
.brain-template/BRAIN.md                            # P3 — template
.brain-template/hooks/pre_extraction.sh.example     # P4
.brain-template/hooks/post_extraction.sh.example    # P4
.brain-template/hooks/on_truncation.sh.example      # P4

docs/HARNESS.md                                     # P1 (created), maintained through P4
docs/FEATURE-INDEX.md                               # P4

tests/unit/test_harness_loop.py                     # P1
tests/unit/test_subagent.py                         # P2
tests/unit/test_skills.py                           # P3
tests/unit/test_memory.py                           # P3
tests/unit/test_hooks.py                            # P4
tests/unit/test_permissions.py                      # P4
tests/acceptance/test_harness_p1.py                 # P1
tests/acceptance/test_harness_p2_subagents.py       # P2
tests/acceptance/test_harness_p3_skills.py          # P3
tests/acceptance/test_harness_p4_full.py            # P4
```

You may make **append-only** changes (new functions / new env-flag-gated branches) to:

```
src/companybrain/pipeline/orchestrator.py    # add `BRAIN_USE_HARNESS=true` delegate to HarnessLoop
src/companybrain/api/routes/pipeline.py      # add SSE stream endpoint
src/companybrain/api/routes/jobs.py          # surface tool-call telemetry
src/companybrain/cli.py                      # add `brain session list/resume/transcript`
src/companybrain/config.py                   # harness tunables
```

Do NOT modify any other file. ADR-0052's session owns:

```
src/companybrain/harness/commands/    # slash commands (P5)
src/companybrain/harness/mcp_server.py # P5
src/companybrain/harness/scheduler.py  # P6
src/companybrain/harness/plugins.py    # P6
ide/vscode-extension/                  # P7
```

---

## Sub-PR sequence

Land four sub-PRs sequentially under `feature/adr-0051-harness-foundation`. Each sub-PR rebases onto its predecessor and ships its own acceptance test.

### Sub-PR 1: Phase P1 — Tool-use harness (~5 days)

**Branch:** `feature/adr-0051-p1-harness-loop` (off main → merge into the coordinated branch)

**Deliverables:**

- `harness/loop.py::HarnessLoop` — owns conversation history; runs the assistant↔tool_use loop until text-only response.
- `harness/tools/` — one file per tool, each registering via `@tool(name=..., schema=...)`. Initial catalog (each wraps an existing pipeline call):
  - `discover_routes` — wraps existing `discover_routes()` from `code_tracer.py`
  - `find_entry_handler` — wraps `find_entry_handler()` from `code_tools.py`
  - `list_candidate_files` — wraps `manifest_filter.build_filtered_manifest()` (from ADR-0050)
  - `read_file` — uses FileCache from ADR-0049
  - `glob_files` — new, ripgrep-style file matching
  - `grep_code` — new, regex-search across repo
  - `extract_methods_from_class` — wraps `ContextAgent.extract_batch` (from ADR-0048)
  - `write_to_brain` — wraps existing brain writers
  - `finalize_brain` — wraps existing manifest builder
- `harness/system_prompt.py` — generated from a template that includes per-tool descriptions. Encodes today's pipeline as guidance ("first discover entry handler, then list candidates, then plan, then extract, then write").
- `BRAIN_USE_HARNESS=true` env flag — when on, `orchestrator.run_pipeline` delegates to `HarnessLoop`. Default false.
- `docs/HARNESS.md` — first version, explains the loop architecture.

**Acceptance test (`tests/acceptance/test_harness_p1.py`):**

```python
async def test_harness_extracts_lob_endpoint_matching_legacy():
    """Run the lob endpoint through the harness; brain output must match
    the legacy pipeline's output within ±5% on entity count and ±0% on
    presence of the .lob() jOOQ chain."""
    legacy_result = await run_pipeline_legacy(
        endpoint="/competitiveness/summary/competitors/payer", method="POST",
    )
    harness_result = await run_pipeline_harness(
        endpoint="/competitiveness/summary/competitors/payer", method="POST",
    )
    assert abs(legacy_result.entity_count - harness_result.entity_count) / legacy_result.entity_count < 0.05
    legacy_lob = legacy_result.brain.read("CompetitivenessPlanRepository.getPayerCompetitors")
    harness_lob = harness_result.brain.read("CompetitivenessPlanRepository.getPayerCompetitors")
    assert "lob" in (harness_lob.metadata.get("query_text") or "")
```

**PR description for sub-PR 1:**

```
feat(harness): tool-use loop foundation (ADR-0051 P1)

Replaces the linear stage-machine in orchestrator.py with a prompt-controlled
HarnessLoop. 8 tools wrap existing pipeline calls; the model decides which
to invoke based on conversation state.

Behind BRAIN_USE_HARNESS=true. Legacy path remains default until P4 acceptance
suite is green.

Acceptance: lob endpoint extracted via harness matches legacy output ±5%.
```

### Sub-PR 2: Phase P2 — Sub-agents and parallel fan-out (~5 days)

**Branch:** `feature/adr-0051-p2-subagents` (rebases onto P1)

**Deliverables:**

- `harness/subagent.py::Subagent` — minimal sub-agent runner with its own context window + configurable tool subset.
- `harness/tools/spawn_extractor.py` — Task-style tool; spawns ExtractorAgent per file or per class.
- `harness/tools/spawn_verifier.py` — VerifierAgent that cross-checks claims (e.g. "this entity says it READS_COLUMN `lob` — verify by grep").
- `harness/tools/spawn_research.py` — ResearchAgent with WebFetch tool for framework-doc lookups.
- Parent agent fires N `spawn_extractor` calls in one assistant turn; harness runs them in parallel (`max_subagents=8`).
- Sub-agent context drops from parent's full conversation (~50 KB) to file-scoped (~10 KB).

**Acceptance test (`tests/acceptance/test_harness_p2_subagents.py`):**

```python
async def test_60_method_endpoint_under_30_seconds():
    result = await run_pipeline_harness(
        endpoint="/competitiveness/summary/competitors/payer", method="POST",
    )
    assert result.telemetry["total_wall_time_seconds"] < 30
    assert result.telemetry["subagent_count"] >= 5    # at least one per major file


async def test_subagent_context_isolation():
    """Sub-agents must not see the parent's full conversation."""
    spans = await run_pipeline_harness_with_tracing(...)
    parent_span = next(s for s in spans if s.name == "harness.parent")
    for sa in [s for s in spans if s.name.startswith("harness.subagent.")]:
        assert sa.input_tokens < parent_span.input_tokens / 2
```

**PR description for sub-PR 2:**

```
feat(harness): sub-agents and parallel fan-out (ADR-0051 P2)

Adds spawn_extractor/spawn_verifier/spawn_research Task-style tools.
Each sub-agent has its own context window (~5× smaller input) and
configurable tool subset.

Acceptance: 60-method endpoint completes < 30s wall time; sub-agent
input < 50% of parent input.
```

### Sub-PR 3: Phase P3 — Skills + memory (~5 days)

**Branch:** `feature/adr-0051-p3-skills-memory` (rebases onto P2)

**Deliverables:**

- `harness/skills.py` — detects primary framework (file-extension count + framework markers) + loads at most one SKILL.md into the system prompt.
- `frameworks/spring-boot/SKILL.md`, `frameworks/fastapi/SKILL.md`, `frameworks/nestjs/SKILL.md`, `frameworks/django/SKILL.md`, `frameworks/rails/SKILL.md`, `frameworks/nextjs/SKILL.md` — each ~2000 tokens of framework-specific extraction guidance (annotation conventions, common idioms, where to look for SQL, common DTO patterns to skip).
- `harness/memory.py::BrainMemory` — reads `.brain/BRAIN.md` per repo on every run; auto-appends observations (with the file-state-tracking pattern from Claude Code: must read current state before write).
- `.brain-template/BRAIN.md` — template for `brain init` to copy.

**Acceptance test (`tests/acceptance/test_harness_p3_skills.py`):**

```python
async def test_same_extraction_works_on_spring_and_fastapi():
    """No orchestrator code change between repos — only SKILL.md selection."""
    spring_result  = await run_pipeline_harness(repo="fixtures/spring-boot-sample", endpoint=..., method=...)
    fastapi_result = await run_pipeline_harness(repo="fixtures/fastapi-sample",   endpoint=..., method=...)
    assert spring_result.entity_count > 5
    assert fastapi_result.entity_count > 5
    assert spring_result.skill_loaded == "spring-boot"
    assert fastapi_result.skill_loaded == "fastapi"


async def test_brain_memory_persists_observations():
    """First run extracts; BRAIN.md should auto-append a 'skip' note when
    a class is dropped 3 runs in a row by the reachability filter."""
    await run_pipeline_harness(...)   # x3 against same endpoint
    brain_md = (Path("fixtures/.../.brain/BRAIN.md")).read_text()
    assert "JsonKeyMapping" in brain_md
    assert "skip" in brain_md.lower()
```

**PR description for sub-PR 3:**

```
feat(harness): skills system + persistent memory (ADR-0051 P3)

Adds frameworks/{spring-boot,fastapi,nestjs,django,rails,nextjs}/SKILL.md
loaded on demand by the skill-detector. Adds .brain/BRAIN.md per-repo
memory file with auto-append for recurring observations.

Acceptance: same lob extraction works on Spring AND FastAPI repos with
no orchestrator code change. BRAIN.md auto-appends recurring drops.
```

### Sub-PR 4: Phase P4 — Hooks + permissions + streaming + introspection (~7 days)

**Branch:** `feature/adr-0051-p4-hooks-streaming` (rebases onto P3)

**Deliverables:**

- `harness/hooks.py` — invokes `.brain/hooks/*.sh` at: `pre_extraction`, `post_extraction`, `on_truncation`, `pre_storage`, `post_storage`, `pre_query`, `post_query`, `session_start`, `session_end`. Hook output (JSON on stdout) can modify the harness's next decision.
- `harness/permissions.py` — per-tool capability declarations × per-workspace grants. Replaces scattered `BRAIN_*` flag checks. Auto / ask / deny tiers.
- `harness/progress.py::TodoList` — structured task tree updated by sub-agents; serialised to SSE stream.
- `/pipeline/jobs/{id}/stream` SSE endpoint — UI subscribes; replaces 2-second polling.
- `harness/compaction.py` — when parent harness conversation grows beyond 80% of context window, auto-compact (preserve plan + key decisions, drop completed sub-agent transcripts).
- CLI status line: `[STAGE: ContextAgent] [COST: $0.012] [FILES: 3/15] [ETA: 12s]` — printed live during `make demo run-cli`.
- `harness/session.py` — `brain session list / resume / transcript`. Builds on existing checkpoint code; promotes to per-tool-call granularity.
- `harness/cost.py` — per-tool-call cost surfaced in job summary.
- Brain-diff before storage — list of new/changed/deleted entities since prior run; `--yes` flag for non-interactive.
- `brain tools list` CLI — exposes the tool registry to the user.
- `docs/FEATURE-INDEX.md` — cross-references every adopted feature → phase + ADR + code location + test.

**Acceptance test (`tests/acceptance/test_harness_p4_full.py`):**

```python
async def test_full_run_with_hooks_streaming_and_permissions():
    """End-to-end harness run with: a custom pre_extraction hook that
    drops files matching a pattern; SSE stream observed by a test client;
    permission denial for an unsupported tool."""
    write_hook(".brain/hooks/pre_extraction.sh", DROP_TEST_FILES_HOOK)
    progress = []
    async with sse_client(f"/pipeline/jobs/{{job_id}}/stream") as stream:
        result = await run_pipeline_harness_async(...)
        async for ev in stream:
            progress.append(ev)
    assert any("subagent_started" in e.type for e in progress)
    assert any("subagent_completed" in e.type for e in progress)
    assert all(not f.endswith("_test.java") for f in result.files_extracted)
    assert result.telemetry["hook_invocations"]["pre_extraction"] == 1


async def test_compaction_keeps_plan_after_context_fills():
    """Synthetic huge endpoint; assert parent context never exceeds 80% AND
    the SpecialistAgent's plan is preserved after compaction."""
    result = await run_pipeline_harness(repo="fixtures/synthetic-huge-repo", ...)
    assert result.telemetry["max_context_used"] < 0.8 * MODEL_CONTEXT_WINDOW
    assert result.telemetry["compaction_invocations"] >= 1
    assert "plan" in result.telemetry["preserved_after_compaction"]
```

**PR description for sub-PR 4:**

```
feat(harness): hooks + permissions + streaming + introspection (ADR-0051 P4)

Adds:
- Hooks at 9 events (.brain/hooks/*.sh)
- Per-tool permission model (auto/ask/deny × workspace grants)
- TodoList + SSE streaming on /pipeline/jobs/{id}/stream
- Auto-compaction at 80% context fill
- CLI status line with live cost/stage/ETA
- brain session list/resume/transcript
- Per-tool-call cost telemetry
- Brain-diff preview before storage
- brain tools list

After this PR lands AND the acceptance suite is green for two weeks,
flip BRAIN_USE_HARNESS=true as default and remove the legacy
orchestrator stage machine.
```

---

## Coordinated branch merge

After all four sub-PRs merge into `feature/adr-0051-harness-foundation`:

```bash
git checkout main
git merge --no-ff feature/adr-0051-harness-foundation \
  -m "feat(harness): land ADR-0051 P1–P4 (agentic harness foundation)"
git push
```

Then notify the ADR-0052 session that `main` is ready for them to rebase onto.

---

## Verification (run before opening EACH sub-PR)

```bash
.venv/bin/mypy src/companybrain/harness
.venv/bin/ruff check src/companybrain/harness
.venv/bin/pytest tests/unit/test_harness_*.py -v
.venv/bin/pytest tests/acceptance/test_harness_pN_*.py -v   # N = phase number
```

All must pass. The acceptance suite is the contract for "this phase is done".

---

## Things to NOT do (these belong to the OTHER session — ADR-0052)

Do not start on:

- Slash commands (`/extract`, `/query`, etc.) — P5, ADR-0052
- MCP server (brain-as-MCP for external consumers) — P5, ADR-0052
- Workspace dataclass refactor — P5, ADR-0052
- Worktrees per-job — P5, ADR-0052
- Bash tool / terminal room — P5, ADR-0052
- WebFetch / WebSearch — P5, ADR-0052 (your sub-agents currently don't get these)
- Headless mode / SDK / output JSON — P5, ADR-0052
- Plugin marketplace / scheduled / notebook / image / browser-verifier / notes — P6, ADR-0052
- IDE integration — P7, ADR-0052

If you find yourself reaching for one of these, STOP. Note it as a follow-up
for the ADR-0052 session and continue with your phase.
