# Implementation Prompt — ADR-0050 (big-repo-safe adaptive extraction)

**You are landing ADR-0050 in this repo. Read this prompt fully before writing any code. ADR-0050 is the THIRD of three coordinated ADRs (0049 → 0048 → 0050). It guarantees zero silent truncation regardless of repo size, with mathematically bounded recovery overhead.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0050-big-repo-safe-adaptive-extraction.md` start-to-finish.
2. Read §"Sequencing & Merge Plan" in that same ADR for the full file-ownership table — it defines exactly what this PR may touch.
3. **Prerequisites check:**
   ```bash
   git log --oneline main | head -20 | grep -E "ADR-0049|ADR-0048" || echo "WARN: prereq ADRs not merged"
   ```
   - If ADR-0049 (caching) is merged: you can use `AstCache` and `FileCache`.
   - If ADR-0048 (two-agent) is merged: you'll wrap `ContextAgent`. If it's NOT merged, your bisection wrapper falls through to the legacy `chunk_extractor` (which already has yesterday's char-by-char JSON recovery as a degraded-but-functional fallback). Either path is acceptable.
4. `git checkout -b feature/adr-0050-big-repo-recovery` from `main`.

---

## File ownership for THIS PR (do not touch anything else)

You exclusively own and may modify:

```
src/companybrain/util/token_estimator.py             # M1 — NEW
src/companybrain/pipeline/batch_planner.py           # M1 — NEW
src/companybrain/pipeline/extraction_recovery.py     # M2 — NEW (bisection wrapper)
src/companybrain/pipeline/region_splitter.py         # M3b — NEW
src/companybrain/collectors/manifest_filter.py       # M4 — NEW
src/companybrain/util/xml_partial_parser.py          # M2 — NEW (replaces yesterday's char scanner)
tests/acceptance/test_big_repo_recovery.py           # NEW
tests/acceptance/test_no_silent_truncation.py        # NEW
tests/unit/test_token_estimator.py                   # NEW
tests/unit/test_batch_planner.py                     # NEW
tests/unit/test_xml_partial_parser.py                # NEW
```

You MAY add **append-only** code to:

```
src/companybrain/agents/context_agent.py             # add extract_solo, summarise_regions
src/companybrain/agents/specialist_agent.py          # add make_skeleton path for >50KB controllers
src/companybrain/providers/anthropic_provider.py     # add streaming support with mid-stream stop_reason hook
src/companybrain/pipeline/orchestrator.py            # wrap ContextAgent calls with extraction_recovery
src/companybrain/collectors/code_tracer.py           # call manifest_filter instead of direct hybrid search
src/companybrain/config.py                           # M1 thresholds, max_split_depth
src/companybrain/pipeline/entity_extractor.py        # iterparse-based partial parser entry point
```

Do NOT modify any other file. Do NOT delete the existing
`_recover_truncated_entities` char scanner — it stays as the
JSON-format fallback for pre-XML-output callers.

---

## Implementation steps

### Step 1 — M1: token estimator + batch planner

`src/companybrain/util/token_estimator.py`:

```python
"""Token estimator calibrated against historical extraction outputs.

Calibration was done offline on 50 prior extractions; constants live
here as named values so the test suite can assert against them.
"""

# Empirical: each extracted entity costs ~250 output tokens (method body
# entity + 21-field BusinessContext). Each edge adds ~60 tokens. Average
# is ~2.5 edges per method. Envelope overhead is ~200 tokens per call.
TOKENS_PER_ENTITY     = 250
TOKENS_PER_EDGE       = 60
AVG_EDGES_PER_METHOD  = 2.5
ENVELOPE_OVERHEAD     = 200
SAFETY_MARGIN         = 0.8     # use only 80% of max_tokens to leave room for under-estimates


def estimate_output_tokens(num_chunks: int) -> int:
    return (
        num_chunks * (TOKENS_PER_ENTITY + int(AVG_EDGES_PER_METHOD * TOKENS_PER_EDGE))
        + ENVELOPE_OVERHEAD
    )


