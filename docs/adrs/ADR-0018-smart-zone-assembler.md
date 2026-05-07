# ADR-0018: Smart-zone context assembler (T0 / T1 / T2 token budgeting)

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 5 days
**Depends on:** ADR-0013 (URN), ADR-0015 (HybridSearcher), ADR-0017 (first-class assumptions/business_context)
**Unblocks:** ADR-0019 (MCP server tools call this)

---

## Context

Today `/query` does basic retrieval: embed the question, find top-k Postgres nodes, dump their summaries to the LLM. There is no token budget, no T0/T1/T2 tiering, no blast radius expansion, no MMR deduplication, no business context auto-injection. This is the gap between "we have a brain" and "the brain answers questions efficiently."

Memory tokens (T0 ≈ 15 tok, T1 ≈ 100 tok) are already generated at Stage 3.5 (`pipeline/memory_tokenizer.py`) and stored in node metadata. They are unused on the read path. The harness §6.1 and v2 §10 prescribe the assembly algorithm.

## Decision

Build a `SmartZoneAssembler` that:
1. **Classifies** the task (READ / WRITE / DEBUG / AUDIT / ONBOARD) — regex first, LLM-fallback for ambiguity.
2. **Retrieves** primary candidates via `HybridSearcher` (ADR-0015).
3. **Expands** by 1–2 hops over Neo4j (blast radius, direction depends on task type).
4. **MMR-reranks** to deduplicate near-identical context.
5. **Tiers** the result: T0 (always loaded for matched + neighbour entities), T1 (deeper for top-N), T2 (full entity JSON for top-K), business_context (always for T2 entities).
6. **Compresses** task-aware (drop fields not relevant to task type).
7. **Renders** the final payload in the format defined in harness §6.2.

Expose as MCP tool `brain_query` (ADR-0019) and as the implementation behind the existing `/query` REST route.

## Implementation

### Module layout

```
company-brain-ai/src/companybrain/assembly/
├── __init__.py
├── smart_zone.py          # SmartZoneAssembler main class
├── classifier.py           # task type classifier (regex + LLM fallback)
├── mmr.py                  # MMR reranking
├── tiering.py              # T0/T1/T2 selection within token budget
├── compressor.py           # task-aware field stripping
├── renderer.py             # build the final payload string
└── types.py                # SmartZonePayload, TokenBudget, TaskType
```

### Files to create

#### `assembly/types.py`

```python
"""Shared types for the smart-zone assembler."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    READ      = "READ"
    WRITE     = "WRITE"
    DEBUG     = "DEBUG"
    AUDIT     = "AUDIT"
    ONBOARD   = "ONBOARD"


@dataclass
class TokenBudget:
    total: int = 6000
    t0_summaries: int = 1200
    t1_detail: int = 3600
    business_context: int = 600
    blast_radius: int = 600

    @classmethod
    def conservative(cls):
        return cls(total=4000, t0_summaries=800, t1_detail=2400,
                   business_context=400, blast_radius=400)

    @classmethod
    def deep(cls):
        return cls(total=12000, t0_summaries=1500, t1_detail=7500,
                   business_context=2000, blast_radius=1000)


@dataclass
class SmartZonePayload:
    task: str
    task_type: TaskType
    t0: list[dict] = field(default_factory=list)         # [{urn, t0_token}]
    t1: list[dict] = field(default_factory=list)         # [{urn, t1_token, ...}]
    t2: list[dict] = field(default_factory=list)         # [{urn, full_entity_json}]
    business_context: list[dict] = field(default_factory=list)
    blast_radius: dict = field(default_factory=dict)     # {urn: [neighbour_urns]}
    tokens_used: int = 0
    tokens_budget: int = 6000
    rendered: str = ""
```

#### `assembly/classifier.py`

