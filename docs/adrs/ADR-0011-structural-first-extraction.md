# ADR-0011: Structural-first extraction ordering

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 1 day
**Supersedes:** Portions of ADR-0008 (integration bridge timing)
**Depends on:** —
**Unblocks:** ADR-0012, ADR-0014

---

## Context

The Python orchestrator (`company-brain-ai/src/companybrain/pipeline/orchestrator.py`) runs the LLM passes (Stage 1 entity extraction, 1.5 intent synthesis, 2 relationships, 3 context, 4 gap detection) and *then* calls `cb-api:8090/extract` (the Bun extractor-worker registry: Git, CoreTs, Next, Prisma, OpenAPI, SQL, JPA, SQLAlchemy, DriftDetector). The structural pass writes Neo4j AFTER the LLM has already paid for entity extraction on every code unit.

ADR-0010 §3 explicitly mandates the opposite ordering — frugality hierarchy: tree-sitter first (zero cost), regex second, heuristics third, embeddings fourth, small LLM fifth, large LLM last. The current ordering inverts this.

Effects today:
- Every code unit visits Stage 1 LLM extraction even when its structural fingerprint hasn't changed since the last run.
- Hash-based freshness exists (`JavaGraphClient.check_freshness()`) at the file content level, but doesn't account for "the file's structural exports are identical even though formatting changed."
- Cb-api's structural data lands in Neo4j too late for the LLM passes to use it as context.

## Decision

Move the cb-api `/extract` call from post-Stage-5 to **Stage 0.5**, immediately after code tracing (0a) and before entity extraction (1). Use the resulting Neo4j structural fingerprint as the *primary* freshness key for Stage 1: if a code unit's structural hash matches what Neo4j already has, skip the LLM call entirely and reuse the existing `function_node` / `class` / `module` projection.

LLM passes remain in place but only run on code units whose structural fingerprint is new or changed. Stages 1.5 / 3 / 3.5 / 4 also become incremental — they only fire on the changed-set.

This is one straightforward refactor with no schema changes.

## Implementation

### Files to edit

- `company-brain-ai/src/companybrain/pipeline/orchestrator.py` — reorder stages.
- `company-brain-ai/src/companybrain/graph/java_client.py` — add a Neo4j read method (or the cb-api equivalent) for structural fingerprints.
- `company-brain-ai/src/companybrain/pipeline/concurrency.py` — no changes; structural pre-pass is a single HTTP call.
- `apps/api/src/index.ts` — add a `GET /fingerprints?repo=<path>&commit=<sha>` endpoint that returns `{ file_path: structural_hash }` for every NodeInfo in Neo4j.

### Files to create

- `company-brain-ai/src/companybrain/pipeline/structural_prepass.py` — orchestrates the cb-api call and freshness diffing.
- `company-brain-ai/src/companybrain/pipeline/types.py` — shared dataclasses (`StructuralFingerprint`, `PrePassResult`).

### Code skeletons

`pipeline/types.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class StructuralFingerprint:
    """One file's structural shape, as known to Neo4j.

    structural_hash is hash(sorted(qualified_names)) — a fingerprint that is
    stable across formatting changes but unstable across renames or signature
    changes.
    """
    file_path: str          # relative to repo root
    structural_hash: str    # sha256 of sorted (kind, qname, signature) tuples
    function_count: int
    class_count: int
    last_indexed_commit: str

@dataclass
class PrePassResult:
    """What the structural pre-pass found.

    fresh_units    — code units whose structural hash matches Neo4j → skip Stage 1.
    dirty_units    — code units that need LLM extraction in Stage 1.
    cb_api_status  — "ok" | "skipped" | "failed:<reason>"
    """
    fresh_units: list = field(default_factory=list)
    dirty_units: list = field(default_factory=list)
    cb_api_status: str = "ok"
```

