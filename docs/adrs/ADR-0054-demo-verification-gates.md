# ADR-0054 — Demo Verification Gates (proves the brain is investor-ready before you book the meeting)

**Status:** Proposed
**Date:** 2026-05-11
**Deciders:** Chinmay (product), pipeline-team
**Context:** Twice in the last week, what we thought was a working extraction wasn't. The lob column failed to extract because the wrong endpoint was passed; a single-endpoint run came back with `query_text` arrays empty because the legacy extractor's JSON truncated mid-string. **A bad demo in front of an investor is more expensive than a thousand failed test runs.** This ADR defines the verification gates the brain MUST pass before any demo, and ships as a self-contained Claude Code prompt that runs them autonomously.

---

## Context

Demo failure modes we've actually hit:

| What went wrong | Detected when | What it cost |
|---|---|---|
| Endpoint mismatch → wrong files extracted → 18 useless entries from `StaticDataController` + HTML generators | After we tried to answer the lob query | 1 wasted run + 30 minutes of debugging |
| `query_text` arrays empty because LLM hit `max_tokens` mid-string and parser dropped everything | When testing the lob query | 1 wasted run + 6 hours architecting JSON-recovery + bumping tokens |
| `_assemble_chain` truncated every file at 6000 chars regardless of size → navigator's classifier saw uniform clipped view | When user spotted `source_len=6019` repeated 7× in the log | 1 wasted run + a "ADR-0045 task #21 was marked completed prematurely" admission |
| Prompt cache hint dropped on the wire → `cache_creation=0 cache_read=0` everywhere → 10× the cost | When user asked "are we caching?" | $0.30/run instead of $0.03/run for weeks |

These all share one shape: **the system reported "success" but the output was unusable.** Standard tests don't catch this because the failure mode is semantic (the extraction looks plausible; it's just wrong).

We need a **demo-readiness suite**: a set of concrete, automated checks that fail loudly when a known failure mode recurs, and that an agent can run end-to-end without human babysitting.

---

## Decision

Ten **demo verification gates**, executed in sequence. Each gate is a self-contained check with a pass/fail assertion. The suite is shipped as `tests/demo/run_demo_verification.py` and runnable via:

```bash
make -f Makefile.demo verify-demo
```

The acceptance contract: **all 10 gates pass = the brain is ready for an investor demo.** Any failure produces a precise diagnosis + recovery command, not a generic stack trace.

### G1 — Prompt caching is firing

**Why this gate exists:** the cache hint silently dropped on the wire for weeks before anyone noticed. Cost was 10× target.

**What it does:**

```bash
# Trigger any LLM call twice; second call must show cache_read > 0
.venv/bin/brain index --repo $REPO --endpoints "POST /competitiveness/metrics"
.venv/bin/brain index --repo $REPO --endpoints "POST /competitiveness/metrics"  # second run
# Grep the log for cache telemetry
grep "cache_read_tokens" .brain-logs/last-run.jsonl | tail -5
```

**Pass:** at least one log entry has `cache_read_tokens > 1000`.
**Fail:** all `cache_read_tokens = 0`.
**Fix:** check `llm/anthropic_provider.py` — verify `cache_control: {"type": "ephemeral"}` is on the system block, not the user block. Verify SDK version supports it.

### G2 — Cost is bounded

**Why this gate exists:** cost regressions silently 10× the burn rate. ADR-0049 promises < $0.05/run after caching; verify before booking the demo.

**What it does:**

```bash
# Run a 5-endpoint index; sum costs from the job summary
.venv/bin/brain index --repo $REPO --endpoints "$DEMO_ENDPOINTS" --json | jq '.telemetry.total_cost_usd'
```

**Pass:** total < $0.50 for 5 endpoints, AND per-endpoint average < $0.10.
**Fail:** anything higher.
**Fix:** check ADR-0049 caching, ADR-0048 batching, ADR-0050 token estimator pre-flight.

### G3 — Extraction quality (the lob smoke test)

**Why this gate exists:** the lob column was the canonical failure case. If lob isn't extracted, no investor demo answers the marquee question.

**What it does:**

