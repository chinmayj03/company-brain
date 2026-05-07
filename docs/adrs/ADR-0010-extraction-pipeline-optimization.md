# ADR-0010: Extraction Pipeline Optimization Directive

**Status:** Accepted  
**Date:** 2026-05-03  
**Authors:** Company Brain Engineering  
**Supersedes:** Portions of ADR-0003 (extractor cost model), ADR-0006 (extraction strategy)

---

## Context

The initial extraction pipeline relied on LLM calls for every file/symbol processed. As the codebase grew, this produced three compounding problems:

1. **Cost**: Large repos triggered thousands of Haiku calls per pipeline run.
2. **Latency**: Sequential LLM calls for entity and relationship extraction were the bottleneck — 30–90 minutes for a 500-file repo.
3. **Noise**: Feeding unfiltered files (generated code, lockfiles, build artifacts) into the LLM produced low-signal extractions and burned token budget.

A comprehensive optimization directive was produced and accepted, covering eight concerns. This ADR records the decisions.

---

## Decision Areas

### 1. Prompt Caching (Anthropic provider)

**Decision:** Use `cache_control: ephemeral` on all system prompts when calling Anthropic models.

**Rationale:** System prompts are static per pipeline run (extraction instructions, schema definitions). Cache hits cost 10× less than fresh input tokens. The `anthropic_provider.py` wraps the system prompt in a `[{"type": "text", "text": ..., "cache_control": {"type": "ephemeral"}}]` block. Cache creation/read token counts are captured in `ChatResponse` and forwarded to the cost ledger.

**Expected impact:** 50–90% cost reduction on Anthropic runs after the first call per session.

---

### 2. Cost Telemetry (`LLMCallRecord` + `compute_cost_usd`)

**Decision:** Every LLM call records provider, model, role, task label, token counts, and USD cost to structured logs and optionally to Langfuse.

**Implementation:**
- `LLMCallRecord` dataclass in `llm/base.py`
- `_PRICE_TABLE` covers Anthropic (Haiku/Sonnet/Opus), Groq (8B/Scout/Qwen), OpenAI (4o-mini/4o)
- `log_llm_call()` emits structlog INFO + no-op Langfuse forward
- `AnthropicProvider.chat()` and `OpenAIProvider.chat()` call both after every response

**Rationale:** Cannot optimise what you cannot measure. Cost attribution per pipeline stage enables data-driven decisions about which roles to downgrade.

---

### 3. Frugality Hierarchy

**Decision:** Six-tier cost ladder, applied in order; higher tiers only invoked when lower tiers fail to produce sufficient signal.

| Tier | Method | Cost |
|------|--------|------|
| 1 | tree-sitter AST parse | zero |
| 2 | regex / pattern matching | zero |
| 3 | heuristics (name patterns, structural shape) | zero |
| 4 | code embeddings (voyage-code-3) | ~$0.00012/1K tokens |
| 5 | small LLM — Haiku / llama-3.1-8b | ~$0.001/1K tokens |
| 6 | large LLM — Sonnet / Opus | ~$0.015/1K tokens |

The relationship extractor uses `TaskRole.BALANCED` (not `FAST`) to avoid Groq's 6,000 TPM limit on the 8B model.

---

### 4. Pre-Extraction File Filters (`FileWalker` + `ExtractionFilter`)

**Decision:** Before any LLM invocation, apply a deterministic filter that classifies every file in the repo.

**`FileWalker` rules:**
- Always skip: `node_modules`, `.git`, `__pycache__`, `dist`, `build`, `target`, `vendor`, `.venv`, `coverage`, `.idea/.vscode`, `generated/`, `.next/.nuxt`
- Respect `.gitignore` and `.cbignore` (project-specific excludes) via `gitignore-parser`
- Generated file detection: 11 filename patterns (`_pb2.py`, `.generated.ts`, `*.min.js`, etc.) + 12 header byte markers (`// Code generated`, `# AUTO-GENERATED`, etc.)
- Size caps: skip > 500 KB; flag > 100 KB but continue
- Lockfiles: index package names as `ExternalDependency` nodes, skip body extraction

**`ExtractionFilter` tiers:**
- `skip` — test utility files
- `tier1` — config, interfaces, DTOs (structure only; no body LLM call)
- `tier2` — small implementation files < 5 KB
- `tier5` — controller/service/repository implementation (full LLM extraction)
- `tier3` — unknown (default to standard extraction)

**Expected impact:** Typically 20–40% of files in a real repo are generated/vendor/test — eliminating these before LLM reduces token spend proportionally.

---

### 5. Anthropic Batch API (`BatchProcessor`)

**Decision:** Non-time-sensitive enrichment calls (e.g. business-context synthesis for already-indexed symbols) are sent through the Anthropic Batch API.

**Rules:**
- Batch mode when ≥ 10 requests are queued; sequential otherwise
- 50% cost discount vs. synchronous API
- 24-hour SLA — acceptable for background enrichment, not for interactive extraction
- `process_with_fallback()` falls back to sequential on API errors

**Not used for:** relationship extraction (interactive during pipeline run), entity extraction first pass, user-facing query responses.

---

### 6. Per-Symbol Incremental Extraction (`SymbolHasher`)

**Decision:** Re-extract only symbols whose content has changed since the last run.