`pipeline/structural_prepass.py`:
```python
"""
Stage 0.5: Structural pre-pass.

Calls cb-api /extract to populate Neo4j structural nodes, then queries
Neo4j (via cb-api /fingerprints) for the structural hash of every traced
file and computes the fresh-vs-dirty split for downstream LLM stages.

Failure mode: if cb-api is unreachable or /extract fails, every traced
unit is marked dirty (full LLM path) and a non-fatal warning logs.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import httpx
import structlog

from companybrain.collectors.code_tracer import CodeUnit, FocalContext
from companybrain.pipeline.types import StructuralFingerprint, PrePassResult

CB_API_URL = os.getenv("CB_API_URL", "http://cb-api:8090")
CB_API_TIMEOUT = 300.0  # seconds — large repos take time
log = structlog.get_logger(__name__)


async def run_structural_prepass(
    *,
    repo_path: str,
    commit_sha: str,
    workspace_id: str,
    focal_context: FocalContext,
) -> PrePassResult:
    """
    1. POST cb-api /extract  (runs Bun extractor-worker registry)
    2. GET  cb-api /fingerprints  (returns Neo4j-side structural hashes)
    3. For each CodeUnit: compare local structural hash to Neo4j's;
       fresh if equal, dirty otherwise.
    """
    result = PrePassResult()

    # Step 1: trigger structural extraction
    try:
        async with httpx.AsyncClient(timeout=CB_API_TIMEOUT) as client:
            extract_resp = await client.post(
                f"{CB_API_URL}/extract",
                json={"repoPath": repo_path, "scope": workspace_id, "commitSha": commit_sha},
            )
            extract_resp.raise_for_status()
    except Exception as exc:
        log.warning("Structural pre-pass: cb-api /extract failed (non-fatal)", error=str(exc))
        result.cb_api_status = f"failed:{exc}"
        result.dirty_units = list(focal_context.code_units)
        return result

    # Step 2: fetch fingerprints
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            fp_resp = await client.get(
                f"{CB_API_URL}/fingerprints",
                params={"scope": workspace_id, "commit": commit_sha},
            )
            fp_resp.raise_for_status()
            fingerprints: dict[str, StructuralFingerprint] = {
                row["file_path"]: StructuralFingerprint(
                    file_path=row["file_path"],
                    structural_hash=row["structural_hash"],
                    function_count=row["function_count"],
                    class_count=row["class_count"],
                    last_indexed_commit=row.get("last_indexed_commit", ""),
                )
                for row in fp_resp.json()["fingerprints"]
            }
    except Exception as exc:
        log.warning("Structural pre-pass: /fingerprints failed (non-fatal)", error=str(exc))
        result.cb_api_status = f"failed:{exc}"
        result.dirty_units = list(focal_context.code_units)
        return result

    # Step 3: split fresh vs dirty
    for unit in focal_context.code_units:
        rel_path = _to_repo_relative(unit.file_path, repo_path)
        local_hash = _local_structural_hash(unit.content)
        neo4j_fp = fingerprints.get(rel_path)
        if neo4j_fp and neo4j_fp.structural_hash == local_hash:
            result.fresh_units.append(unit)
        else:
            result.dirty_units.append(unit)

    log.info(
        "Structural pre-pass complete",
        total=len(focal_context.code_units),
        fresh=len(result.fresh_units),
        dirty=len(result.dirty_units),
        cb_api=result.cb_api_status,
    )
    return result


def _local_structural_hash(content: str) -> str:
    """
    Quick local fingerprint for comparison against Neo4j's stored hash.
    Implementation: tree-sitter parse → sorted (kind, qname, signature)
    tuples → sha256. For stage 1 a simpler heuristic (sorted top-level
    `def`/`class` names) is acceptable — refine later in a follow-up ADR.
    """
    # Minimal heuristic — replace with structural/parser.py call in a follow-up
    import re
    names = sorted(set(re.findall(r"^\s*(?:def|class|function|interface|public\s+\w+\s+)\s+(\w+)", content, re.MULTILINE)))
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def _to_repo_relative(absolute_path: str, repo_path: str) -> str:
    try:
        return str(Path(absolute_path).relative_to(repo_path))
    except ValueError:
        return absolute_path  # already relative
```

### Edits to `pipeline/orchestrator.py`

Find the existing freshness section (`# ── Pre-flight: Freshness check`) and the existing `_trigger_structural_extraction()` call near the end of `run_pipeline()`.

