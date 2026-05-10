# Implementation Prompt — ADR-0048 (two-agent batched extraction)

**You are landing ADR-0048 in this repo. Read this prompt fully before writing any code. ADR-0048 is the SECOND of three coordinated ADRs (0049 → 0048 → 0050). It replaces the navigator's 26-turn ReAct loop and per-method chunk extractor with two specialised agents (Specialist + Context).**

---

## Pre-flight

1. Read `docs/adrs/ADR-0048-two-agent-batched-extraction.md` start-to-finish.
2. Read `docs/adrs/ADR-0050-big-repo-safe-adaptive-extraction.md` §"Sequencing & Merge Plan" for the file-ownership table.
3. **Prerequisite check:** ADR-0049 should be merged on `main`. If not:
   ```bash
   git log --oneline main | head -10 | grep -q "ADR-0049" || echo "WARN: ADR-0049 not yet merged"
   ```
   If 0049 is missing, your code still works — caching is transparent and you'll just see `cache_read=0` in logs. Don't block on it.
4. `git checkout -b feature/adr-0048-two-agent-extraction` from `main` (rebased onto 0049 if available).

---

## File ownership for THIS PR (do not touch anything else)

You exclusively own and may modify:

```
src/companybrain/agents/specialist_agent.py          # NEW
src/companybrain/agents/context_agent.py             # NEW
src/companybrain/pipeline/chunk_extractor.py         # DEPRECATE (kept behind flag for fallback)
tests/acceptance/test_two_agent_extraction.py        # NEW
tests/unit/test_specialist_agent.py                  # NEW
tests/unit/test_context_agent.py                     # NEW
```

You MAY add **append-only** code (new functions, new env-flag-gated branches) to:

```
src/companybrain/collectors/code_tracer.py           # _trace_java now calls SpecialistAgent
src/companybrain/pipeline/orchestrator.py            # wires SpecialistAgent + ContextAgent
src/companybrain/pipeline/entity_extractor.py        # add _entities_from_dto_plan helper
src/companybrain/config.py                           # ContextAgent batch tunables
```

Do NOT modify any other file.

`KnowledgeNavigatorAgent` stays in the codebase — it's the
`BRAIN_USE_LEGACY_NAVIGATOR=true` fallback. Don't delete it.

---

## Implementation steps

### Step 1 — `SpecialistAgent` (single-call planner)

Create `src/companybrain/agents/specialist_agent.py`:

```python
"""SpecialistAgent — single LLM call, no tools, no ReAct loop.

Replaces KnowledgeNavigatorAgent's 25-turn loop with a single
strategic planning call. Receives the full entry handler file +
a filtered repo manifest, returns a structured extraction plan.
"""
from dataclasses import dataclass
from pathlib import Path

from companybrain.providers import get_provider, ChatMessage, TaskRole
from companybrain.config import settings


_SYSTEM_PROMPT = """You are a code-aware extraction planner.

Given:
- An entry handler file (full content)
- A filtered manifest of candidate files in the repo

Return a JSON plan that lists which files + which methods within each
should be extracted by the downstream ContextAgent. Skip pure DTOs,
value objects, request/response shells — those are handled
structurally without an LLM call.

Output schema (compact, single-line JSON):
{
  "plan": [
    {"file": "...", "role": "controller|service|repository|model",
     "methods": ["m1", "m2"], "relevance": 0.0-1.0,
     "reason": "why this file matters for the endpoint"}
  ],
  "skip_dto": ["DtoName1", "DtoName2"]
}

Roles MUST be one of: controller, service, repository, model, util, test.
Relevance 1.0 = directly on the call chain; 0.5 = tangential helper.
methods[] must be EXACT method names from the file.
Skip files entirely if their relevance < 0.3.
"""


@dataclass
class ExtractionPlan:
    plan: list[dict]      # [{file, role, methods, relevance, reason}, ...]
    skip_dto: list[str]   # DTO class names to fast-path


class SpecialistAgent:
    def __init__(self):
        self._provider = get_provider()

    async def plan(
        self,
        endpoint: str,
        http_method: str,
        entry_handler_path: str,
        candidate_files: list[tuple[str, str, int]],   # (path, role, size_kb)
    ) -> ExtractionPlan:
        entry_content = Path(entry_handler_path).read_text(errors="ignore")
        manifest_md = self._build_manifest_table(candidate_files)
        user = self._build_user_prompt(
            endpoint, http_method, entry_handler_path,
            entry_content, manifest_md,
        )
        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user),
            ],
            role=TaskRole.BALANCED,
            max_tokens=2_000,
        )
        return self._parse(raw)

    def _build_manifest_table(self, files):
        """Markdown table — LLM parses this natively, ~40% smaller than JSON."""
        rows = ["| file | role | size_kb |", "|---|---|---|"]
        for path, role, size_kb in files:
            rows.append(f"| {path} | {role} | {size_kb} |")
        return "\n".join(rows)

    def _build_user_prompt(self, endpoint, method, handler_path,
                           handler_content, manifest_md):
        return (
            f"<endpoint>{method} {endpoint}</endpoint>\n\n"
            f'<entry_handler path="{handler_path}">\n'
            f'{handler_content}\n'
            f'</entry_handler>\n\n'
            f'<repo_manifest>\n'
            f'{manifest_md}\n'
            f'</repo_manifest>'
        )

    def _parse(self, raw: str) -> ExtractionPlan:
        import json
        data = json.loads(raw)
        return ExtractionPlan(
            plan=data.get("plan", []),
            skip_dto=data.get("skip_dto", []),
        )
```

