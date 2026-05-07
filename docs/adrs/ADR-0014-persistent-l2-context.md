# ADR-0014: Persistent L2 shared context across pipeline runs

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 1 day
**Depends on:** ADR-0012 (writes go through BrainStore; .brain/ directory exists)
**Unblocks:** —

---

## Context

`pipeline/context_hierarchy.py::L2SharedContext` accumulates domain glossary, service registry, architecture patterns, cross-cutting concerns, field semantics, and the entity catalog as the run progresses. The 5th file's extraction sees what the first 4 found.

But L2 is in-memory only. At the end of `run_pipeline()` it is discarded. Every new run on the same repo starts with empty L2 and must rediscover the same vocabulary, service classifications, and architecture patterns.

This is a quick, cheap win. The harness's "Main Memory" tier (graph store) is paged in selectively for high-uncertainty nodes; L2 should be paged in *always* on the same-repo same-branch path, since it's tiny (≤ 2KB rendered).

## Decision

Serialise `L2SharedContext` to `.brain/.l2-cache/{branch}.json` at the end of every successful run; reload it at the start of the next run for the same `(repo_path, branch)` pair. Cap entity_catalog at 30 (already done in-memory) so the file stays small.

## Implementation

### File to edit

`company-brain-ai/src/companybrain/pipeline/shared_context_accumulator.py` — add `L2Persistence` helper. Keep `SharedContextAccumulator` unchanged.

### File to create

None — co-locate the helper in `shared_context_accumulator.py` to keep the L2 surface in one place.

### Code skeleton

Append to `shared_context_accumulator.py`:

```python
import json
import re
from pathlib import Path

from companybrain.pipeline.context_hierarchy import L2SharedContext


_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class L2Persistence:
    """
    Serialise / deserialise L2SharedContext to .brain/.l2-cache/{branch}.json.

    Used by the orchestrator at:
      - run start  → load() to warm L2 from prior runs
      - run end    → save() so the next run can warm

    The file is git-trackable but is conventionally gitignored under .brain/
    (commit only the canonical entity JSONs; the L2 cache is per-engineer).
    Engineers who want shared L2 commit the file explicitly.
    """

    @staticmethod
    def cache_path(repo_path: str | Path, branch: str = "main") -> Path:
        safe_branch = _BRANCH_SAFE.sub("_", branch)
        return Path(repo_path) / ".brain" / ".l2-cache" / f"{safe_branch}.json"

    @staticmethod
    def save(l2: L2SharedContext, repo_path: str | Path, branch: str = "main") -> None:
        path = L2Persistence.cache_path(repo_path, branch)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "domain_glossary":  l2.domain_glossary,
            "service_registry": l2.service_registry,
            "pattern_library":  l2.pattern_library,
            "cross_cutting":    l2.cross_cutting,
            "field_semantics":  l2.field_semantics,
            # entity_catalog is already capped at 30 in the accumulator;
            # serialise as-is.
            "entity_catalog":   l2.entity_catalog,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        log.info("L2 cache saved",
                 path=str(path),
                 entries=sum(len(v) if hasattr(v, "__len__") else 0
                             for v in payload.values() if v != 1))

    @staticmethod
    def load(repo_path: str | Path, branch: str = "main") -> L2SharedContext:
        path = L2Persistence.cache_path(repo_path, branch)
        if not path.exists():
            return L2SharedContext()
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            log.warning("L2 cache corrupt — starting fresh",
                        path=str(path), error=str(exc))
            return L2SharedContext()
        if data.get("version") != 1:
            log.warning("L2 cache version mismatch — starting fresh",
                        path=str(path), version=data.get("version"))
            return L2SharedContext()
        l2 = L2SharedContext(
            domain_glossary  = data.get("domain_glossary", {}),
            service_registry = data.get("service_registry", {}),
            pattern_library  = data.get("pattern_library", []),
            cross_cutting    = data.get("cross_cutting", []),
            field_semantics  = data.get("field_semantics", {}),
            entity_catalog   = data.get("entity_catalog", []),
        )
        log.info("L2 cache loaded", path=str(path), summary=l2.compact_summary())
        return l2
```

### Edits to `pipeline/orchestrator.py`

Two surgical changes:

1. **At the top of `run_pipeline()`**, before constructing `extractor / cm_agent / accumulator / l2`, replace the bare `l2 = L2SharedContext()` line:

```python
from companybrain.pipeline.shared_context_accumulator import L2Persistence

repo_path_for_l2 = (request.repos[0].local_path or "") if request.repos else ""
branch_for_l2    = request.branch or "main"

l2 = L2Persistence.load(repo_path_for_l2, branch_for_l2) if repo_path_for_l2 else L2SharedContext()
if not l2.is_empty():
    await progress("0.6", "🧠",
                   f"L2 warmed from cache: {l2.compact_summary()}",
                   summary=l2.compact_summary())
```

2. **Just before returning `PipelineResult`** (after the success path completes, before `_checkpoint_clear`), persist:

```python
if repo_path_for_l2:
    try:
        L2Persistence.save(l2, repo_path_for_l2, branch_for_l2)
    except Exception as exc:
        log.warning("L2 cache save failed (non-fatal)", error=str(exc))
```

3. **Failure-path safety.** In the `except Exception as e:` branch, do *not* save L2 — partial state should not poison the next run.

### `.gitignore` recommendation

Add to `.gitignore` documentation (not enforced by the code) that engineers who want per-engineer L2 caches should add:

```
.brain/.l2-cache/
```

…to their repo's `.gitignore`. Engineers who want shared L2 (recommended for stable mainline-branch runs) commit it explicitly.

## Test plan

`tests/unit/pipeline/test_l2_persistence.py`:

```python
import json
from pathlib import Path

from companybrain.pipeline.context_hierarchy import L2SharedContext
from companybrain.pipeline.shared_context_accumulator import L2Persistence


def test_save_and_load_round_trip(tmp_path: Path):
    l2 = L2SharedContext(
        domain_glossary={"NIQ": "Network IQ — competitiveness scoring"},
        service_registry={"PaymentService": {"role": "service", "file": "src/p.ts"}},
        pattern_library=["SAGA in PaymentService"],
        field_semantics={"niq_score": "0-100 competitiveness rank"},
    )
    L2Persistence.save(l2, tmp_path, "main")
    out = L2Persistence.load(tmp_path, "main")
    assert out.domain_glossary == l2.domain_glossary
    assert out.service_registry == l2.service_registry
    assert out.pattern_library == l2.pattern_library


def test_load_missing_file_returns_empty(tmp_path):
    out = L2Persistence.load(tmp_path, "main")
    assert out.is_empty()


def test_load_corrupt_file_returns_empty(tmp_path):
    p = L2Persistence.cache_path(tmp_path, "main")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json")
    out = L2Persistence.load(tmp_path, "main")
    assert out.is_empty()


def test_branch_segregation(tmp_path):
    l2_main = L2SharedContext(domain_glossary={"X": "main"})
    l2_feat = L2SharedContext(domain_glossary={"X": "feature"})
    L2Persistence.save(l2_main, tmp_path, "main")
    L2Persistence.save(l2_feat, tmp_path, "feature/foo")
    assert L2Persistence.load(tmp_path, "main").domain_glossary["X"] == "main"
    assert L2Persistence.load(tmp_path, "feature/foo").domain_glossary["X"] == "feature"


def test_unsafe_branch_chars_are_sanitised(tmp_path):
    l2 = L2SharedContext(domain_glossary={"X": "Y"})
    L2Persistence.save(l2, tmp_path, "feat/with/slashes")
    expected = tmp_path / ".brain" / ".l2-cache" / "feat_with_slashes.json"
    assert expected.exists()
```

### Integration assertion

In the existing pipeline integration test (or a new one):
- Run pipeline once on pilot.
- Run pipeline again immediately.
- Assert that on the second run, the orchestrator log line `"L2 warmed from cache:"` appears with at least one non-empty category.

## Acceptance criteria

- [ ] `L2Persistence` class exists in `pipeline/shared_context_accumulator.py`.
- [ ] All four unit tests pass.
- [ ] Orchestrator loads L2 from `.brain/.l2-cache/{branch}.json` at run start.
- [ ] Orchestrator saves L2 at end of successful run.
- [ ] Orchestrator does NOT save L2 on failed run.
- [ ] Branch name is filesystem-sanitised (slashes etc.).
- [ ] Cache miss / corrupt file → start with empty L2, no exception raised.
- [ ] Integration test confirms second-run L2 warm-load.

## Verification commands

```bash
# 1. Wipe any existing L2 cache
rm -rf pilot/.brain/.l2-cache/

# 2. First run — L2 starts empty, builds up
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET

# 3. Inspect the cache
cat pilot/.brain/.l2-cache/main.json | jq 'keys'

# 4. Second run — should log "L2 warmed from cache"
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET 2>&1 | grep "L2 warmed"

# 5. Different branch — should be empty
git -C pilot switch -c feature/test
make ai-test-pipeline REPO=./pilot ENDPOINT=/api/users METHOD=GET 2>&1 | grep "L2 cache"
```

## Rollback

```bash
git revert <commit-sha>
rm -rf pilot/.brain/.l2-cache/
```

Nothing else to undo. The orchestrator falls back to the in-memory-only behaviour.

## Out of scope

- **Cross-repo L2 sharing.** L2 is per-(repo, branch). Stage 2 introduces `platform-brain/.l2-cache/` for cross-repo glossary sharing.
- **L2 versioning beyond v=1.** Schema migrations within L2 — defer until L2 expands.
- **Concurrency safety.** If two pipelines run on the same `(repo, branch)` simultaneously, the last writer wins on save. The orchestrator's checkpoint key already prevents accidental concurrent runs of the *same endpoint*; cross-endpoint same-repo concurrency is rare in dev and acceptable for Stage 1.