**Implementation:**
- `SymbolHasher.hash_file()` uses tree-sitter to extract method/function bodies and computes SHA-256 per symbol
- Falls back to regex extraction (`def `, `function `, `class `) when tree-sitter grammar unavailable
- `SymbolHasher.diff(old, new)` returns the set of changed symbol names
- Hashes stored in Redis at `cb:sym:{repo}:{filepath}` with 7-day TTL

**Expected impact:** On incremental runs (post-first-index), typically only 5–15% of symbols change per commit — reduces LLM calls by 85–95%.

---

### 7. Active Learning — Pattern Distiller (`PatternDistiller`)

**Decision:** High-confidence LLM-extracted relationship edges are persisted in Redis and promoted to tier-2 deterministic patterns after ≥ 3 corroborating observations.

**Flow:**
1. Before LLM extraction: `apply_patterns()` returns pre-computed relationship candidates
2. After LLM extraction: `record_edges()` stores edges with confidence ≥ 0.9 in `cb:patterns:{workspace_id}`
3. Patterns accumulate across runs — LLM call rate decreases over time

**Design constraints:**
- Patterns are workspace-scoped (not cross-repo)
- Only edges with confidence ≥ 0.9 are recorded to avoid noise propagation
- Promotion threshold of 3 observations balances false-positive risk vs. learning speed

---

### 8. Hybrid Retrieval Stack (`HybridSearcher`)

**Decision:** Replace single-strategy lookup with a 4-stage pipeline: BM25 → dense (voyage-code-3/Qdrant) → graph expansion → cross-encoder rerank.

**Stages:**
1. **BM25** (`BM25Index`, rank-bm25): top-50 candidates from camelCase-aware tokenizer; path tokens appended for filename boosting
2. **Dense** (`CodeEmbedder` + `QdrantStore`): voyage-code-3 embeddings, INT8 scalar quantization (4× memory reduction, ~zero recall loss), top-50 candidates
3. **Graph expansion**: 1–2 typed hops from seeds (callers, callees, implements) via Neo4j
4. **Rerank** (`Reranker`, bge-reranker-v2-m3 cross-encoder): union of BM25 + dense candidates, 40/60 weighted merge, cross-encoder rescore, top-10 final results

**Graceful degradation:** BM25 always works (no external deps). Dense retrieval is skipped if `VOYAGE_API_KEY` is absent. Reranker falls back to original scores if `sentence-transformers` is not installed.

**Critical constraint:** `voyage-code-3` is mandatory for dense code retrieval. Generic text embedding models (`text-embedding-3-small`, etc.) must not be substituted — they perform significantly worse on code understanding tasks.

---

### 9. Observability Infrastructure

**Decision:** All LLM interactions are observable via Langfuse (self-hosted).

**Infrastructure additions (docker-compose.infra.yml):**
- `qdrant` (v1.9.2, ports 6333/6334)
- `neo4j` (5.18-community, ports 7474/7687)
- `langfuse-db` (postgres:16-alpine, isolated from app DB)
- `langfuse` (langfuse/langfuse:2, port 3001)

**Langfuse integration:** `LangfuseTracker` singleton; all methods are silent no-ops if `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are not set. Cost, token count, and prompt version tracked per generation.

---

## Consequences

### Positive
- Pipeline cost projected to drop 70–95% on incremental runs (symbol hashing + pattern distiller)
- Retrieval quality improves substantially over keyword-only matching (hybrid search + reranking)
- Cost visibility enables per-stage optimization and budget alerts
- All optimization layers degrade gracefully — system works without any optional deps

### Negative
- New infrastructure dependencies (Qdrant, Langfuse) increase `make up` startup time
- First-run embedding indexing takes O(1 minute) for a 500-file repo
- bge-reranker-v2-m3 requires ~1.5 GB RAM for CPU inference; can be disabled on memory-constrained machines
- Pattern distiller adds Redis dependency for persistence (already required for session state)

### Neutral
- voyage-code-3 requires a paid API key; free tier (1M tokens/month) is sufficient for most dev use cases
- Langfuse is fully self-hosted; no data leaves the network unless explicitly configured

---

## Deferred Items

The following directive items were evaluated and explicitly deferred:

| Item | Reason deferred |
|------|-----------------|
| SCIP output format | Requires SCIP CLI toolchain integration; no clear consumer in current MCP surface |
| stack-graphs cross-file name resolution | High complexity; current import-graph approach covers 90% of use cases |
| Inngest/Temporal durable execution | Operational overhead outweighs benefit at current scale |
| semgrep annotation wiring | Semgrep dep installed; annotation discovery via AST covers immediate needs |

These may be revisited in a future phase when the indexing scale warrants them.

---

## References

- `companybrain/llm/base.py` — `LLMCallRecord`, `compute_cost_usd`, `log_llm_call`
- `companybrain/llm/anthropic_provider.py` — prompt caching implementation
- `companybrain/pipeline/file_walker.py` — pre-extraction filters
- `companybrain/pipeline/extraction_filter.py` — tier classification
- `companybrain/pipeline/symbol_hasher.py` — per-symbol incremental hashing
- `companybrain/pipeline/batch_processor.py` — Anthropic Batch API wrapper
- `companybrain/pipeline/pattern_distiller.py` — active learning / pattern promotion
- `companybrain/retrieval/hybrid_search.py` — 4-stage retrieval orchestrator
- `companybrain/observability/langfuse_client.py` — Langfuse integration
- `docker-compose.infra.yml` — Qdrant, Neo4j, Langfuse service definitions