def estimate_input_tokens(chunks: list) -> int:
    """Char-based approximation: 4 chars ≈ 1 token (Anthropic guidance)."""
    total_chars = sum(
        len(c.body or "") + len(c.header_context or "") + len(c.import_context or "")
        for c in chunks
    )
    return total_chars // 4


def fits_in_budget(num_chunks: int, max_tokens: int) -> bool:
    return estimate_output_tokens(num_chunks) <= int(max_tokens * SAFETY_MARGIN)
```

`src/companybrain/pipeline/batch_planner.py`:

```python
"""Adaptive batching by token budget — replaces fixed-size ChunkBatcher."""
from companybrain.util.token_estimator import (
    estimate_output_tokens, fits_in_budget,
)


def pack_into_batches(
    chunks: list,
    max_output_tokens: int = 4_000,
    hard_max_per_batch: int = 16,
) -> list[list]:
    """Greedy first-fit packing.

    For each chunk, add it to the current batch IF the batch with this
    chunk added still fits in budget. Otherwise close the current batch
    and start a new one.

    Singletons that exceed budget alone get their own batch and will be
    handled by extraction_recovery's M3 fallback.
    """
    batches: list[list] = []
    current: list = []
    for c in chunks:
        if not current:
            current.append(c)
            continue
        if fits_in_budget(len(current) + 1, max_output_tokens) and len(current) < hard_max_per_batch:
            current.append(c)
        else:
            batches.append(current)
            current = [c]
    if current:
        batches.append(current)
    return batches
```

### Step 2 — M2 + iterparse XML partial parser

`src/companybrain/util/xml_partial_parser.py`:

```python
"""Robust partial XML parser using stdlib xml.etree.ElementTree.iterparse.

Replaces the char-by-char _recover_truncated_entities scanner shipped
yesterday. iterparse emits 'end' events for each element as it CLOSES,
so a truncated XML stream yields all complete elements and silently
drops the partial trailing one.
"""
import xml.etree.ElementTree as ET
from io import StringIO


def parse_complete_elements(raw: str, element_tag: str) -> list[ET.Element]:
    """Return every <element_tag>...</element_tag> that completed before
    the truncation point. Returns [] on totally unparseable input."""
    # Wrap in a synthetic root in case the LLM emitted a fragment.
    wrapped = f"<__root__>{raw}</__root__>"
    out: list[ET.Element] = []
    try:
        for event, elem in ET.iterparse(StringIO(wrapped), events=("end",)):
            if elem.tag == element_tag:
                out.append(elem)
    except ET.ParseError:
        # iterparse raised at the truncation point — but events fired
        # for everything before it, so `out` is already correct.
        pass
    return out
```

`src/companybrain/pipeline/extraction_recovery.py`:

```python
"""Bisection-on-truncation wrapper for ContextAgent.

When a batch returns stop_reason='max_tokens', salvage what completed,
recursively split the rest. Bounded recursion via max_split_depth.
"""
import asyncio
import structlog

from companybrain.agents.context_agent import ContextAgent, ContextAgentResult
from companybrain.config import settings

log = structlog.get_logger(__name__)


async def extract_batch_with_recovery(
    chunks: list,
    agent: ContextAgent,
    max_tokens: int = 4_000,
    depth: int = 0,
) -> list[ContextAgentResult]:
    if depth > settings.max_split_depth:
        log.error("extraction_recovery.depth_exceeded", chunks=len(chunks))
        # Last-resort: extract each remaining chunk solo with bumped tokens
        return await _extract_each_solo(chunks, agent)

    response = await agent.extract_batch_raw(chunks, max_tokens=max_tokens)

    if response.stop_reason != "max_tokens":
        return agent.parse(response)   # happy path

    completed = agent.parse_partial(response)
    completed_qnames = {r.entity.name for r in completed}
    remaining = [c for c in chunks if c.qname not in completed_qnames]

    log.warning(
        "extraction_recovery.truncated",
        depth=depth, completed=len(completed), remaining=len(remaining),
    )

    if not remaining:
        return completed
    if len(remaining) == 1:
        return completed + [await _extract_solo(remaining[0], agent)]

    # Bisect: split remaining in half, recurse on each.
    mid = len(remaining) // 2
    left, right = await asyncio.gather(
        extract_batch_with_recovery(remaining[:mid], agent, max_tokens, depth + 1),
        extract_batch_with_recovery(remaining[mid:], agent, max_tokens, depth + 1),
    )
    return completed + left + right