1. **Insert Stage 0.5** between code tracing (`0a`) and Stage 0c freshness check:

```python
# ── Stage 0.5: Structural pre-pass (NEW — ADR-0011) ───────────────────
from companybrain.pipeline.structural_prepass import run_structural_prepass

repo_path_for_pre = (request.repos[0].local_path or request.repos[0].url) if request.repos else ""
commit_for_pre = _resolve_commit_sha(repo_path_for_pre)  # see helper below

await progress("0.5", "🪚", "Structural pre-pass — cb-api → Neo4j → fingerprints")
prepass = await run_structural_prepass(
    repo_path=repo_path_for_pre,
    commit_sha=commit_for_pre,
    workspace_id=request.workspace_id,
    focal_context=focal_context,
)

# Reuse fresh units (skip LLM); LLM only runs on dirty units
fresh_units = prepass.fresh_units
dirty_units = prepass.dirty_units

stages_summary.append({
    "stage": "0.5",
    "label": "Structural Pre-pass",
    "fresh": len(fresh_units),
    "dirty": len(dirty_units),
    "cb_api": prepass.cb_api_status,
})
await progress(
    "0.5", "✅",
    f"{len(fresh_units)} structural-fresh, {len(dirty_units)} need LLM",
    fresh=len(fresh_units), dirty=len(dirty_units),
)
```

2. **Replace** the existing Stage 0c freshness logic — its `dirty_units` now comes from the pre-pass, so the `JavaGraphClient.check_freshness()` call can be retired *or* kept as a secondary check that further reduces the dirty set if Postgres has a hit but Neo4j doesn't (rare but possible during Postgres↔Neo4j divergence — ADR-0013 fixes the underlying cause).

3. **Remove** the post-Stage-5 `_trigger_structural_extraction()` call (lines ~784–797). The structural pass already happened at Stage 0.5; calling it again is wasted work.

4. **Add helper** at the bottom of `orchestrator.py`:

```python
import subprocess

def _resolve_commit_sha(repo_path: str) -> str:
    """Returns the current HEAD SHA of the repo, or 'HEAD' if not a git repo."""
    if not repo_path:
        return "HEAD"
    try:
        return subprocess.check_output(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return "HEAD"
```

### Edits to `apps/api/src/index.ts` (new endpoint)

Add a `GET /fingerprints?scope=<workspace_id>&commit=<sha>` endpoint:

```typescript
// after the existing POST /extract handler:
if (req.method === "GET" && url.pathname === "/fingerprints") {
  const scope  = url.searchParams.get("scope")  ?? "";
  const commit = url.searchParams.get("commit") ?? "";
  if (!scope) return new Response("scope required", { status: 400 });

  const fingerprints = await graph.runRead(
    `MATCH (f:File { scope: $scope })
     OPTIONAL MATCH (f)-[:CONTAINS]->(n)
     WITH f, collect({ kind: labels(n)[0], qname: n.qualified_name, sig: coalesce(n.signature, '') }) AS members
     WITH f,
          apoc.util.sha256([m IN members WHERE m.qname IS NOT NULL |
                              m.kind + '|' + m.qname + '|' + m.sig]) AS structural_hash,
          size([m IN members WHERE m.kind = 'Function']) AS function_count,
          size([m IN members WHERE m.kind = 'Class'])    AS class_count
     RETURN f.path AS file_path, structural_hash, function_count, class_count,
            f.last_indexed_commit AS last_indexed_commit`,
    { scope }
  );

  return addCors(new Response(JSON.stringify({ fingerprints }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}
```

