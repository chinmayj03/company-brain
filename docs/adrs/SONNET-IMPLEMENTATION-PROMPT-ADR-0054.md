# Implementation Prompt — ADR-0054 (Demo Verification Gates)

**You are running a single-session Claude Code job. Your task: implement the 10 demo verification gates from ADR-0054, run them end-to-end against `network-iq-backend-java`, and produce `demo-verification-report.md`. After your run, the user should be able to look at one file and know whether the brain is investor-demo-ready.**

**Estimated effort: 1 working day.** This is a TEST/QA PR, not a feature PR — no production code changes unless a gate fails and the fix is small enough to land in the same session.

---

## Pre-flight (run these first; ABORT if any fail)

```bash
cd /Users/chinmayjadhav/Documents/Claude/Projects/company-brain

# 0a. Confirm git state is clean
git status                                       # must say "working tree clean"
git log --oneline -1                             # capture the commit SHA for the report

# 0b. Confirm infrastructure is up
make -f Makefile.demo health                     # all green
# If anything is red: `make -f Makefile.demo up-all` then re-check

# 0c. Confirm the target repo exists at the expected path
test -d /Users/chinmayjadhav/Documents/network-iq-backend-java \
  || { echo "target repo missing — abort"; exit 1; }

# 0d. Confirm the Python service is responsive
curl -sf http://localhost:8000/health | jq

# 0e. Confirm cb-api is responsive
curl -sf http://localhost:8090/health | jq

# 0f. Confirm cost cap is set high enough for the suite
grep "BRAIN_JOB_BUDGET_USD" .env
# If < 5.00, update it to 5.00 temporarily for the suite
```

If pre-flight fails, **stop immediately** and report which step failed. Don't try to fix infra silently — that's the user's job.

---

## Deliverables for this PR

```
tests/demo/run_demo_verification.py          # NEW — main entry point
tests/demo/fixtures/__init__.py               # NEW
tests/demo/fixtures/canonical_questions.py    # NEW — the 5 fixed Q&A pairs
tests/demo/fixtures/expected_substrings.py    # NEW — required substrings per gate
tests/demo/gates/__init__.py                  # NEW
tests/demo/gates/g1_caching.py                # NEW — Gate 1
tests/demo/gates/g2_cost.py                   # NEW — Gate 2
tests/demo/gates/g3_lob_extraction.py         # NEW — Gate 3
tests/demo/gates/g4_reachability.py           # NEW — Gate 4
tests/demo/gates/g5_query_quality.py          # NEW — Gate 5
tests/demo/gates/g6_blast_radius.py           # NEW — Gate 6
tests/demo/gates/g7_onboarding.py             # NEW — Gate 7
tests/demo/gates/g8_reproducibility.py        # NEW — Gate 8
tests/demo/gates/g9_mcp_server.py             # NEW — Gate 9
tests/demo/gates/g10_latency.py               # NEW — Gate 10
tests/demo/reporter.py                        # NEW — generates demo-verification-report.md
Makefile.demo                                  # APPEND-ONLY — add `verify-demo` target
demo-verification-report.md                   # NEW — generated artifact in repo root
```

File ownership: you exclusively touch `tests/demo/` and append the new `verify-demo` target to `Makefile.demo`. Do not modify any other file.

If any gate fails for a known-fixable reason (e.g. caching not wired, prompt-cache hint missing, etc.), document the fix in the report — DO NOT silently patch production code in this PR. Production fixes get their own ADR + PR.

---

## Implementation skeleton

### `run_demo_verification.py` — main entry