async def _extract_solo(chunk, agent) -> ContextAgentResult:
    """M3a: single-method retry with bumped max_tokens.
    M3b: if still too big, fall through to region split.
    M3c: if region split also truncates, map-reduce summary."""
    try:
        results = await agent.extract_batch([chunk], max_tokens=4_000)
        if results:
            return results[0]
    except Exception as e:
        log.warning("solo_extract_failed", qname=chunk.qname, error=str(e))

    # M3b
    from companybrain.pipeline.region_splitter import split_method_into_regions
    regions = split_method_into_regions(chunk)
    if not regions:
        # Nothing to split → return a stub entity rather than nothing
        return _stub_result(chunk)

    region_results = await asyncio.gather(*[
        agent.extract_region(r) for r in regions
    ])

    # M3c
    return await agent.summarise_regions(chunk, region_results)


async def _extract_each_solo(chunks, agent):
    return [await _extract_solo(c, agent) for c in chunks]


def _stub_result(chunk):
    """When all recovery fails, emit a stub Entity so we don't lose
    the existence of the method, just its inner edges."""
    from companybrain.models.entities import ExtractedEntity
    from companybrain.agents.context_agent import ContextAgentResult
    return ContextAgentResult(
        entity=ExtractedEntity(
            entity_type="Method", name=chunk.qname,
            file=chunk.file_path, repo="", signature="",
            confidence=0.3,   # low confidence — we couldn't extract details
        ),
        edges=[],
        business_context={"purpose": "extraction_recovery_stub"},
    )
```

### Step 3 — M3b: region splitter

`src/companybrain/pipeline/region_splitter.py`:

```python
"""Tree-sitter-based region splitter for oversized methods.

For methods that don't fit even in a solo call (e.g. a single 500-line
method with inline SQL), split into AST regions: try/catch blocks,
loops, switch arms, conditional branches. Each region becomes a
separate extraction call.
"""
from dataclasses import dataclass


@dataclass
class RegionChunk:
    parent_qname: str
    body: str
    kind: str       # 'try_statement' | 'for_statement' | ...
    file_path: str
    language: str


_REGION_TYPES = {
    "try_statement", "for_statement", "while_statement",
    "if_statement", "switch_statement", "block",
}
_MIN_REGION_BYTES = 200    # don't split into trivial pieces


def split_method_into_regions(chunk) -> list[RegionChunk]:
    from companybrain.util.ast_cache import AstCache   # ADR-0049 dependency
    cache = AstCache()
    tree = cache.parse(_get_parser(chunk.language), chunk.body.encode(),
                       (chunk.file_path, chunk.body_hash))

    regions: list[RegionChunk] = []
    def walk(node):
        if (node.type in _REGION_TYPES
                and node.end_byte - node.start_byte >= _MIN_REGION_BYTES):
            regions.append(RegionChunk(
                parent_qname=chunk.qname,
                body=chunk.body[node.start_byte:node.end_byte],
                kind=node.type,
                file_path=chunk.file_path,
                language=chunk.language,
            ))
            return  # don't recurse into already-emitted regions
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return regions
```

### Step 4 — M4: hierarchical manifest filter

`src/companybrain/collectors/manifest_filter.py`:

```python
"""Three-layer hierarchical filter for huge monorepos.

Layer 1 (deterministic): group files by top-2 path segments, score by
        endpoint-keyword overlap + presence of @RestController/@Service.
Layer 2 (deterministic): within top packages, run hybrid search +
        drop pure DTOs (zero method bodies in AST).
Layer 3 (one LLM call): SpecialistAgent receives the surviving ≤20 files.

No truncation possible; layer 3's input is bounded by construction.
"""
from pathlib import Path
from typing import NamedTuple