```bash
# Run lob endpoint extraction
.venv/bin/brain index --repo $REPO --endpoints "POST /competitiveness/summary/competitors/payer"

# Hard assertions:
# (a) The plan repo entity exists
JSON_PATH="$REPO/.brain/component/CompetitivenessPlanRepository.getPayerCompetitors.json"
test -f "$JSON_PATH" || fail "no entity for getPayerCompetitors"

# (b) query_text is non-empty AND contains 'lob'
QUERY_TEXT=$(jq -r '.metadata.query_text' "$JSON_PATH")
[[ -n "$QUERY_TEXT" && "$QUERY_TEXT" != "null" ]] || fail "query_text empty"
[[ "$QUERY_TEXT" == *"lob"* ]] || fail "lob not present in query_text"

# (c) code_snippet has the .lob(r.value4()) jOOQ chain
CODE=$(jq -r '.metadata.code_snippet' "$JSON_PATH")
[[ "$CODE" == *".lob("* ]] || fail "jOOQ .lob() chain not extracted"

# (d) confidence ≥ 0.7 (not a guess)
CONF=$(jq -r '.metadata.confidence' "$JSON_PATH")
python -c "exit(0 if float('$CONF') >= 0.7 else 1)" || fail "low confidence: $CONF"
```

**Pass:** all four assertions hold.
**Fail:** any miss.
**Fix:** trace through — if `query_text` is empty, the LLM hit max_tokens (check ADR-0049 token bump + ADR-0050 batch planner). If the file doesn't exist, the navigator dropped CompetitivenessPlanRepository (check ADR-0050 manifest filter). If `.lob(` is missing but `query_text` is non-empty, the prompt's `<do_not_extract>` block (ADR-0053 PR-A) probably skipped it.

### G4 — Reachability (orphan-entity catch)

**Why this gate exists:** the brain has emitted "drift" entities before (entities with zero inbound and zero outbound edges). They pollute the query path with noise.

**What it does:**

```bash
# For each entity in the .brain/, count edges
python -c '
import json, glob, pathlib
for f in glob.glob("$REPO/.brain/component/*.json"):
    d = json.loads(open(f).read())
    edges = d.get("relationships", [])
    if not edges:
        print(f"ORPHAN: {pathlib.Path(f).stem}")
'
```

**Pass:** orphan rate < 10% of entities (some standalone constants are legitimate).
**Fail:** > 30%.
**Fix:** ADR-0043 reachability filter; check it's wired into the enrich path, not just the full pipeline.

### G5 — Query quality (the 5 fixed questions)

**Why this gate exists:** users + investors evaluate the brain on its answer quality, not its extraction count. A populated brain with bad query path is worse than a small brain with great queries.

**The 5 canonical questions** (fixed; never change them):

1. *"What tables and columns does getPayerCompetitors read, and what would break if I rename the lob column?"*
2. *"Walk me through the call chain for POST /competitiveness/summary/competitors/payer. Controller → service → repository → SQL."*
3. *"What is this codebase about? Give me the architecture in 3 paragraphs."*
4. *"Which database tables are read by the most endpoints? What's the highest-risk schema change?"*
5. *"I'm a new engineer. Where should I start to understand the payments/competitor logic?"*

**What it does:** runs each question, validates the response shape + content.

```bash
for QID in 1 2 3 4 5; do
  ANSWER=$(curl -sX POST http://localhost:8000/query \
    -d "$(jq -n --arg q "${QUESTIONS[$QID]}" '{question:$q,workspace_id:"...",repo_path:"...'$REPO'"}')")
  # Structural checks
  echo "$ANSWER" | jq -e '.summary_md' || fail "no summary_md in Q$QID"
  echo "$ANSWER" | jq -e '.confidence' || fail "no confidence in Q$QID"
  # Q1 + Q2 specifically: sql_quotes must contain lob
  if [[ $QID == 1 || $QID == 2 ]]; then
    echo "$ANSWER" | jq -r '.sql_quotes[]' | grep -q "lob" || fail "Q$QID missing lob in sql_quotes"
  fi
  # All: confidence ≥ 0.6
  CONF=$(echo "$ANSWER" | jq -r '.confidence')
  python -c "exit(0 if '$CONF' in ('high','medium') or (str('$CONF').replace('.','').isdigit() and float('$CONF')>=0.6) else 1)" || fail "Q$QID low confidence"
done
```