### Step 2 — `ContextAgent` (batched extractor)

Create `src/companybrain/agents/context_agent.py`:

```python
"""ContextAgent — batched per-method extraction.

Receives a batch of method bodies (typically 8 same-class siblings),
returns entities + edges + business_context for the whole batch in
one LLM call.
"""
from dataclasses import dataclass, field
from typing import Optional

from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.providers import get_provider, ChatMessage, TaskRole


_SYSTEM_PROMPT = """You are a code-context extractor.

For each method in the input, emit:
  - one entity (Method) with code_snippet + query_text + business_context
  - all edges originating from that method (CALLS, READS_COLUMN, USES, etc.)

Edge types (closed taxonomy, 50 total):
  CALLS, INVOKES, AWAITS, CALLS_ENDPOINT, DELEGATES_TO,
  USES, DEPENDS_ON, INSTANTIATES, CONTAINS,
  EXTENDS, IMPLEMENTS, OVERRIDES,
  READS_COLUMN, WRITES_COLUMN, READS_FIELD, WRITES_FIELD,
  RETURNS, ACCEPTS_PARAM, TRANSFORMS, SERIALIZES_TO,
  PERSISTS_TO, INDEXED_BY, CACHED_BY,
  RENDERS, RENDERS_FIELD, BINDS_TO, ROUTED_BY,
  PUBLISHES_TO, SUBSCRIBES_TO, LISTENS_TO, SCHEDULED_BY,
  AUTHORIZED_BY, PROTECTED_BY, AUDITED_BY,
  VALIDATES, ENFORCES, SANITIZES,
  THROWS, CATCHES, WRAPS_EXCEPTION, HANDLES_ERROR,
  TESTED_BY, ANNOTATES, MOCKS, FIXTURE_FOR, DOCUMENTED_BY.

BusinessContext fields (21):
  purpose, change_risk (LOW|MEDIUM|HIGH), data_sensitivity,
  invariants[], side_effects[], failure_modes[], owners,
  ... [reproduce the full schema from existing chunk_extractor.py]

Return a single line of compact JSON, schema:
{
  "results": [
    {"qname": "...",
     "entity": {...ExtractedEntity fields...},
     "edges": [{"type":"CALLS","target":"...","confidence":0.9,"evidence":"..."}],
     "business_context": {...21 fields...}}
  ]
}
"""


@dataclass
class ContextAgentResult:
    entity: ExtractedEntity
    edges: list[ExtractedRelationship]
    business_context: dict


class ContextAgent:
    def __init__(self):
        self._provider = get_provider()

    async def extract_batch(
        self,
        chunks: list,                # list[MethodChunk]
        max_tokens: int = 4_000,
    ) -> list[ContextAgentResult]:
        if not chunks:
            return []
        user = self._build_user_xml(chunks)
        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user),
            ],
            role=TaskRole.BALANCED,
            max_tokens=max_tokens,
        )
        return self._parse(raw, chunks)

    def _build_user_xml(self, chunks):
        # Group by parent class so the class header is shared once
        first = chunks[0]
        parts = [
            f'<class_header file="{first.file_path}" '
            f'class="{first.qname.split(".")[0]}">',
            first.header_context,
            '</class_header>',
            '',
            f'<imports>',
            first.import_context,
            '</imports>',
            '',
        ]
        for c in chunks:
            parts.extend([
                f'<method qname="{c.qname}" lang="{c.language}">',
                c.body,
                '</method>',
                '',
            ])
        return "\n".join(parts)

    def _parse(self, raw, chunks):
        # Existing parsing logic — see chunk_extractor.py for the
        # ExtractedEntity / ExtractedRelationship construction pattern.
        # Defensively coerce dicts → dataclasses (we have three layers
        # of defensive coercion shipped already; reuse them).
        import json
        data = json.loads(raw)
        return [_to_result(item) for item in data.get("results", [])]
```