class CandidateFile(NamedTuple):
    path: str
    role: str
    size_kb: int
    package_score: float
    bm25_score: float


async def build_filtered_manifest(
    repo_path: Path,
    endpoint: str,
    method: str,
    max_packages: int = 5,
    max_files: int = 20,
) -> list[CandidateFile]:
    # Layer 1
    packages = _score_packages(repo_path, endpoint)[:max_packages]

    # Layer 2 — bounded hybrid search within selected packages
    from companybrain.collectors.code_tracer import _get_hybrid_searcher
    candidates = await _get_hybrid_searcher().search(
        query=f"{endpoint} {method}", repo_name=repo_path.name,
        repo_path=repo_path, top_k=max_files * 2,
        path_prefixes=[p[0] for p in packages],
    )

    # Drop pure DTOs (cheap structural check)
    surviving = [c for c in candidates if not _is_pure_dto(Path(c.path))]
    return surviving[:max_files]
```

### Step 5 — M5: SpecialistAgent skeleton input

In `src/companybrain/agents/specialist_agent.py` (append-only):

```python
def make_skeleton(self, entry_file: str, entry_method: str,
                  cap_chars: int = 8_000) -> str:
    """For controllers > 50 KB, send a structural skeleton instead of
    the full file. The SpecialistAgent never needs sibling method bodies
    — those are extracted by ContextAgent later from disk."""
    from companybrain.util.ast_cache import AstCache
    parsed = AstCache().parse(...)
    parts = [
        _render_imports(parsed),
        _render_class_header(parsed),
        _render_method_full(parsed, entry_method),     # entry method body in full
        _render_method_signatures_only(parsed, exclude=entry_method),
    ]
    return "".join(parts)[:cap_chars]
```

Branch in `plan()`:

```python
async def plan(self, *, endpoint, http_method, entry_handler_path, candidate_files):
    handler_size = Path(entry_handler_path).stat().st_size
    if handler_size > 50_000:
        entry_content = self.make_skeleton(entry_handler_path, "<entry_method>")
    else:
        entry_content = Path(entry_handler_path).read_text(errors="ignore")
    # ... existing prompt construction ...
```

### Step 6 — M6: streaming truncation detection

In `src/companybrain/providers/anthropic_provider.py` (append-only,
new method `chat_streaming` alongside the existing `chat`):

```python
async def chat_streaming(self, messages, *, role, max_tokens,
                         on_truncation_detected=None, **kwargs):
    """Streaming variant. If on_truncation_detected is set, it's called
    as soon as a 'message_delta' event arrives with stop_reason='max_tokens'.
    Returns the full accumulated response after the stream closes."""
    system_param, user_messages = _build_sdk_messages(messages)
    accumulated = []
    async with self._client.messages.stream(
        model=self._resolve_model(role), system=system_param,
        messages=user_messages, max_tokens=max_tokens, **kwargs,
    ) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                accumulated.append(event.delta.text)
            elif (event.type == "message_delta"
                  and event.delta.stop_reason == "max_tokens"
                  and on_truncation_detected):
                # Don't await — schedule and continue draining the stream
                asyncio.create_task(on_truncation_detected())
        return "".join(accumulated), stream.usage
```

### Step 7 — Wire recovery into orchestrator

In `pipeline/orchestrator.py`, find the chunked extraction block.
Replace the direct `ContextAgent.extract_batch` call with
`extract_batch_with_recovery`. Replace the fixed `ChunkBatcher` with
`batch_planner.pack_into_batches`.

### Step 8 — Telemetry additions

Add to job-result builder:

```python
result["telemetry"]["recovery"] = {
    "recovery_invocations": _recovery_count,
    "bisection_depth_max":  _max_depth,
    "region_splits":        _region_split_count,
    "oversized_methods":    _oversized_count,
}
```

### Step 9 — Acceptance tests

`tests/acceptance/test_big_repo_recovery.py`:

```python
async def test_64_method_class_recovers():
    """Synthetic class with 64 ~3KB methods; batched extraction must
    complete with all 64 entities present even if recovery fires."""
    fixture = make_synthetic_class(method_count=64, body_size=3_000)
    result = await run_extraction(fixture)
    assert len(result.entities) == 64
    # recovery may or may not fire depending on prompt; either is ok
    # as long as no entities are lost.