```python
"""Task-type classifier — regex first, LLM fallback for ambiguity."""
from __future__ import annotations
import re
from companybrain.assembly.types import TaskType


_PATTERNS = [
    (TaskType.READ,    re.compile(r"\b(what does|explain|how does|describe|summari[sz]e|what is)\b", re.I)),
    (TaskType.WRITE,   re.compile(r"\b(change|modify|add|refactor|implement|fix|update|extend)\b", re.I)),
    (TaskType.DEBUG,   re.compile(r"\b(error|bug|failing|why is|broken|crash|exception|stack trace)\b", re.I)),
    (TaskType.AUDIT,   re.compile(r"\b(review|check|is .* safe|what uses|who calls|impact|blast radius)\b", re.I)),
    (TaskType.ONBOARD, re.compile(r"\b(explain the whole|overview|how does the (system|codebase)|onboard|tour)\b", re.I)),
]


def classify(task: str) -> TaskType:
    """Return the highest-priority match. Default = READ."""
    for tt, pat in _PATTERNS:
        if pat.search(task):
            return tt
    return TaskType.READ


# Retrieval parameters per task type — see harness §6.3
TASK_PARAMS = {
    TaskType.READ:    {"t1_top_n": 5,  "t2_top_k": 2, "hops": 1, "direction": "both",       "mmr_lambda": 0.6},
    TaskType.WRITE:   {"t1_top_n": 8,  "t2_top_k": 4, "hops": 2, "direction": "upstream",   "mmr_lambda": 0.75},
    TaskType.DEBUG:   {"t1_top_n": 8,  "t2_top_k": 4, "hops": 2, "direction": "both",       "mmr_lambda": 0.5},
    TaskType.AUDIT:   {"t1_top_n": 10, "t2_top_k": 0, "hops": 3, "direction": "upstream",   "mmr_lambda": 0.7},
    TaskType.ONBOARD: {"t1_top_n": 12, "t2_top_k": 0, "hops": 0, "direction": "downstream", "mmr_lambda": 0.4},
}
```

#### `assembly/mmr.py`

```python
"""Maximum Marginal Relevance — diversify top-k retrieval results."""
from __future__ import annotations
import math


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b: return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def mmr_rerank(*, query_emb: list[float],
                candidate_embs: dict[str, list[float]],
                relevance: dict[str, float],
                lambda_: float = 0.7,
                top_k: int = 10) -> list[str]:
    """Return up to top_k urns balancing relevance and novelty."""
    selected: list[str] = []
    remaining = list(candidate_embs.keys())
    while remaining and len(selected) < top_k:
        if not selected:
            best = max(remaining, key=lambda u: relevance.get(u, 0.0))
        else:
            best, best_score = None, -1e9
            for u in remaining:
                rel = relevance.get(u, 0.0)
                redundancy = max(
                    cosine(candidate_embs[u], candidate_embs[s]) for s in selected
                )
                score = lambda_ * rel - (1 - lambda_) * redundancy
                if score > best_score:
                    best, best_score = u, score
        selected.append(best)
        remaining.remove(best)
    return selected
```

#### `assembly/tiering.py`