### Step 3 — Refactor `code_tracer._trace_java`

In `src/companybrain/collectors/code_tracer.py`, find `_trace_java`
and add a feature-flag fork at the top:

```python
async def _trace_java(self, repo_path, repo_name, endpoint, method="GET"):
    if settings.use_legacy_navigator:
        return await self._trace_java_legacy(...)   # existing code, renamed
    return await self._trace_java_specialist(repo_path, repo_name, endpoint, method)
```

Implement `_trace_java_specialist`:

```python
async def _trace_java_specialist(self, repo_path, repo_name, endpoint, method):
    # 1. Find entry handler (existing find_entry_handler call)
    entry = find_entry_handler(endpoint, method, str(repo_path))
    if not entry:
        # Re-use the new NoMatchingEndpointError logic ADR shipped earlier
        raise NoMatchingEndpointError(endpoint, method, discover_routes(repo_path))

    # 2. Build candidate manifest via hybrid search (existing infrastructure)
    candidates = await _get_hybrid_searcher().search(
        query=endpoint, repo_name=repo_name, repo_path=repo_path, top_k=20,
    )
    candidate_tuples = [
        (c.path, _infer_role(Path(c.path).stem), int(Path(c.path).stat().st_size / 1024))
        for c in candidates
    ]

    # 3. SpecialistAgent — ONE LLM call
    plan = await SpecialistAgent().plan(
        endpoint=endpoint, http_method=method,
        entry_handler_path=entry["file"],
        candidate_files=candidate_tuples,
    )

    # 4. Convert plan → CodeUnit list
    units = []
    for entry_plan in plan.plan:
        units.append(CodeUnit(
            file_path=str(Path(entry_plan["file"]).resolve()),  # absolute (per ADR-0045/0048)
            repo_name=repo_name,
            role=entry_plan["role"],
            language="java",
            content="",   # ADR-0045: chunker reads from disk
            class_name=Path(entry_plan["file"]).stem,
            imports=[],
        ))

    # 5. Stash skip_dto on the FocalContext for entity_extractor
    self._dto_skip_list = plan.skip_dto
    return units
```

### Step 4 — Wire `ContextAgent` into the chunked path

In `src/companybrain/pipeline/orchestrator.py`, find the chunked
extraction block (the `if _use_chunk_queue:` section). Swap the
chunk_extractor call for ContextAgent. The simplest path:

```python
# OLD: drain_queue spawns chunk_extractor workers
# NEW: drain_queue's worker now uses ContextAgent.extract_batch
```