```python
"""Demo verification suite — runs all 10 gates, produces a report.

Usage:
    python tests/demo/run_demo_verification.py \
        --repo /Users/chinmayjadhav/Documents/network-iq-backend-java \
        --workspace 00000000-0000-0000-0000-000000000001 \
        --report demo-verification-report.md

Exit codes:
    0  — all gates pass (DEMO READY)
    1  — at least one gate fails (NOT READY)
    2  — pre-flight failed (couldn't even start)
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime
import subprocess
import sys
from pathlib import Path

from tests.demo.gates import (
    g1_caching, g2_cost, g3_lob_extraction, g4_reachability,
    g5_query_quality, g6_blast_radius, g7_onboarding,
    g8_reproducibility, g9_mcp_server, g10_latency,
)
from tests.demo.reporter import write_report

GATES = [
    ("G1 — Caching",          g1_caching.run),
    ("G2 — Cost bounded",     g2_cost.run),
    ("G3 — lob extraction",   g3_lob_extraction.run),
    ("G4 — Reachability",     g4_reachability.run),
    ("G5 — Query quality",    g5_query_quality.run),
    ("G6 — Blast radius",     g6_blast_radius.run),
    ("G7 — Onboarding",       g7_onboarding.run),
    ("G8 — Reproducibility",  g8_reproducibility.run),
    ("G9 — MCP server",       g9_mcp_server.run),
    ("G10 — Latency",         g10_latency.run),
]


@dataclasses.dataclass
class GateResult:
    name: str
    status: str  # 'PASS' | 'DEGRADED' | 'FAIL'
    detail: str
    duration_s: float
    fix_recommendation: str | None = None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workspace", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--report", default="demo-verification-report.md")
    args = parser.parse_args()

    results: list[GateResult] = []
    overall_pass = True
    for name, fn in GATES:
        print(f"▶ Running {name}…", flush=True)
        import time
        start = time.perf_counter()
        try:
            result = await fn(repo=args.repo, workspace=args.workspace)
        except Exception as exc:
            result = GateResult(
                name=name, status="FAIL",
                detail=f"unhandled exception: {exc!r}",
                duration_s=time.perf_counter() - start,
                fix_recommendation="debug the gate runner itself; see traceback",
            )
        result.duration_s = time.perf_counter() - start
        result.name = name
        results.append(result)
        if result.status == "FAIL":
            overall_pass = False
        print(f"  └─ {result.status} ({result.duration_s:.1f}s) — {result.detail}", flush=True)

    write_report(
        path=Path(args.report),
        results=results,
        repo=args.repo,
        commit_sha=subprocess.check_output(
            ["git", "-C", args.repo, "rev-parse", "HEAD"]
        ).decode().strip(),
        run_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    )
    print(f"\nReport: {args.report}")
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
```

### Gate skeleton (use this shape for every gate)

```python
# tests/demo/gates/g1_caching.py
"""Gate 1: Prompt caching is firing.

Runs the same one-endpoint extraction twice; verifies cache_read_tokens > 1000
on the second run. Failure means ADR-0049 C1 didn't actually land — fix the
AnthropicProvider before any other gate is meaningful.
"""
import subprocess
import json
from tests.demo.run_demo_verification import GateResult


async def run(*, repo: str, workspace: str) -> GateResult:
    # Run twice
    for _ in range(2):
        subprocess.run(
            [".venv/bin/brain", "index",
             "--repo", repo,
             "--workspace-id", workspace,
             "--endpoints", "POST /competitiveness/metrics"],
            check=True, capture_output=True,
        )

    # Grep the log
    log_path = ".brain-logs/last-run.jsonl"
    cache_reads = []
    with open(log_path) as f:
        for line in f:
            try:
                ev = json.loads(line)
                if "cache_read_tokens" in ev:
                    cache_reads.append(ev["cache_read_tokens"])
            except json.JSONDecodeError:
                continue

    if any(cr > 1000 for cr in cache_reads):
        return GateResult(
            name="", status="PASS",
            detail=f"max cache_read_tokens={max(cache_reads)} across {len(cache_reads)} calls",
            duration_s=0,
        )
    return GateResult(
        name="", status="FAIL",
        detail=f"cache_read_tokens=0 on all {len(cache_reads)} calls — cache hint dropped",
        duration_s=0,
        fix_recommendation=(
            "Inspect llm/anthropic_provider.py: cache_control: {type: ephemeral} "
            "must be on the LAST system block, AND the system param must be a "
            "list of typed blocks (not a string). Re-run after fix."
        ),
    )
```

