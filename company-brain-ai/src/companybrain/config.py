"""
Application configuration — loaded from environment variables.

Quick-start (local dev, no API keys):
  Copy .env.example to .env and run:
  docker compose -f docker-compose.infra.yml up -d

LLM provider switch:
  LLM_PROVIDER=ollama      → local Ollama (default, no keys needed)
  LLM_PROVIDER=anthropic   → requires ANTHROPIC_API_KEY
  LLM_PROVIDER=openai      → requires OPENAI_API_KEY
  LLM_PROVIDER=groq        → requires GROQ_API_KEY (free tier, 300+ tok/s)
  LLM_PROVIDER=openrouter  → requires OPENROUTER_API_KEY (30+ free models)
"""

from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"

    # ── LLM Provider ─────────────────────────────────────────────────────────
    # Switch the entire inference backend with one variable.
    # Valid values: ollama | anthropic | openai | groq | openrouter
    llm_provider: str = "groq"

    # Ollama (local) — used when llm_provider=ollama
    ollama_host: str = "http://localhost:11434"

    # Per-role model overrides for Ollama (optional)
    ollama_model_fast:      str = "llama3.1:8b"
    ollama_model_balanced:  str = "deepseek-coder-v2:16b"
    ollama_model_synthesis: str = "deepseek-r1:14b"
    ollama_model_reasoning: str = "deepseek-r1:14b"
    ollama_model_query:     str = "deepseek-r1:14b"

    # Anthropic — used when llm_provider=anthropic
    anthropic_api_key: str = ""
    # Defaults use haiku for cheap/fast tasks and sonnet for synthesis.
    # Override ANTHROPIC_MODEL_SYNTHESIS=claude-opus-4-7 for highest quality.
    anthropic_model_fast:      str = "claude-haiku-4-5-20251001"
    anthropic_model_balanced:  str = "claude-sonnet-4-6"
    anthropic_model_synthesis: str = "claude-sonnet-4-6"   # was opus — 5× cheaper, 90% quality
    anthropic_model_reasoning: str = "claude-sonnet-4-6"
    anthropic_model_query:     str = "claude-sonnet-4-6"
    # Set to true to use Anthropic's Batch API (async, 24hr, 50% cost reduction).
    # The BatchProcessor in batch_processor.py is fully implemented — this was the only
    # thing preventing it from being used. Enable for all non-blocking pipeline stages
    # (context synthesis, gap detection, relationship extraction). Tradeoff: results
    # arrive in a polling loop (up to 24h) rather than synchronously — acceptable for
    # background indexing, not for interactive queries.
    anthropic_use_batch_api: bool = True

    # OpenAI — used when llm_provider=openai
    openai_api_key: str = ""
    openai_base_url: Optional[str] = None   # Set for Azure OpenAI or custom endpoints
    openai_model_fast:      str = "gpt-4o-mini"
    openai_model_balanced:  str = "gpt-4o"
    openai_model_synthesis: str = "gpt-4o"
    openai_model_reasoning: str = "gpt-4o"
    openai_model_query:     str = "gpt-4o"

    # Groq — used when llm_provider=groq
    # Get API key at: https://console.groq.com/keys  (no credit card required)
    #
    # Current model lineup (April 2026):
    #   openai/gpt-oss-20b              →  1,000 tok/s  — fastest, great for extraction
    #   meta-llama/llama-4-scout-17b-16e-instruct → 750 tok/s — vision + tool use
    #   openai/gpt-oss-120b             →    500 tok/s  — best reasoning on Groq
    #   qwen/qwen3-32b                  →    400 tok/s  — strong reasoning, 131K ctx
    #   llama-3.3-70b-versatile         →    280 tok/s  — proven, reliable
    #   llama-3.1-8b-instant            →    560 tok/s  — ultra-fast fallback
    groq_api_key: str = ""
    groq_model_fast:      str = "llama-3.1-8b-instant"                        # 560 tok/s, text model — high-volume extraction
    groq_model_balanced:  str = "meta-llama/llama-4-scout-17b-16e-instruct"   # 750 tok/s, tool-use, text model
    groq_model_synthesis: str = "meta-llama/llama-4-scout-17b-16e-instruct"   # 30k TPM — context synthesis
    groq_model_reasoning: str = "meta-llama/llama-4-scout-17b-16e-instruct"  # 30k TPM — gap detection (qwen3-32b only 6k TPM)
    groq_model_query:     str = "meta-llama/llama-4-scout-17b-16e-instruct"  # 30k TPM — user-facing queries

    # OpenRouter — used when llm_provider=openrouter
    # Free tier: 30+ models available, 20 req/min on free models
    # Get API key at: https://openrouter.ai/keys
    openrouter_api_key: str = ""
    openrouter_model_fast:      str = "meta-llama/llama-3.1-8b-instruct:free"
    openrouter_model_balanced:  str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_model_synthesis: str = "deepseek/deepseek-r1:free"
    openrouter_model_reasoning: str = "deepseek/deepseek-r1:free"
    openrouter_model_query:     str = "meta-llama/llama-3.3-70b-instruct:free"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://companybrain:companybrain@localhost:5432/companybrain"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Git / GitHub ─────────────────────────────────────────────────────────
    github_token: str = ""   # Optional — only for PR enrichment

    # ── AWS / SQS ─────────────────────────────────────────────────────────────
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    sqs_endpoint: str = "http://localhost:4566"    # LocalStack default
    sqs_ingestion_queue: str = "company-brain-ingestion"

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret: str = "dev-secret-change-in-production"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "vscode-webview://*",
    ]

    # ── Retrieval stack ────────────────────────────────────────────────────
    qdrant_url:        str  = "http://localhost:6333"
    qdrant_api_key:    str  = ""                    # empty = no auth (dev)
    qdrant_collection: str  = "code_embeddings"
    voyage_api_key:    str  = ""                    # VOYAGE_API_KEY env var
    reranker_model:    str  = "BAAI/bge-reranker-v2-m3"  # local cross-encoder
    bm25_top_k:        int  = 50                    # BM25 candidates before rerank
    dense_top_k:       int  = 50                    # dense vector candidates
    rerank_top_k:      int  = 10                    # final reranked results
    hybrid_search_enabled: bool = True             # feature flag

    # ── Observability ──────────────────────────────────────────────────────
    langfuse_public_key:  str  = ""                # LANGFUSE_PUBLIC_KEY env var
    langfuse_secret_key:  str  = ""                # LANGFUSE_SECRET_KEY env var
    langfuse_host:        str  = "http://localhost:3001"
    otel_endpoint:        str  = ""                # e.g. http://localhost:4318/v1/traces
    otel_enabled:         bool = False

    # ── Pipeline tuning ───────────────────────────────────────────────────────
    max_commits_per_repo: int = 200
    cluster_window_hours: int = 24
    max_entity_extraction_concurrency: int = 10
    max_context_synthesis_concurrency: int = 5

    # ── Context window & output token budgets ────────────────────────────────
    # max_input_tokens: hard cap on prompt size sent to any LLM.
    #   Claude models support 200K tokens; gpt-4o supports 128K.
    #   Keep headroom for the response: set to (context_window - max_output_tokens).
    #   Default 120_000 is safe for both providers.
    max_input_tokens: int = 120_000

    # Per-stage output token limits.  Raise these if you see truncated JSON responses.
    # The previous hardcoded value for entity extraction was 512 — dangerously low
    # for files with many classes/methods.
    # Groq llama-3.1-8b-instant supports up to 8K output tokens.
    # Diff-based extraction can produce large JSON arrays — 6K gives ample headroom.
    # max_tokens sets the billing ceiling per LLM call — right-size these to P95 actual output.
    # Overly generous ceilings waste buffer on retries and inflate tail latency.
    # Use Langfuse to audit actual output token distributions and tighten further.
    # Tightened per-stage caps so output cost can't balloon past what each
    # stage actually produces. Empirical p95 sizes (claude-haiku-4-5):
    #   entity extraction   →  10-30 entities × ~80 tok →   ~2000  (cap 2000)
    #   intent synthesis    →  one MethodIntent × ~400 tok →  ~400  (cap  700)
    #   relationship extr.  →  up to 80 edges × ~50 tok  →   ~4000  (cap 2500)
    #   context synthesis   →  one 21-field ctx × ~600 tok → ~600  (cap  900)
    #   gap detection       →  3-8 gaps × ~150 tok       →   ~1200  (cap 1000)
    # Caps sit ~2× the empirical p95 so real responses don't get truncated,
    # but we stop paying for unused output budget.
    max_tokens_entity_extraction:  int = 6_000   # Stage 1 (raised from 2000 → 4000 → 6000;
                                                  # large repository impls like
                                                  # CompetitivenessRepositoryImpl have 30+ methods
                                                  # with rich query_text/code_snippet — anything
                                                  # smaller truncates the JSON mid-string)
    max_tokens_intent_synthesis:   int = 900     # Stage 1.5 (raised 700 → 900 for richer intent)
    max_tokens_relationship:       int = 4_000   # Stage 2 (raised 2500 → 4000 — 50-type taxonomy
                                                  # × 80 edges + per-edge evidence strings was hitting
                                                  # the cap on dense classes)
    max_tokens_context_synthesis:  int = 1_400   # Stage 3 (raised 900 → 1400 for the 21-field
                                                  # BusinessContext with longer evidence quotes)
    max_tokens_gap_detection:      int = 1_500   # Stage 4 (raised 1000 → 1500)
    max_tokens_query:              int = 4_096   # Live query responses — keep generous

    # ── Stage skip flags (cost-cut) ──────────────────────────────────────────
    # When True, Stage 1.5 (intent synthesis) is skipped entirely. With the
    # expanded 21-field BusinessContext schema (commit 5aa83a1c4), Stage 3
    # already captures purpose / side_effects / change_risk / gaps which were
    # the main signal IntentSynthesizer produced. Skipping Stage 1.5 cuts ~50
    # LLM calls per typical pipeline run.
    # Override via env var: BRAIN_SKIP_INTENT_SYNTHESIS=true
    skip_intent_synthesis: bool = False
    # When True, Stage 4 (gap detection) is skipped. One LLM call but useful
    # for fast iteration / demo runs where gaps aren't being acted on.
    skip_gap_detection:    bool = False
    # When True, the query-time intent router (ADR-0043 WS2) is skipped.
    # The query route falls back to the 'concept' template + default index.
    # Override via env var: BRAIN_SKIP_INTENT_ROUTER=true
    skip_intent_router:    bool = False

    # ── Query-time budget & cache (ADR-0043) ─────────────────────────────────
    # brain_query_budget_usd: hard ceiling on LLM spend per /query call.
    # Enforced by the provider's cost tracker. Set to 0 to disable.
    brain_query_budget_usd:   float = 0.05
    # brain_query_cache_ttl_sec: how long intent-router classifications are
    # cached in-process before re-classifying. 0 = always re-classify.
    brain_query_cache_ttl_sec: int  = 3600

    # ── ADR-0042: per-pass token budgets ─────────────────────────────────────
    # Each budget sits ~2× the empirical p95 for that pass to prevent truncation.
    max_tokens_annotation_pass:       int = 800     # E2 — ANNOTATES edges
    max_tokens_storage_target_pass:   int = 1_500   # E3 — DatabaseTable entities
    max_tokens_schema_migration_pass: int = 2_500   # E5 — migration schema
    max_tokens_client_call_pass:      int = 1_500   # E6 — CALLS_ENDPOINT edges
    max_tokens_test_coverage_pass:    int = 2_500   # E7 — TESTED_BY edges
    max_tokens_intent_router:         int = 300     # E10 — intent classification

    # ── ADR-0042: job cost guard ──────────────────────────────────────────────
    # Abort the pipeline if cumulative cost exceeds this threshold.
    # Override via env var: BRAIN_JOB_BUDGET_USD=0.50
    brain_job_budget_usd: float = 0.50

    # ── ADR-0044: chunked extraction ──────────────────────────────────────────
    # Routes extraction through the per-method chunk queue (no truncation).
    # Override via env var: BRAIN_USE_CHUNK_QUEUE=false to temporarily disable.
    use_chunk_queue: bool = True
    # Escape hatch: BRAIN_LEGACY_EXTRACT=true restores the old per-file path.
    use_legacy_extract: bool = False
    # Max parallel workers draining the extraction queue per pipeline run.
    chunk_queue_max_workers: int = 4

    # ── ADR-0042 E10: intent router ────────────────────────────────────────────
    # When True, the intent router is called before SmartZoneAssembler.
    # Adds ~200ms per query but dramatically improves subgraph selection.
    enable_intent_router: bool = True

    # ── ADR-0048: two-agent batched extraction ────────────────────────────────
    # Number of methods sent in one ContextAgent batch call.
    # Higher = fewer LLM calls, more context per call. Tune via Langfuse p95.
    context_agent_batch_size: int = 8
    # BRAIN_USE_LEGACY_NAVIGATOR=true → restore KnowledgeNavigatorAgent ReAct loop.
    # Keep as fallback while two-agent path stabilises.
    use_legacy_navigator: bool = False

    # ── ADR-0050: big-repo-safe adaptive extraction ───────────────────────────
    # M1: token pre-flight thresholds for batch sizing.
    # Batches whose estimated output exceeds this are split before the call.
    adr0050_max_batch_output_tokens: int = 4_000
    # Hard limit on batch size regardless of token budget.
    adr0050_hard_max_per_batch: int = 16
    # M2: max bisection recursion depth (log2(64)=6 covers any realistic batch).
    max_split_depth: int = 6
    # M5: controllers larger than this get a skeleton input instead of full text.
    adr0050_skeleton_threshold_bytes: int = 50_000

    # ── ADR-0051 P1: agentic harness migration ───────────────────────────────
    # When True, run_pipeline() delegates to companybrain.harness.HarnessLoop
    # instead of the linear stage machine in this file. The harness wraps the
    # existing pipeline tools (discover_routes, find_entry_handler, ContextAgent,
    # write_to_brain, etc.) and lets the model decide call order.
    # Override via env var: BRAIN_USE_HARNESS=true. Default false until the P4
    # acceptance suite is green for two weeks (per ADR-0051).
    use_harness: bool = False

    # ── ADR-0051 P2: sub-agents and parallel fan-out ─────────────────────────
    # Maximum concurrent sub-agents per spawn_* tool call. Each sub-agent gets
    # its own LLM context window, so this caps both wall-time fan-out width
    # and provider concurrency. 8 matches the typical batch granularity from
    # ADR-0048's two-agent extraction; raise on hosts with high TPM headroom.
    # Override via env var: MAX_SUBAGENTS=16.
    max_subagents: int = 8
    # Per-sub-agent wall-clock cap. Exceeding it returns a timed_out=True
    # result rather than aborting the whole fan-out — one slow file should
    # not stall the parent's batch. Tuned for the 60-method endpoint
    # acceptance target; raise for repos with very large files.
    # Override via env var: SUBAGENT_TIMEOUT_S=180.
    subagent_timeout_s: int = 120

    # ── ADR-0051 P4: hooks + permissions + streaming + introspection ──────────
    # When False, the harness skips invoking shell hooks regardless of whether
    # `<repo>/.brain/hooks/*.sh` exists. Defaults to True; tests and locked-
    # down environments can flip it off via BRAIN_HOOKS_ENABLED=false.
    hooks_enabled: bool = True
    # Per-hook wall-clock cap. A long hook must offload work elsewhere.
    hook_timeout_s: int = 30
    # Trigger compaction when the running input-token usage exceeds this
    # fraction of the model's context window. 0.80 leaves ~40K headroom on
    # a 200K-window provider for the next multi-tool turn.
    compaction_threshold: float = 0.80
    # Nominal context window used when the provider doesn't expose its own.
    # Compaction is provider-agnostic; widening the window here only delays
    # compaction, it cannot exceed the provider's real ceiling.
    compaction_context_limit_tokens: int = 200_000
    # Treat ASK decisions as AUTO-approve. CLI `--yes` plumbs through here;
    # the env var BRAIN_AUTOAPPROVE=true is also honoured.
    grants_auto_approve: bool = False

    # ── ADR-0052 P5: slash + MCP + workspace + rooms + headless + SDK ──────
    # Mounts the brain-as-MCP route at /mcp/harness on the FastAPI app. Off
    # by default; turn on for environments that want IDEs to connect over
    # HTTP without spinning up `brain mcp serve --http` separately.
    harness_mcp_enabled: bool = False
    # Host + port for `brain mcp serve --http`. Defaults match the docs.
    harness_mcp_host: str = "127.0.0.1"
    harness_mcp_port: int = 8765
    # Cap on `run_repo_command` wall-clock per call (seconds). Tools may
    # request shorter timeouts but never longer.
    run_repo_command_max_timeout_s: int = 300
    # Per-job worktree prefix; visible in `git worktree list`. Tweak only if
    # you have a tmp-dir convention.
    worktree_prefix: str = "brain-wt-"

    # ── ADR-0055 additions ─────────────────────────────────────────────────
    # Cross-file cross-cutting pass (Stage 2.5) tunables. Defaults match the
    # ADR; override via env vars (e.g. CROSS_FILE_PATTERN_MIN_INSTANCES=4).
    # Set ``cross_file_enable_llm_passes=False`` to skip SP-3/4/5 and run
    # only the deterministic SP-1/SP-2 passes — useful for offline tests.
    cross_file_pattern_min_instances: int = 5
    cross_file_antipattern_min_strength: float = 0.80
    cross_file_invariant_window_size: int = 8
    cross_file_enable_llm_passes: bool = True


settings = Settings()