```python
"""T0 / T1 / T2 selection within a token budget."""
from __future__ import annotations
from dataclasses import dataclass

from companybrain.assembly.types import TokenBudget, SmartZonePayload
from companybrain.store.base import BrainEntity


# Approx tokens per character — overestimate slightly for safety.
TOKEN_PER_CHAR = 0.27


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * TOKEN_PER_CHAR))


def assign_tiers(*, ranked_urns: list[str],
                  entities: dict[str, BrainEntity],
                  budget: TokenBudget,
                  t1_top_n: int, t2_top_k: int) -> SmartZonePayload:
    """Place entities into T0 / T1 / T2 slots within budget."""
    payload = SmartZonePayload(task="", task_type=None, tokens_budget=budget.total)

    # T0 — all matched entities get their t0 token (always cheap, ≤ 30 tok each)
    t0_used = 0
    for u in ranked_urns:
        e = entities.get(u)
        if not e: continue
        t0_text = e.t0_token or e.qualified_name
        t = estimate_tokens(t0_text)
        if t0_used + t > budget.t0_summaries:
            break
        payload.t0.append({"urn": u, "t0": t0_text, "type": e.entity_type})
        t0_used += t
    payload.tokens_used += t0_used

    # T1 — top-N get their t1 token (~100 tok each)
    t1_used = 0
    for u in ranked_urns[:t1_top_n]:
        e = entities.get(u)
        if not e or not e.t1_token: continue
        t = estimate_tokens(e.t1_token)
        if t1_used + t > budget.t1_detail:
            break
        payload.t1.append({"urn": u, "t1": e.t1_token, "type": e.entity_type})
        t1_used += t
    payload.tokens_used += t1_used

    # T2 — top-K get full JSON
    t2_used = 0
    for u in ranked_urns[:t2_top_k]:
        e = entities.get(u)
        if not e: continue
        payload_json = e.to_dict()
        text = str(payload_json)
        t = estimate_tokens(text)
        if t2_used + t > budget.t1_detail - t1_used:   # share the same pool
            break
        payload.t2.append({"urn": u, "entity": payload_json})
        t2_used += t
    payload.tokens_used += t2_used

    return payload
```

#### `assembly/compressor.py`

```python
"""Task-aware compression — drop fields not relevant to the task type."""
from companybrain.assembly.types import TaskType


_KEEP_FIELDS = {
    TaskType.READ:    ["t1_summary", "metadata.props", "relationships:CALLS"],
    TaskType.WRITE:   ["t1_summary", "metadata.props", "metadata.state",
                        "relationships", "metadata.assumptions"],
    TaskType.DEBUG:   ["t1_summary", "metadata", "relationships",
                        "metadata.assumptions", "metadata.error_paths"],
    TaskType.AUDIT:   ["t1_summary", "relationships:RELIES_ON",
                        "relationships:CALLS", "metadata.severity"],
    TaskType.ONBOARD: ["t1_summary", "tags"],
}


def compress(t2_entry: dict, task_type: TaskType) -> dict:
    """Return a stripped copy of the entity JSON keeping only relevant fields."""
    keep = _KEEP_FIELDS.get(task_type, ["t1_summary"])
    e = t2_entry["entity"]
    out = {"urn": t2_entry["urn"], "type": e["entity_type"], "qname": e["qualified_name"]}
    for spec in keep:
        path, _, edge_filter = spec.partition(":")
        if edge_filter and path == "relationships":
            out["relationships"] = [r for r in e.get("relationships", [])
                                     if r.get("edge_type") == edge_filter]
        else:
            ref = e
            for part in path.split("."):
                if ref is None: break
                ref = ref.get(part) if isinstance(ref, dict) else None
            if ref is not None:
                out[path] = ref
    return out
```

#### `assembly/renderer.py`

```python
"""Render the assembled payload as a single string in harness §6.2 format."""
from companybrain.assembly.types import SmartZonePayload


def render(payload: SmartZonePayload) -> str:
    lines: list[str] = []
    lines.append("=== COMPANY BRAIN CONTEXT ===\n")
    if payload.t0:
        lines.append("[ENTITY SUMMARIES - T0]")
        for entry in payload.t0:
            lines.append(f"  {entry['urn']}\n    → {entry['t0']}")
        lines.append("")
    if payload.t1:
        lines.append("[ENTITY DETAIL - T1]")
        for entry in payload.t1:
            lines.append(f"  {entry['urn']}\n    {entry['t1']}")
        lines.append("")
    if payload.t2:
        lines.append("[FULL CONTEXT - T2]")
        for entry in payload.t2:
            import json
            lines.append(f"  {entry['urn']}\n```json\n{json.dumps(entry['entity'], indent=2)}\n```")
        lines.append("")
    if payload.business_context:
        lines.append("[BUSINESS CONTEXT]")
        for bc in payload.business_context:
            lines.append(f"  {bc.get('qualified_name','?')}: {bc.get('t1_summary','')}")
        lines.append("")
    if payload.blast_radius:
        lines.append("[BLAST RADIUS]")
        for seed, neighbours in payload.blast_radius.items():
            lines.append(f"  {seed} →")
            for n in neighbours[:10]:
                lines.append(f"    {n}")
        lines.append("")
    lines.append(f"=== END BRAIN CONTEXT (tokens: {payload.tokens_used} / {payload.tokens_budget}) ===")
    return "\n".join(lines)
```