(If APOC isn't available, replace `apoc.util.sha256(...)` with a JS-side hash after the rows return.)

## Test plan

### Unit tests (Python)

`company-brain-ai/tests/unit/pipeline/test_structural_prepass.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from companybrain.pipeline.structural_prepass import run_structural_prepass, _local_structural_hash
from companybrain.collectors.code_tracer import FocalContext, CodeUnit


@pytest.mark.asyncio
async def test_prepass_marks_unchanged_files_fresh():
    fc = FocalContext(code_units=[
        CodeUnit(file_path="src/Foo.java", repo_name="pilot", role="service",
                 class_name="Foo", content="public class Foo { void bar() {} }",
                 language="java"),
    ])
    fake_hash = _local_structural_hash(fc.code_units[0].content)

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(200, {}))), \
         patch("httpx.AsyncClient.get",  new=AsyncMock(return_value=_resp(200, {
             "fingerprints": [
                 {"file_path": "src/Foo.java", "structural_hash": fake_hash,
                  "function_count": 1, "class_count": 1}
             ]
         }))):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot", commit_sha="abc",
            workspace_id="ws", focal_context=fc,
        )
    assert len(result.fresh_units) == 1
    assert len(result.dirty_units) == 0
    assert result.cb_api_status == "ok"


@pytest.mark.asyncio
async def test_prepass_falls_back_to_dirty_when_cb_api_down():
    fc = FocalContext(code_units=[
        CodeUnit(file_path="src/Foo.java", repo_name="pilot", role="service",
                 class_name="Foo", content="public class Foo {}", language="java"),
    ])
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("ECONNREFUSED"))):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot", commit_sha="abc",
            workspace_id="ws", focal_context=fc,
        )
    assert len(result.dirty_units) == 1
    assert result.fresh_units == []
    assert result.cb_api_status.startswith("failed:")


def _resp(status, body):
    class R:
        status_code = status
        def raise_for_status(self): pass
        def json(self): return body
    return R()
```

### Integration test

`company-brain-ai/tests/integration/test_orchestrator_prepass.py`:
- Run the orchestrator twice in a row on the pilot repo without changing any code.
- Assert the second run's `stages_summary` has `0.5.fresh > 0` and Stage 1's `entities` count is reused, not regenerated.
- Assert total LLM call count from the second run (via `LLMCallRecord` summary) is **at most 20% of the first run's count**.

## Acceptance criteria

- [ ] `pipeline/structural_prepass.py` exists with `run_structural_prepass()` and `PrePassResult`.
- [ ] `pipeline/types.py` exists with `StructuralFingerprint` and `PrePassResult`.
- [ ] `apps/api/src/index.ts` exposes `GET /fingerprints` returning the documented JSON shape.
- [ ] Orchestrator inserts Stage 0.5 between 0a and 0c.
- [ ] Orchestrator removes the post-Stage-5 `_trigger_structural_extraction()` call.
- [ ] Both unit tests pass.
- [ ] Integration test: second run on unchanged repo cuts LLM call count by ≥80%.
- [ ] cb-api outage does NOT break the pipeline — units fall back to dirty path.
- [ ] No regression: a clean run on the pilot repo produces the same entity_count ± 5% as before this change.

## Verification commands

```bash
# 1. Run pipeline once on pilot repo
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET

# 2. Capture LLM call count from logs
grep "llm_call.completed" logs/ai-1.log | wc -l   # baseline

# 3. Run pipeline again with no code changes
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET

# 4. Capture second-run LLM call count
grep "llm_call.completed" logs/ai-2.log | wc -l   # expect ≤ 20% of baseline

# 5. Confirm Neo4j has Function nodes for pilot repo
cypher-shell -u neo4j -p password "MATCH (f:Function) WHERE f.scope='ws' RETURN count(f);"
# Expect: > 0 after first run, same count after second run.
```

## Rollback

```bash
git revert <commit-sha>
make ai-restart
```

The change is additive in shape (new files + reordered call) so revert is clean. No DB migration to undo.

## Out of scope

- **Function-level structural fingerprinting in Python.** `_local_structural_hash()` uses a regex heuristic. Replace with `companybrain/structural/parser.py` invocation in a follow-up ADR (call it ADR-0020).
- **Postgres ↔ Neo4j ID alignment.** ADR-0013 fixes the dual-ID-scheme problem properly.
- **Extracting `function_node` entities into the Postgres semantic graph.** Today only Neo4j has function-level nodes. Postgres alignment is ADR-0013 + a future ADR-0021 (function_node entity in Postgres).