Modify `pipeline/worker.py` (which `drain_queue` uses): replace its
internal LLM call with `ContextAgent().extract_batch(chunks_from_db)`.

### Step 5 — DTO fast-path

In `src/companybrain/pipeline/entity_extractor.py`, add:

```python
def _entities_from_dto_plan(
    skip_dto_names: list[str],
    repo_path: str,
    repo_name: str,
) -> list[ExtractedEntity]:
    """Emit Class entities for DTOs the SpecialistAgent flagged as
    structural-only. No LLM call. Uses the existing trivial-POJO
    detection logic at module level."""
    out = []
    for name in skip_dto_names:
        path = _find_class_file(repo_path, name)   # existing helper
        if not path:
            continue
        out.append(ExtractedEntity(
            entity_type="Class",
            name=name,
            file=str(path),
            repo=repo_name,
            signature=_extract_class_signature(path),
            confidence=0.9,
        ))
    return out
```

Call it from the orchestrator after the chunked extraction completes.

### Step 6 — Config tunables

In `src/companybrain/config.py`:

```python
context_agent_batch_size: int = 8        # methods per ContextAgent call
use_legacy_navigator:    bool = False    # BRAIN_USE_LEGACY_NAVIGATOR
```

### Step 7 — Acceptance test

`tests/acceptance/test_two_agent_extraction.py`:

```python
async def test_specialist_agent_picks_real_files():
    """SpecialistAgent receives the real CompetitivenessController +
    candidate manifest; must include CompetitivenessPlanRepository in
    its plan (that's where the lob extraction lives)."""
    plan = await SpecialistAgent().plan(
        endpoint="/competitiveness/summary/competitors/payer",
        http_method="POST",
        entry_handler_path="fixtures/.../CompetitivenessController.java",
        candidate_files=[...],
    )
    files = {p["file"] for p in plan.plan}
    assert any("CompetitivenessPlanRepository" in f for f in files)


async def test_context_agent_batches_and_returns_all():
    """8 method chunks in one batch must return 8 results."""
    chunks = [_make_chunk(f"method{i}") for i in range(8)]
    results = await ContextAgent().extract_batch(chunks)
    assert len(results) == 8
    assert all(r.entity is not None for r in results)


async def test_total_llm_calls_under_15_on_real_repo():
    """End-to-end count: SpecialistAgent (1) + ContextAgent batches (≤8)
    + handler finder (1) + reachability filter (0) ≤ 10 calls."""
    result = await run_pipeline(
        endpoint="/competitiveness/summary/competitors/payer",
        method="POST",
        repo="fixtures/network-iq-backend-java-snapshot",
    )
    assert result.telemetry["total_llm_calls"] < 15
```

---

## Verification

```bash
.venv/bin/pytest tests/unit/test_specialist_agent.py tests/unit/test_context_agent.py -v
.venv/bin/pytest tests/acceptance/test_two_agent_extraction.py -v
```

Then run the canonical demo end-to-end:

```bash
make -f Makefile.demo wipe
make -f Makefile.demo run-cli ENDPOINT=/competitiveness/summary/competitors/payer METHOD=POST
```

The job summary should show `total_llm_calls < 15` and the brain
should contain `CompetitivenessPlanRepository.getPayerCompetitors`
with non-empty `query_text`.

---

## PR description

```
feat(pipeline): two-agent batched extraction (ADR-0048)

Replaces KnowledgeNavigatorAgent's 25-turn ReAct loop + per-method
chunk_extractor with:
- SpecialistAgent: 1 LLM call, no tools, plans the extraction
- ContextAgent: batched (8 methods/call) extraction with all edges + 
  business_context

Cuts pipeline cost ~85% ($0.30 → $0.04) and wall time ~95% (5min → 30s).
Legacy KnowledgeNavigatorAgent kept behind BRAIN_USE_LEGACY_NAVIGATOR=true
for fallback.

Coordinated with ADR-0049 (caching) and ADR-0050 (big-repo recovery).
File-ownership table in ADR-0050 §"Sequencing & Merge Plan".
```