**Pass:** all 5 questions return structured answers with confidence ≥ 0.6 and Q1/Q2 reference the lob column.
**Fail:** any question returns empty summary, raw markdown wrapped in JSON string, or low confidence.
**Fix:** if Q1/Q2 miss lob, G3 was a false pass — investigate extraction. If everything is structurally empty, check the SmartZoneAssembler / JsonFileBrainStore path (ADR task #19/20 territory).

### G6 — Blast radius (the demo hero)

**Why this gate exists:** this is the visualization investors react to. It must show >= 5 affected files when querying a column rename.

**What it does:**

```bash
# Direct MCP tool call (verifies blast_radius tool works)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"blast_radius","arguments":{"entity_urn":"urn:cb:dev:code:network-iq:column:competitive_payer_plan.lob"}}}' \
  | .venv/bin/python -m companybrain.mcp.server stdio --workspace-id $WORKSPACE_ID \
  | jq '.result.affected_entities | length'
```

**Pass:** ≥ 5 affected entities returned for the lob column rename.
**Fail:** 0 or 1 (means the edge graph is broken).
**Fix:** check `pipeline/structural_edges.py` + `relationship_extractor.py` actually emitted READS_COLUMN edges, not just structural CONTAINS edges.

### G7 — Onboarding flow

**Why this gate exists:** "onboard a new engineer" is one of the two product 1 buyer pitches (Persona 1 from PRODUCT-VISION.md). If the brain can't answer "where do I start?" coherently, the pitch breaks.

**What it does:**

```bash
# Run the onboarding query; validate it returns a multi-paragraph response with module references
ANSWER=$(curl -sX POST http://localhost:8000/query \
  -d '{"question":"I am a new engineer. Walk me through the system in 5 minutes — what are the modules, how do they connect, where should I read first?","workspace_id":"...","repo_path":"'$REPO'"}')

# Structural
WORD_COUNT=$(echo "$ANSWER" | jq -r '.summary_md' | wc -w)
[[ $WORD_COUNT -ge 200 ]] || fail "onboarding answer too short: $WORD_COUNT words"

# Mentions ≥ 3 actual files from the repo
MENTIONS=$(echo "$ANSWER" | jq -r '.affected_entities[]' | wc -l)
[[ $MENTIONS -ge 3 ]] || fail "onboarding cited < 3 files"
```

**Pass:** ≥ 200-word answer with ≥ 3 cited files.
**Fail:** generic boilerplate or no citations.
**Fix:** the SmartZoneAssembler likely got a sparse zone for an open-ended question; investigate the T0/T1/T2 sizing for non-endpoint queries.

### G8 — Reproducibility (snapshot → rebuild)

**Why this gate exists:** between investor meetings, you'll `wipe` and `rebuild-from-json`. If the rebuild path doesn't produce identical brain state, the demo will degrade silently across the day.

**What it does:**

```bash
# Snapshot
cp -r $REPO/.brain $REPO/.brain.snapshot

# Capture original query answer (golden)
ANSWER_BEFORE=$(curl -sX POST http://localhost:8000/query -d '{"question":"... Q1 ...","repo_path":"'$REPO'"}')
echo "$ANSWER_BEFORE" | jq -r '.summary_md' > /tmp/before.md

# Wipe + rebuild
make -f Makefile.demo wipe
rm -rf $REPO/.brain
cp -r $REPO/.brain.snapshot $REPO/.brain
.venv/bin/brain rebuild-from-json --repo $REPO --workspace-id $WORKSPACE_ID

# Re-query
ANSWER_AFTER=$(curl -sX POST http://localhost:8000/query -d '{"question":"... Q1 ...","repo_path":"'$REPO'"}')
echo "$ANSWER_AFTER" | jq -r '.summary_md' > /tmp/after.md

# Compare (allow some Sonnet wording variance; both must mention lob)
grep -q "lob" /tmp/before.md && grep -q "lob" /tmp/after.md \
  || fail "rebuild lost lob context"
```

**Pass:** both queries cite lob.
**Fail:** rebuild silently lost data.
**Fix:** `rebuild-from-json` likely missed Qdrant or Neo4j; check `cli_helpers/brain_rebuild.py`.

### G9 — MCP server (external client smoke test)

**Why this gate exists:** Product 4 (AI Agent Substrate) is the strategic flag for the seed round. If the MCP server doesn't respond to a standard MCP client, the Cursor-integration demo dies.

**What it does:**

```bash
# Spin up MCP server in stdio mode; pipe a standard MCP tools/list request
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | timeout 30 .venv/bin/python -m companybrain.mcp.server stdio --workspace-id $WORKSPACE_ID \
  | tee /tmp/mcp-resp.json

# Assert ≥ 5 tools listed
TOOL_COUNT=$(jq '.result.tools | length' /tmp/mcp-resp.json)
[[ $TOOL_COUNT -ge 5 ]] || fail "MCP server returned only $TOOL_COUNT tools"

# Assert query_brain is one of them
jq -e '.result.tools[] | select(.name == "query_brain")' /tmp/mcp-resp.json || fail "query_brain not exposed"
```

**Pass:** server responds, ≥ 5 tools, includes `query_brain`.
**Fail:** server crashes, hangs, or returns wrong shape.
**Fix:** check `mcp/server.py::run_stdio()` and that `BRAIN_USE_HARNESS=true` isn't required for it (it should run standalone).

### G10 — Latency (the demo can't pause)

**Why this gate exists:** an investor demo with a 30-second pause loses the room. Every query must return in under 10 seconds; extraction must average under 30 seconds per endpoint.

**What it does:**

```bash
# Re-run G5 questions with wall-time measurement
for QID in 1 2 3 4 5; do
  START=$(date +%s)
  curl -sX POST http://localhost:8000/query -d "...Q$QID..." > /dev/null
  ELAPSED=$(( $(date +%s) - $START ))
  [[ $ELAPSED -le 10 ]] || fail "Q$QID took ${ELAPSED}s (limit 10s)"
done
```

**Pass:** every query < 10s.
**Fail:** any > 10s.
**Fix:** check Sonnet model variant (the demo should use `claude-sonnet-4-6` with caching, not Opus), check SmartZoneAssembler doesn't over-stuff context.

---

## Output format

The agent running the suite produces one of three outcomes per gate, written to `demo-verification-report.md`:

```markdown
# Demo Verification Report

**Repo:** network-iq-backend-java
**Commit:** abc123def
**Run at:** 2026-05-11T14:32:00Z
**Overall:** 9/10 PASS — DEMO READY WITH CAVEATS

| Gate | Status | Detail |
|---|---|---|
| G1 — Caching | ✅ PASS | cache_read_tokens=4287 on call 2 |
| G2 — Cost bounded | ✅ PASS | $0.34 / 5 endpoints (under $0.50) |
| G3 — lob extraction | ✅ PASS | query_text 1247 chars, contains .lob(r.value4()) |
| G4 — Reachability | ✅ PASS | 4/57 orphans (7%) |
| G5 — Query quality | ⚠️ DEGRADED | Q4 confidence=0.55 (below 0.6 threshold) |
| G6 — Blast radius | ✅ PASS | 12 affected entities for lob |
| G7 — Onboarding | ✅ PASS | 312 words, 6 cited files |
| G8 — Reproducibility | ✅ PASS | both pre/post mention lob |
| G9 — MCP server | ✅ PASS | 10 tools, query_brain present |
| G10 — Latency | ✅ PASS | all queries 3-7s |

**Demo readiness verdict:**
- Q1, Q2, Q3, Q5: ready for live demo
- Q4: rehearse offline; brain doesn't have strong cross-endpoint stats yet

**Recommended fixes before next investor meeting:**
1. (G5/Q4) Boost cross-endpoint aggregation: add an `endpoint_count_per_table` materialized view; ~2 hours of work.
```

---

## Action items

1. [ ] `tests/demo/run_demo_verification.py` — implements the 10 gates above as a single Python script.
2. [ ] `tests/demo/fixtures/` — pinned commit SHA of `network-iq-backend-java` + the 5 canonical questions + expected substrings.
3. [ ] `Makefile.demo` target: `verify-demo` runs the script.
4. [ ] CI integration: nightly run against the pinned fixture commit; alert if pass count drops.
5. [ ] Output report committed to `demo-verification-report.md`; investor demos require this file dated within 48h.

---

## Companion implementation prompt

See `SONNET-IMPLEMENTATION-PROMPT-ADR-0054.md` — self-contained Claude Code session that lands the verification suite + runs it + produces the first report.