#### `assembly/smart_zone.py`

```python
"""SmartZoneAssembler — orchestrates classify → retrieve → expand → MMR → tier → compress."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional

import structlog

from companybrain.assembly.classifier import classify, TASK_PARAMS
from companybrain.assembly.compressor import compress
from companybrain.assembly.mmr import mmr_rerank
from companybrain.assembly.renderer import render
from companybrain.assembly.tiering import assign_tiers
from companybrain.assembly.types import SmartZonePayload, TaskType, TokenBudget
from companybrain.retrieval.embedder import Embedder, make_embedder
from companybrain.retrieval.hybrid_search import HybridSearcher
from companybrain.store.base import BrainEntity, BrainStore
from companybrain.store.identity import workspace_slug_for, parse_urn

log = structlog.get_logger(__name__)


class SmartZoneAssembler:
    def __init__(self, *, brain_root: Path, workspace_id: str,
                 store: BrainStore, neo4j_driver, embedder: Optional[Embedder] = None):
        self.brain_root = Path(brain_root)
        self.workspace_id = workspace_id
        self.workspace_slug = workspace_slug_for(workspace_id)
        self.store = store
        self.neo4j = neo4j_driver
        self.embedder = embedder or make_embedder()
        self.searcher = HybridSearcher(brain_root=self.brain_root,
                                        workspace_slug=self.workspace_slug,
                                        embedder=self.embedder)

    async def assemble(self, *, task: str, entities: list[str] | None = None,
                        budget: TokenBudget | None = None) -> SmartZonePayload:
        budget = budget or TokenBudget()
        task_type = classify(task)
        params = TASK_PARAMS[task_type]

        # 1. Primary retrieval (URNs already given OR hybrid search)
        if entities:
            primary = entities[:40]
            scores = {u: 1.0 for u in primary}
        else:
            hits = self.searcher.search(task, top_k=40)
            primary = [h.urn for h in hits]
            scores = {h.urn: h.score for h in hits}

        # 2. Blast-radius expansion
        expanded: set[str] = set(primary)
        blast: dict[str, list[str]] = {}
        if params["hops"] > 0:
            for seed in primary[:5]:
                neighbours = await self._neighbours(seed, hops=params["hops"],
                                                     direction=params["direction"])
                blast[seed] = neighbours
                expanded.update(neighbours)

        # 3. Hydrate entities
        all_urns = list(expanded)
        loaded: dict[str, BrainEntity] = {}
        for u in all_urns:
            e = await self.store.read(u)
            if e is not None:
                loaded[u] = e

        # 4. MMR rerank (using t1_token text for embeddings — cheap)
        relevant_urns = [u for u in all_urns if u in loaded]
        if len(relevant_urns) > params["t1_top_n"] and self.embedder:
            cand_embs = {
                u: self.embedder.embed(loaded[u].t1_token or loaded[u].qualified_name)
                for u in relevant_urns
            }
            query_emb = self.embedder.embed(task)
            ranked = mmr_rerank(
                query_emb=query_emb, candidate_embs=cand_embs,
                relevance={u: scores.get(u, 0.5) for u in relevant_urns},
                lambda_=params["mmr_lambda"],
                top_k=params["t1_top_n"] + params["t2_top_k"] + 5,
            )
        else:
            ranked = sorted(relevant_urns, key=lambda u: scores.get(u, 0.0), reverse=True)

        # 5. Tier assignment
        payload = assign_tiers(
            ranked_urns=ranked,
            entities=loaded,
            budget=budget,
            t1_top_n=params["t1_top_n"],
            t2_top_k=params["t2_top_k"],
        )
        payload.task = task
        payload.task_type = task_type
        payload.blast_radius = blast

        # 6. Pull business_context for every T2 entity (always included)
        bc_entities = []
        for entry in payload.t2:
            for rel in entry["entity"].get("relationships", []):
                if rel.get("edge_type") == "EXPLAINS":
                    bc = await self.store.read(rel["target_id"])
                    if bc is not None:
                        bc_entities.append(bc.to_dict())
        payload.business_context = bc_entities

        # 7. Task-aware compression on T2
        payload.t2 = [compress(e, task_type) for e in payload.t2]

        # 8. Render
        payload.rendered = render(payload)
        log.info("smart_zone.assembled",
                 task_type=task_type, primary=len(primary),
                 expanded=len(expanded), tiered=len(ranked),
                 tokens_used=payload.tokens_used)
        return payload

    async def _neighbours(self, urn: str, *, hops: int, direction: str) -> list[str]:
        clause = {
            "upstream":   f"<-[*1..{hops}]-",
            "downstream": f"-[*1..{hops}]->",
            "both":       f"-[*1..{hops}]-",
        }[direction]
        q = f"MATCH (n {{id: $urn}}){clause}(m) RETURN DISTINCT m.id AS id LIMIT 30"
        async with self.neo4j.session() as session:
            result = await session.run(q, urn=urn)
            return [r["id"] for r in await result.data()]
```