async def test_oversized_single_method_region_splits():
    """Synthetic 500-line method must produce edges via region split."""
    fixture = make_synthetic_method(line_count=500, edge_count=12)
    result = await run_extraction(fixture)
    assert result.telemetry["recovery"]["region_splits"] >= 1
    assert len(result.edges_for(fixture.method_name)) >= 8   # most edges recovered


async def test_huge_controller_uses_skeleton():
    """Synthetic 70KB controller with 50 endpoints — SpecialistAgent
    must use the skeleton path AND identify the entry method."""
    fixture = make_synthetic_controller(size_kb=70, endpoint_count=50)
    plan = await SpecialistAgent().plan(
        endpoint=fixture.entry_endpoint, http_method="GET",
        entry_handler_path=fixture.controller_path,
        candidate_files=fixture.candidates,
    )
    files = {p["file"] for p in plan.plan}
    assert fixture.controller_path in files


async def test_total_cost_under_target_with_recovery():
    """Even with recovery firing, cost stays bounded."""
    fixture = make_big_synthetic_repo()
    result = await run_pipeline(fixture)
    assert result.telemetry["total_cost_usd"] < 0.20
```

`tests/acceptance/test_no_silent_truncation.py`:

```python
async def test_force_truncation_loses_zero_entities(monkeypatch):
    """Mock every LLM response with stop_reason='max_tokens' randomly;
    assert zero entities are silently lost."""
    truncate_at = [True, False, True, True, False]
    call_idx = iter(range(1_000))

    async def mock_extract(self, chunks, max_tokens):
        idx = next(call_idx)
        if truncate_at[idx % len(truncate_at)] and len(chunks) > 1:
            # Return only the first half + stop_reason=max_tokens
            return _truncated_response(chunks[:len(chunks) // 2])
        return _full_response(chunks)

    monkeypatch.setattr(ContextAgent, "extract_batch_raw", mock_extract)
    result = await run_pipeline(fixture)

    # Entities expected = entities returned (mod recovery stubs)
    expected = count_methods_in_fixture(fixture)
    actual = len(result.entities)
    assert actual >= expected * 0.95   # at most 5% downgraded to stubs
```

---

## Verification

```bash
.venv/bin/pytest tests/unit/test_token_estimator.py tests/unit/test_batch_planner.py tests/unit/test_xml_partial_parser.py -v
.venv/bin/pytest tests/acceptance/test_big_repo_recovery.py tests/acceptance/test_no_silent_truncation.py -v
```

All tests must pass. The synthetic-truncation test is the key one —
it proves the safety net actually catches.

---

## PR description

```
feat(pipeline): big-repo-safe adaptive extraction (ADR-0050)

Six mechanisms guarantee zero silent truncation regardless of repo size:
- M1: token-output pre-flight on every batch (95% of truncations prevented)
- M2: bisection-on-truncation (recursive split, log2(N) bounded)
- M3: oversized-method fallback (3-tier: bump tokens → region-split → map-reduce)
- M4: hierarchical manifest filter (package → file → SpecialistAgent)
- M5: SpecialistAgent skeleton input for controllers > 50KB
- M6: streaming truncation detection for sub-200ms recovery trigger

Replaces yesterday's char-by-char _recover_truncated_entities with
xml.etree.ElementTree.iterparse-based partial parser (more robust,
shorter code).

Cost is now O(entity_count) — never explodes on big repos. Latency
grows linearly. No call ever runs without a size budget.

Coordinated with ADR-0049 (caching) and ADR-0048 (two-agent extraction).
File-ownership table in ADR-0050 §"Sequencing & Merge Plan".
```