**Implement g2–g10 in the same shape** — each gate is one async `run(repo, workspace) -> GateResult` function. Pass `GateResult` with `status`, `detail`, and (on FAIL) `fix_recommendation`.

### Gate-specific implementation notes

**G2 (Cost):**
```python
result = subprocess.run([".venv/bin/brain", "index", "--repo", repo,
                          "--endpoints", DEMO_ENDPOINTS, "--json"],
                         capture_output=True, text=True, check=True)
payload = json.loads(result.stdout)
cost = payload["telemetry"]["total_cost_usd"]
endpoint_count = payload["telemetry"]["endpoints_extracted"]
avg = cost / max(1, endpoint_count)
if cost <= 0.50 and avg <= 0.10:
    return PASS
elif cost <= 1.00:
    return DEGRADED  # warn but don't fail
else:
    return FAIL
```

**G3 (lob extraction):** the 4-assertion check from the ADR. Use `jq`-equivalent in Python: `json.load(open(json_path))` then dictionary access. Required substrings: `"lob"` in `query_text`, `".lob("` in `code_snippet`, confidence ≥ 0.7.

**G4 (Reachability):**
```python
import glob, json, pathlib
orphans = []
total = 0
for f in glob.glob(f"{repo}/.brain/component/*.json"):
    total += 1
    d = json.load(open(f))
    if not d.get("relationships"):
        orphans.append(pathlib.Path(f).stem)
ratio = len(orphans) / max(1, total)
status = "PASS" if ratio < 0.10 else ("DEGRADED" if ratio < 0.30 else "FAIL")
```

**G5 (Query quality):** loop over 5 questions from `fixtures/canonical_questions.py`, hit `POST http://localhost:8000/query`, validate each response's shape + content. For Q1 + Q2, additionally check `"lob"` appears in `sql_quotes`.

**G6 (Blast radius):** spawn the MCP server via subprocess piping stdio; send the JSON-RPC request; parse the response; assert `len(affected_entities) >= 5`.

**G7 (Onboarding):** single curl to `/query` with the onboarding question; count words in `summary_md`; count entries in `affected_entities`.

**G8 (Reproducibility):** uses `subprocess.run` for `make -f Makefile.demo wipe` + `cp -r snapshot .brain` + `.venv/bin/brain rebuild-from-json`. Compares Q1's `summary_md` before/after for the substring `"lob"`.

**G9 (MCP server):** uses `subprocess.Popen` with `stdin=PIPE, stdout=PIPE` to spawn the MCP server in stdio mode; sends `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`; parses the response.

**G10 (Latency):** wraps each G5 query in `time.perf_counter()` and asserts `elapsed < 10.0`.

### Reporter (`tests/demo/reporter.py`)

```python
"""Generates demo-verification-report.md from gate results."""
from pathlib import Path
from typing import Sequence


def write_report(*, path: Path, results: Sequence, repo: str,
                 commit_sha: str, run_at: str) -> None:
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    degraded   = sum(1 for r in results if r.status == "DEGRADED")
    overall = (
        "DEMO READY" if fail_count == 0 and degraded == 0
        else "DEMO READY WITH CAVEATS" if fail_count == 0
        else "NOT READY"
    )

    lines = [
        "# Demo Verification Report",
        "",
        f"**Repo:** {repo}",
        f"**Commit:** `{commit_sha}`",
        f"**Run at:** {run_at}",
        f"**Overall:** {pass_count}/{len(results)} PASS — **{overall}**",
        "",
        "| Gate | Status | Duration | Detail |",
        "|---|---|---|---|",
    ]
    for r in results:
        icon = {"PASS": "✅", "DEGRADED": "⚠️", "FAIL": "❌"}[r.status]
        lines.append(f"| {r.name} | {icon} {r.status} | {r.duration_s:.1f}s | {r.detail} |")

    if fail_count or degraded:
        lines += ["", "## Recommended fixes before next investor meeting", ""]
        for r in results:
            if r.status != "PASS" and r.fix_recommendation:
                lines.append(f"- **{r.name}**: {r.fix_recommendation}")

    path.write_text("\n".join(lines) + "\n")
```