### Edits

#### `companybrain/api/routes/query.py`

Replace the existing search-and-prompt flow with:

```python
from companybrain.assembly.smart_zone import SmartZoneAssembler
from companybrain.assembly.types import TokenBudget
from companybrain.store import JsonFileBrainStore, FanoutBrainStore
from neo4j import AsyncGraphDatabase

@router.post("/")
async def query(req: QueryRequest):
    repo_path = Path(req.repo_path or ".")
    brain_root = repo_path / ".brain"
    json_store = JsonFileBrainStore(brain_root)
    driver = AsyncGraphDatabase.driver(...)
    assembler = SmartZoneAssembler(
        brain_root=brain_root, workspace_id=req.workspace_id,
        store=json_store, neo4j_driver=driver,
    )
    payload = await assembler.assemble(task=req.task, budget=TokenBudget())
    # Then call LLM with payload.rendered as the context. Existing LLM provider stays.
    ...
```

## Test plan

`tests/unit/assembly/test_classifier.py`:

```python
import pytest
from companybrain.assembly.classifier import classify
from companybrain.assembly.types import TaskType

@pytest.mark.parametrize("task,expected", [
    ("what does UserCard do",                       TaskType.READ),
    ("change UserCard to show a status indicator",  TaskType.WRITE),
    ("UserCard render is failing in production",    TaskType.DEBUG),
    ("review the auth refactor PR",                 TaskType.AUDIT),
    ("explain the whole codebase to me",            TaskType.ONBOARD),
    ("",                                              TaskType.READ),  # default
])
def test_classify(task, expected):
    assert classify(task) == expected
```

`tests/unit/assembly/test_mmr.py`:

```python
from companybrain.assembly.mmr import mmr_rerank

def test_picks_diverse():
    embs = {
        "a": [1, 0, 0],
        "b": [0.99, 0.01, 0],
        "c": [0, 1, 0],
    }
    rel = {"a": 0.9, "b": 0.85, "c": 0.5}
    chosen = mmr_rerank(query_emb=[1, 0, 0], candidate_embs=embs,
                         relevance=rel, lambda_=0.5, top_k=2)
    # Should pick a (most relevant) then c (most diverse) — not b (near-duplicate of a)
    assert chosen == ["a", "c"]
```

`tests/unit/assembly/test_tiering.py`:

```python
from companybrain.assembly.tiering import assign_tiers, estimate_tokens
from companybrain.assembly.types import TokenBudget
from companybrain.store.base import BrainEntity

def test_tiering_respects_budget():
    entities = {
        f"urn:cb:t:c:r:component:E{i}": BrainEntity(
            id=f"urn:cb:t:c:r:component:E{i}", entity_type="component",
            repo="r", file="f", qualified_name=f"E{i}",
            t0_token="x" * 30, t1_token="y" * 200,
        ) for i in range(20)
    }
    payload = assign_tiers(
        ranked_urns=list(entities.keys()),
        entities=entities,
        budget=TokenBudget(total=1000, t0_summaries=200, t1_detail=400, business_context=100, blast_radius=100),
        t1_top_n=10, t2_top_k=2,
    )
    assert payload.tokens_used <= 700  # T0 + T1 budget
```

`tests/integration/test_smart_zone.py`:

```python
import pytest
from pathlib import Path
from companybrain.assembly.smart_zone import SmartZoneAssembler
from companybrain.store import JsonFileBrainStore
from neo4j import AsyncGraphDatabase

@pytest.mark.asyncio
@pytest.mark.integration
async def test_assemble_returns_payload(tmp_path):
    # populate .brain/ with a few entities
    store = JsonFileBrainStore(tmp_path)
    # ... (write a few BrainEntity instances)
    driver = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))
    assembler = SmartZoneAssembler(
        brain_root=tmp_path, workspace_id="ws",
        store=store, neo4j_driver=driver,
    )
    payload = await assembler.assemble(task="what does UserCard do")
    assert payload.task_type.value == "READ"
    assert payload.rendered.startswith("=== COMPANY BRAIN CONTEXT ===")
    assert payload.tokens_used > 0
```

## Acceptance criteria

- [ ] `companybrain/assembly/` package with all seven modules.
- [ ] All unit tests pass.
- [ ] Integration test against running Neo4j + .brain/ passes.
- [ ] `/query` REST route uses `SmartZoneAssembler.assemble()` and returns the rendered payload + the LLM answer in the response.
- [ ] Token budget is respected: `payload.tokens_used <= payload.tokens_budget`.
- [ ] Task type classification covers all 5 types (READ/WRITE/DEBUG/AUDIT/ONBOARD).
- [ ] `business_context` is auto-included for every T2 entity that has an `EXPLAINS` edge.
- [ ] `brain query "what does X do" --repo ./pilot` (CLI from ADR-0016) returns a rendered payload.

## Verification commands

```bash
# Run unit tests
pytest tests/unit/assembly/ -v

# Run integration test
pytest tests/integration/test_smart_zone.py -v

# Live query
brain query "what does PaymentService do" --repo ./pilot --top-k 5

# Check rendered output structure
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"task":"explain UserCard","workspace_id":"...","repo_path":"./pilot"}' | jq .rendered
```

## Rollback

`git revert <commit-sha>`. The `/query` route reverts to the simpler retrieval; nothing in storage was modified.

## Out of scope

- **Semantic cache.** v2 §9 — add in Stage 3.
- **Streaming responses.** Today the assembler returns once. Streaming the rendered payload to the LLM is a perf optimisation for v2.
- **LLM-fallback for ambiguous task classification.** Regex-only is sufficient for Stage 1; if accuracy disappoints, add a Haiku call gated on `classify(task) == TaskType.READ` AND query length > 30 words.
- **Per-entity token-budget weighting.** Today every entity gets equal budget within a tier. A future ADR can weight by score / runtime traffic / criticality.
- **Token estimator using actual tokenizer.** `TOKEN_PER_CHAR=0.27` is an overestimate; replace with tiktoken / Claude's tokenizer in a follow-up if budgets become tight.