### `Makefile.demo` addition (append-only)

```makefile
# ── Demo verification suite ───────────────────────────────────────────────────
.PHONY: verify-demo
verify-demo:
	@echo "$(CYAN)→ Running demo verification (10 gates)$(NC)"
	@cd company-brain-ai && .venv/bin/python ../tests/demo/run_demo_verification.py \
	  --repo $(TARGET_REPO) \
	  --workspace $(WORKSPACE_ID) \
	  --report ../demo-verification-report.md
	@echo ""
	@echo "$(GREEN)→ Report: demo-verification-report.md$(NC)"
	@head -10 demo-verification-report.md
```

---

## How to run

```bash
# After implementing all 10 gates and the reporter:
make -f Makefile.demo verify-demo

# Inspect the report:
cat demo-verification-report.md

# Re-run any single gate manually if you want to dig in:
cd company-brain-ai
.venv/bin/python -c "
import asyncio
from tests.demo.gates.g3_lob_extraction import run
result = asyncio.run(run(
    repo='/Users/chinmayjadhav/Documents/network-iq-backend-java',
    workspace='00000000-0000-0000-0000-000000000001',
))
print(result)
"
```

---

## Expected outcomes (what to put in the PR description)

After your first end-to-end run, the report will land in one of three states:

**State A — All 10 pass (best case):** the brain is investor-demo-ready. Commit the report + ship.

**State B — Some DEGRADED, no FAIL (likely):** the brain works but has rough edges. Report goes to the user with prioritised fixes; user decides which to land before the demo.

**State C — One or more FAIL (likely):** at least one of the known failure modes is back. The report will name the gate + offer a fix recommendation. Do NOT silently patch production in this PR — open a follow-up PR per-fix, citing the ADR (e.g. "ADR-0049 C1 keystone fix regressed, see G1 failure detail").

---

## Things to NOT do

- Do not modify any extraction-pipeline code in this PR. If a gate fails, document the fix and let the user open a separate PR.
- Do not skip gates because they "look like they'll pass" — run them all.
- Do not change the canonical 5 questions. Pin them. The whole point is that the same 5 questions get asked every time, so we can detect regression across runs.
- Do not let the suite run-time exceed 30 minutes. If extraction is too slow for the full run, switch to the 5-endpoint subset (DEMO_ENDPOINTS list) and document the trade-off.

---

## PR description (use this as the template)

```
test(demo): ADR-0054 demo verification gate suite

Implements the 10 demo verification gates from ADR-0054. Runnable via
`make -f Makefile.demo verify-demo`. Produces demo-verification-report.md.

Acceptance: each gate has its own runner under tests/demo/gates/; the
report renders correctly across PASS / DEGRADED / FAIL outcomes; the
suite completes in < 30 minutes on the canonical fixture.

First run results: [paste the report's overall verdict + gate table]

If any gate FAILED, follow-up PRs are listed below by gate ID.
```

---

## After the agent is done

The user (a human) does one thing: read `demo-verification-report.md`.

- Green report → book the investor meeting.
- Yellow report → land the prioritised fixes, re-run verify-demo, then book.
- Red report → don't demo yet; open the listed follow-up PRs first.

This is the single source of truth for "is the brain demo-ready". No more guesswork before a meeting.
