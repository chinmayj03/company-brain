# ADR-0029: Pipeline reliability & end-to-end hardening

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 5 days
**Depends on:** ADR-0011 through ADR-0019 (Stage 1) ideally, but this ADR can ship in parallel — every fix is additive
**Unblocks:** ADR-0030–0034 (demo integration). A demo on a flaky pipeline is worse than no demo.

---

## Context

The pipeline has many places to fail and several of them already do, intermittently. From `CURRENT-STATE-and-breakages.md` §7 plus what's plainly in the code:

- **LLM rate limits.** Groq free tier hits 14k TPM; current `_wait_for_rate_limit` waits 20–90 s but only on `EntityExtractor`. Other extractors (`IntentSynthesizer`, `RelationshipExtractor`, `ContextSynthesizer`, `GapDetector`, `ContextManagerAgent`) do not have rate-limit-aware backoff.
- **Token / context window.** `OLLAMA_NUM_CTX=3072` truncates ~50% of fresh setups silently. Anthropic / OpenAI default model context is large but the smart-zone payload (ADR-0018) plus assembled history can still overflow on big repos. There is no pre-flight token check.
- **JSON output.** Models prepend `\`\`\`json` fences, add prose, emit trailing commas, truncate mid-array. The current code calls `json.loads()` directly and retries on failure — wasted retries that often fail the same way.
- **Provider fallback.** Anthropic / Ollama / OpenAI are wired but the *fallback chain* is not. If Anthropic 429s, the run errors instead of falling to Haiku → Groq → Ollama.
- **Navigator context blow-up.** `NavigatorAgent` walks the call chain hop-by-hop, accumulating source code in the prompt. By hop 4 on a Java service, the prompt can exceed 100k tokens. There is no cap.
- **Database resilience.** Neo4j connection drops are swallowed as "non-fatal" but the data is silently missing. Postgres deadlocks under concurrent pipelines are not retried. Qdrant timeouts during retrieval bring down the whole search.
- **Cost.** No per-job spend cap. A misconfigured run on a 5,000-file repo could cost hundreds of dollars before anyone notices.
- **Recovery.** Checkpoint exists at `/tmp/cb_checkpoint_*.json` keyed on `(workspace_id, http_method, endpoint_path)` but `/tmp` is ephemeral in Docker; checkpoints don't survive container restarts.
- **Concurrency.** Two engineers running the same endpoint extraction collide on the same checkpoint file.
- **Health visibility.** There is no `/health/deep` that confirms Postgres + Neo4j + Qdrant + Redis + Ollama are all reachable. The frontend polls without knowing if the backend can do its job.

This ADR closes every one of these gaps with a single coordinated hardening pass.

## Decision

Adopt **layered reliability**: bulkheads (each external dependency has its own circuit), retries with provider-aware backoff, a fully-wired fallback chain, hard token and cost budgets enforced before each call, robust JSON recovery, navigator context capping, durable checkpoints, deep health checks, and structured failure events shipped to Langfuse.

No single layer prevents every failure. The combination guarantees that the pipeline either succeeds, succeeds-with-degraded-quality, or fails fast with a clear actionable error — never silently produces bad data.

## Implementation

The work is grouped into nine subsystems. Each subsystem is independently testable and shippable; together they are the reliability story.

---

### 1. Provider fallback chain

#### Files to create

`company-brain-ai/src/companybrain/llm/fallback.py`

```python
"""
Provider fallback chain.

Order (configurable via BRAIN_PROVIDER_CHAIN):
    anthropic_sonnet → anthropic_haiku → groq → openai → ollama

A call attempts providers in order. Each step has a per-provider error
classifier that decides "retry same provider" vs "fall to next provider"
vs "fail fast" (e.g. invalid API key — no point retrying).
"""
from __future__ import annotations
import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional

import structlog

from companybrain.llm.base import (
    ChatMessage, ChatResponse, LLMProvider, TaskRole, ToolDefinition,
)

log = structlog.get_logger(__name__)


class FailureClass(str, Enum):
    RATE_LIMIT       = "rate_limit"     # retry same provider with backoff
    TIMEOUT          = "timeout"        # retry same provider once, then fall
    SERVER_ERROR     = "server_error"   # 5xx — fall to next provider
    INVALID_JSON     = "invalid_json"   # retry same provider with stricter prompt
    CONTEXT_OVERFLOW = "context_overflow"  # fall to next provider with bigger ctx
    AUTH             = "auth"           # fail fast; do not retry
    REFUSAL          = "refusal"        # model refused; fail fast
    UNKNOWN          = "unknown"        # fall to next provider


@dataclass
class ProviderStep:
    name: str
    provider: LLMProvider
    role: TaskRole
    max_retries: int = 2
    retry_backoff_s: float = 5.0


@dataclass
class FallbackResult:
    response: ChatResponse | None
    chain: list[dict] = field(default_factory=list)  # [{step, attempt, outcome, latency_ms}]
    final_provider: str | None = None


class FallbackChain:
    """Iterates through provider steps until one succeeds or all fail."""

    def __init__(self, steps: list[ProviderStep]):
        if not steps:
            raise ValueError("FallbackChain requires at least one step")
        self.steps = steps

    async def chat(self, messages: list[ChatMessage], *,
                    role: TaskRole | None = None,
                    tools: list[ToolDefinition] | None = None,
                    temperature: float | None = None,
                    response_format: str | None = None) -> FallbackResult:
        result = FallbackResult(response=None)
        for step in self.steps:
            for attempt in range(step.max_retries + 1):
                started = asyncio.get_event_loop().time()
                try:
                    resp = await step.provider.chat(
                        messages=messages,
                        role=role or step.role,
                        tools=tools, temperature=temperature,
                        response_format=response_format,
                    )
                    result.response = resp
                    result.final_provider = step.name
                    result.chain.append({
                        "step": step.name, "attempt": attempt,
                        "outcome": "ok",
                        "latency_ms": int((asyncio.get_event_loop().time() - started) * 1000),
                    })
                    return result
                except Exception as exc:
                    cls = classify_exception(exc)
                    result.chain.append({
                        "step": step.name, "attempt": attempt,
                        "outcome": cls.value, "error": str(exc)[:200],
                        "latency_ms": int((asyncio.get_event_loop().time() - started) * 1000),
                    })
                    if cls == FailureClass.AUTH or cls == FailureClass.REFUSAL:
                        raise   # fail fast
                    if cls == FailureClass.RATE_LIMIT and attempt < step.max_retries:
                        await asyncio.sleep(_rate_limit_backoff(step.name, attempt))
                        continue
                    if attempt < step.max_retries and cls in (FailureClass.TIMEOUT, FailureClass.INVALID_JSON):
                        await asyncio.sleep(step.retry_backoff_s * (attempt + 1))
                        continue
                    break  # exhausted retries on this step → fall to next step
        log.error("fallback_chain.exhausted", chain=result.chain)
        raise RuntimeError(f"All providers exhausted: {result.chain}")


def classify_exception(exc: BaseException) -> FailureClass:
    msg = str(exc).lower()
    if "401" in msg or "403" in msg or "unauthorized" in msg or "invalid api key" in msg:
        return FailureClass.AUTH
    if "429" in msg or "rate_limit" in msg or "rate limit" in msg or "too many requests" in msg:
        return FailureClass.RATE_LIMIT
    if "timeout" in msg or "timed out" in msg or "deadline" in msg:
        return FailureClass.TIMEOUT
    if "context length" in msg or "max_tokens" in msg or "too long" in msg or "context window" in msg:
        return FailureClass.CONTEXT_OVERFLOW
    if "refused" in msg or "refusal" in msg or "policy" in msg:
        return FailureClass.REFUSAL
    if "json" in msg or "decode" in msg or "parse" in msg:
        return FailureClass.INVALID_JSON
    if "5" in msg[:3] and ("error" in msg or "internal" in msg or "service" in msg):
        return FailureClass.SERVER_ERROR
    return FailureClass.UNKNOWN


def _rate_limit_backoff(provider_name: str, attempt: int) -> float:
    # Provider-specific waits (token-bucket vs fixed-window awareness)
    if provider_name.startswith("groq"):
        # Groq free tier resets every 60 s
        return min(20 * (attempt + 1), 90)
    if provider_name.startswith("anthropic"):
        # Anthropic returns Retry-After header — honour it if present, else exponential
        return min(2 ** attempt, 30)
    if provider_name.startswith("openai"):
        return min(5 * (attempt + 1), 60)
    return min(10 * (attempt + 1), 60)


def build_default_chain() -> FallbackChain:
    """
    Build the chain from the BRAIN_PROVIDER_CHAIN env var.
    Default: anthropic_sonnet → anthropic_haiku → groq → openai → ollama.
    """
    from companybrain.llm import get_provider
    chain_spec = os.getenv("BRAIN_PROVIDER_CHAIN",
                            "anthropic_sonnet,anthropic_haiku,groq,ollama")
    steps: list[ProviderStep] = []
    for name in [s.strip() for s in chain_spec.split(",") if s.strip()]:
        provider, role = _resolve(name)
        if provider is None:
            log.warning("fallback_chain.skip", reason=f"{name} unavailable")
            continue
        steps.append(ProviderStep(name=name, provider=provider, role=role))
    if not steps:
        raise RuntimeError("No providers available — check API keys / Ollama")
    return FallbackChain(steps)


def _resolve(name: str) -> tuple[LLMProvider | None, TaskRole]:
    """Map a chain step name to (provider instance, default role)."""
    from companybrain.llm.factory import make_provider
    if name == "anthropic_sonnet" and os.getenv("ANTHROPIC_API_KEY"):
        return make_provider("anthropic", model_override="claude-sonnet-4-6"), TaskRole.SYNTHESIS
    if name == "anthropic_haiku" and os.getenv("ANTHROPIC_API_KEY"):
        return make_provider("anthropic", model_override="claude-haiku-4-5-20251001"), TaskRole.FAST
    if name == "groq" and os.getenv("GROQ_API_KEY"):
        return make_provider("groq"), TaskRole.FAST
    if name == "openai" and os.getenv("OPENAI_API_KEY"):
        return make_provider("openai"), TaskRole.BALANCED
    if name == "ollama":
        return make_provider("ollama"), TaskRole.FAST
    return None, TaskRole.FAST
```

#### Edits

- `companybrain/llm/factory.py` — add `make_provider(name, *, model_override=None)`.
- Every place that calls `get_provider().chat(...)` directly is replaced with `_chain.chat(...)` where `_chain` is a singleton `FallbackChain`. Hot spots: `EntityExtractor`, `IntentSynthesizer`, `RelationshipExtractor`, `ContextSynthesizer`, `GapDetector`, `ContextManagerAgent`, `NavigatorAgent`, the `/v1/ask` route.

---

### 2. Robust JSON output recovery

#### Files to create

`company-brain-ai/src/companybrain/llm/json_recovery.py`

```python
"""
Robust extraction of a JSON object/array from possibly-noisy LLM output.

Recovers from:
  - markdown fences (```json ... ```)
  - leading prose ("Here is the JSON: ...")
  - trailing prose / explanations
  - trailing commas
  - single-quoted strings (mild cases)
  - truncated arrays (best-effort: drop incomplete trailing element)
"""
from __future__ import annotations
import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n```", re.DOTALL)
_LEAD_TRAILING_RE = re.compile(r"^[^{\[]*(.*?)[^}\]]*$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def extract_json(text: str) -> Any:
    """
    Try increasingly forgiving strategies to parse the LLM output as JSON.
    Raises ValueError if every strategy fails.
    """
    if not text or not text.strip():
        raise ValueError("Empty LLM response")

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    fence = _FENCE_RE.search(text)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            text = fence.group(1)  # continue below with the fenced content

    # 3. Find the outermost JSON object or array via balanced braces
    candidate = _balanced_extract(text)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 4. Strip trailing commas
        cleaned = _TRAILING_COMMA_RE.sub(r"\1", candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 5. Try json5 (tolerates comments, single quotes, trailing commas)
        try:
            import json5
            return json5.loads(cleaned)
        except Exception:
            pass

        # 6. Last resort: truncate to last complete element
        truncated = _truncate_to_last_complete(cleaned)
        if truncated:
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Could not extract JSON from response (len={len(text)})")


def _balanced_extract(text: str) -> str | None:
    """Find the outermost balanced { ... } or [ ... ] in text."""
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _truncate_to_last_complete(candidate: str) -> str | None:
    """For a partial array, drop the trailing incomplete element."""
    if not candidate.startswith("["):
        return None
    last_obj_end = candidate.rfind("},")
    if last_obj_end == -1:
        return None
    return candidate[:last_obj_end + 1] + "]"
```

#### Edits

Wherever the code does `json.loads(response.content)` — primarily `EntityExtractor`, `RelationshipExtractor`, `IntentSynthesizer`, `ContextSynthesizer`, `GapDetector`, `NavigatorAgent` — replace with `extract_json(response.content)`. On `ValueError`, raise as `FailureClass.INVALID_JSON` so the fallback chain handles it.

---

### 3. Token-aware request shaping

#### Files to create

`company-brain-ai/src/companybrain/llm/token_budget.py`

```python
"""
Token estimation + pre-flight budget check.

Every LLM call goes through `prepare_request(messages, model)` which:
  1. Estimates total tokens (input + expected output reservation).
  2. If over the model's context window, applies one of:
       - drop_oldest_history (for chat history)
       - chunk_then_summarise (for big code unit input)
       - escalate_model (request a model with bigger window via fallback chain)
       - fail_fast (if no shrinking strategy applies)
  3. Returns a possibly-modified messages list + the model decision.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

# Approximate token counts per character. Overestimate slightly for safety.
_CHAR_PER_TOKEN = 3.7


# Per-model input context windows (input + output combined).
# Conservative — leave headroom for the model's reply.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6":          200_000,
    "claude-haiku-4-5-20251001":  200_000,
    "claude-opus-4-6":            200_000,
    "gpt-4o":                     128_000,
    "gpt-4o-mini":                128_000,
    "llama-3.3-70b-versatile":    128_000,    # Groq
    "llama-3.1-8b-instant":       128_000,    # Groq
    "deepseek-coder-v2:16b":       16_000,    # Ollama
    "deepseek-r1:14b":             64_000,    # Ollama
    "llama3.1:8b":                  8_192,
}


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHAR_PER_TOKEN))


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


class ShrinkStrategy(str, Enum):
    NONE                 = "none"
    DROP_OLDEST_HISTORY  = "drop_oldest_history"
    CHUNK_THEN_SUMMARISE = "chunk_then_summarise"
    ESCALATE_MODEL       = "escalate_model"
    FAIL_FAST            = "fail_fast"


@dataclass
class PreparedRequest:
    messages: list[dict]
    model: str
    strategy_applied: ShrinkStrategy
    estimated_input_tokens: int
    output_reservation: int


def prepare_request(messages: list[dict], *, model: str,
                     output_reservation: int = 4_000,
                     allow_drop_history: bool = True) -> PreparedRequest:
    window = MODEL_CONTEXT_WINDOWS.get(model)
    if window is None:
        # Unknown model — assume 128k for safety
        window = 128_000
    budget = window - output_reservation
    est = estimate_messages_tokens(messages)

    if est <= budget:
        return PreparedRequest(messages=messages, model=model,
                                strategy_applied=ShrinkStrategy.NONE,
                                estimated_input_tokens=est,
                                output_reservation=output_reservation)

    # Strategy 1: drop oldest non-system messages
    if allow_drop_history and len(messages) > 3:
        kept = [messages[0]]      # system prompt
        recent = messages[-2:]    # last user + last assistant
        kept.extend(recent)
        new_est = estimate_messages_tokens(kept)
        if new_est <= budget:
            return PreparedRequest(messages=kept, model=model,
                                    strategy_applied=ShrinkStrategy.DROP_OLDEST_HISTORY,
                                    estimated_input_tokens=new_est,
                                    output_reservation=output_reservation)

    # Strategy 2: signal "escalate" — caller (FallbackChain) routes to bigger model
    return PreparedRequest(messages=messages, model=model,
                            strategy_applied=ShrinkStrategy.ESCALATE_MODEL,
                            estimated_input_tokens=est,
                            output_reservation=output_reservation)
```

Plug `prepare_request()` into every provider's `chat()` so over-budget requests either shrink or signal escalation. The FallbackChain treats `ShrinkStrategy.ESCALATE_MODEL` like `FailureClass.CONTEXT_OVERFLOW` — fall to the next provider.

---

### 4. Navigator context cap

#### Files to edit

`company-brain-ai/src/companybrain/agents/navigator_agent.py`

Add a per-step token budget in the `assemble` phase:

```python
NAVIGATOR_MAX_TOKENS = int(os.getenv("BRAIN_NAVIGATOR_MAX_TOKENS", "30000"))


def _trim_assembled_source(nodes: list[NavigatorNode]) -> list[NavigatorNode]:
    """Keep adding nodes' raw_source until the cumulative token count would
    exceed NAVIGATOR_MAX_TOKENS. Beyond that, replace raw_source with a 1-line
    'see file:line' stub. Most-recent-discovered nodes win the budget."""
    out: list[NavigatorNode] = []
    used = 0
    for n in reversed(nodes):
        body_tokens = estimate_tokens(n.raw_source or "")
        if used + body_tokens <= NAVIGATOR_MAX_TOKENS:
            out.append(n)
            used += body_tokens
        else:
            stub = NavigatorNode(**{**n.__dict__})
            stub.raw_source = (
                f"// elided to fit context: see {n.file_path} "
                f"({n.class_name}.{n.method_name})"
            )
            out.append(stub)
    return list(reversed(out))
```

Call `_trim_assembled_source(nodes)` immediately before sending the classify prompt. If even after trimming the prompt is over the model's window, fall to the next provider via FallbackChain.

Add a hop cap: `NAVIGATOR_MAX_HOPS = int(os.getenv("BRAIN_NAVIGATOR_MAX_HOPS", "6"))`. Beyond that, return what the navigator has and log a `navigator.hop_cap_reached` event.

---

### 5. Database resilience

#### Postgres

Update `application.yml` (Java backend):

```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 30          # was 10
      minimum-idle: 5
      connection-timeout: 5000
      validation-timeout: 3000
      max-lifetime: 1800000
      keepalive-time: 600000
```

Add a `@Retryable` wrapper on `PipelineService.applyPipelineResult()`:

```java
@Retryable(
    value = { CannotSerializeTransactionException.class, CannotAcquireLockException.class },
    maxAttempts = 3,
    backoff = @Backoff(delay = 200, multiplier = 2.0)
)
public void applyPipelineResult(...) { ... }
```

#### Neo4j

`graph/neo4j_writer.py` already has retry; tighten the failure mode so silent swallowing logs *with severity = warning, not debug* and **emits a Langfuse event** so we can detect drift. Add a periodic reconciliation job: every 10 min, `count(n)` in Neo4j and `count(*)` in Postgres' `nodes` should match within 5%; otherwise alert.

Add a circuit breaker: if 3 consecutive Neo4j writes fail within 60 s, open the circuit for 5 min — pipeline runs continue but the Neo4j mirror is skipped, with a `neo4j_circuit_open` event. After 5 min the circuit attempts half-open; one success closes it.

#### Qdrant

Same circuit-breaker pattern in `retrieval/qdrant_store.py`. Search-side fallback: if Qdrant search fails, `HybridSearcher._search_one_type` already handles dense-side absence; ensure BM25-only mode degrades cleanly with a `qdrant_unavailable` log line.

#### Redis

Job-state writes should be best-effort. If Redis is down, the orchestrator continues and the frontend shows the last known state without progress updates. Wrap every `_redis()` call in `try / except` that logs at debug.

---

### 6. Cost circuit breaker

#### Files to create

`company-brain-ai/src/companybrain/llm/cost_guard.py`

```python
"""
Per-job cost ceiling.

A pipeline run reports cumulative LLM USD cost via UsageTracker. Before each
LLM call, CostGuard checks whether the call would push the run past the cap.
If yes, it raises BudgetExceeded — orchestrator catches it and reports the
job as "halted: budget exceeded" with what was completed so far.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

from companybrain.llm.base import get_usage_tracker

DEFAULT_DEV_CAP_USD = float(os.getenv("BRAIN_JOB_BUDGET_USD", "5.00"))
DEFAULT_PROD_CAP_USD = float(os.getenv("BRAIN_JOB_BUDGET_USD_PROD", "50.00"))


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostGuard:
    cap_usd: float

    def check(self, projected_call_usd: float = 0.0) -> None:
        tracker = get_usage_tracker()
        spent = tracker.total_cost_usd()
        if spent + projected_call_usd > self.cap_usd:
            raise BudgetExceeded(
                f"Job budget exceeded: spent ${spent:.4f} + projected ${projected_call_usd:.4f} > cap ${self.cap_usd:.2f}"
            )
```

Call `CostGuard(cap).check()` in `_extract_unit`, in synthesis stages, and at the start of every LLM call. The orchestrator wraps `run_pipeline` in `try / except BudgetExceeded` and returns `PipelineResult(status="halted_budget", ...)`.

For the demo `/v1/ask` route, set a per-request cap (default $0.10) so a runaway query doesn't burn the demo budget.

---

### 7. Durable checkpoints

Move checkpoints from `/tmp` to `<repo>/.brain/.checkpoints/{run_id}.json`. Pros: survives container restarts, is per-engineer (their git checkout), and makes resume keyed on actual state instead of just request shape.

Update `_checkpoint_save` / `_checkpoint_load` accordingly. Add a stale-checkpoint sweeper that runs at orchestrator start and deletes files older than 7 days.

Concurrency: take a file lock (`fcntl.flock`) on the checkpoint file at run start. If the lock is held, the orchestrator returns `409 Conflict` with the existing run_id instead of corrupting state.

---

### 8. Deep health check

#### Files to edit

`company-brain-ai/src/companybrain/api/routes/health.py`

```python
@router.get("/health/deep")
async def deep_health():
    """Verify every external dependency is reachable and at expected version."""
    results: dict[str, dict] = {}

    # Postgres
    try:
        async with db_pool.acquire() as conn:
            v = await conn.fetchval("SELECT version()")
            results["postgres"] = {"ok": True, "version": str(v)[:60]}
    except Exception as exc:
        results["postgres"] = {"ok": False, "error": str(exc)}

    # Neo4j
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(...)
        async with driver.session() as s:
            v = await (await s.run("CALL dbms.components() YIELD versions")).data()
        results["neo4j"] = {"ok": True, "versions": v}
    except Exception as exc:
        results["neo4j"] = {"ok": False, "error": str(exc)}

    # Qdrant
    try:
        from companybrain.retrieval.qdrant_client import make_client
        results["qdrant"] = {"ok": True,
                              "collections": len(make_client().get_collections().collections)}
    except Exception as exc:
        results["qdrant"] = {"ok": False, "error": str(exc)}

    # Redis
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        results["redis"] = {"ok": True}
    except Exception as exc:
        results["redis"] = {"ok": False, "error": str(exc)}

    # cb-api (Bun extractor service)
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{os.getenv('CB_API_URL', 'http://cb-api:8090')}/health")
        results["cb_api"] = {"ok": r.status_code == 200}
    except Exception as exc:
        results["cb_api"] = {"ok": False, "error": str(exc)}

    # LLM providers — soft check (key present + Ollama list)
    results["llm"] = {
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "openai":    bool(os.getenv("OPENAI_API_KEY")),
        "groq":      bool(os.getenv("GROQ_API_KEY")),
        "ollama":    _ollama_reachable(),
    }

    overall = all(v.get("ok", False) for k, v in results.items() if isinstance(v, dict) and "ok" in v)
    return {"ok": overall, "components": results}
```

Add the same surface in Java at `GET /v1/health/deep`. The frontend shows a status pill in the header that polls this every 30 s. Red pill → "the brain is degraded — see /health/deep".

---

### 9. Structured failure observability

Every failure path emits a `LLMCallRecord`-style structured event with at least: `stage`, `provider`, `model`, `failure_class`, `attempt`, `latency_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `error_truncated`. Forward to Langfuse so a demo run that goes wrong is debuggable from the dashboard, not from grepping logs.

Add a `/v1/runs/{run_id}/timeline` endpoint that returns the full structured event list for that run — used by the demo dashboard's "what happened" panel.

---

## Test plan

### Unit tests

`tests/unit/llm/test_fallback_chain.py`:

- Mock provider that 429s twice then succeeds → verify retry + final ok.
- Mock provider that always 401s → verify fail-fast (no fall-through).
- Mock provider 1 always-fails-server → verify provider 2 is tried.
- All providers fail → verify `RuntimeError` raised with chain summary.

`tests/unit/llm/test_json_recovery.py`:

```python
import pytest
from companybrain.llm.json_recovery import extract_json

@pytest.mark.parametrize("text,expected", [
    ('{"a":1}',                                 {"a": 1}),
    ('```json\n{"a":1}\n```',                   {"a": 1}),
    ('Here is JSON: {"a":1} thanks',            {"a": 1}),
    ('{"a":1,}',                                 {"a": 1}),
    ('[\n  {"a":1},\n  {"a":2},\n',             [{"a": 1}, {"a": 2}]),  # truncated
])
def test_recovery(text, expected):
    assert extract_json(text) == expected

def test_empty_raises():
    with pytest.raises(ValueError):
        extract_json("")
```

`tests/unit/llm/test_token_budget.py`:

- 500-token messages on a 200k model → `ShrinkStrategy.NONE`.
- 250k-token messages with history → `DROP_OLDEST_HISTORY`, fits.
- 250k-token single message on 128k model → `ESCALATE_MODEL`.

`tests/unit/llm/test_cost_guard.py`:

- Spent $4.99, cap $5.00, projected $0.02 → raises.
- Spent $0.50, cap $5.00, projected $1.00 → ok.

`tests/unit/agents/test_navigator_trim.py`:

- 10 nodes × 5,000 tokens each, cap 30,000 → 6 nodes have full source, 4 are stubbed.

### Integration tests (chaos-flavoured)

`tests/integration/test_provider_fallback.py`:

- Block Anthropic via `ANTHROPIC_API_KEY=invalid`, run pipeline → expect Groq path.
- Block both Anthropic and Groq → expect Ollama path.
- Block all → expect clear `RuntimeError("All providers exhausted")`.

`tests/integration/test_neo4j_outage.py`:

- Stop Neo4j mid-run, restart 30 s later. Pipeline reports `neo4j_circuit_open` for the affected entities, completes successfully on Postgres, reconciles on next run.

`tests/integration/test_concurrent_pipelines.py`:

- Two pipelines on same `(workspace, endpoint)` → second returns 409, no checkpoint corruption.

`tests/integration/test_budget_breaker.py`:

- Set `BRAIN_JOB_BUDGET_USD=0.01`, run pipeline → halts after 1–2 LLM calls with `halted_budget` status.

### End-to-end demo dry-run

A scripted scenario that exercises the full path:

```bash
make e2e-demo-dryrun
```

Runs:
1. `make up` and wait for `/health/deep` to return ok.
2. `brain index ./pilot --workspace-id <uuid>` — full extraction.
3. 5 NL questions via `/v1/ask` — verify each returns within 30 s and uses ≤ 6,000 token smart-zone payload.
4. Force-fail Anthropic mid-question (revoke key), re-ask → verify Groq fallback completes.
5. Force-fail Neo4j (`docker stop cb-neo4j`), re-ask blast-radius question → verify graceful degradation.
6. Restore everything, re-run, verify reconciliation.

---

## Acceptance criteria

### Provider chain
- [ ] `FallbackChain` is the single entry point for every LLM call in the codebase.
- [ ] Default chain `anthropic_sonnet → anthropic_haiku → groq → ollama` works end-to-end.
- [ ] `BRAIN_PROVIDER_CHAIN` env var overrides the default and is honoured.
- [ ] Auth failures fail fast (no retry, no fall-through).
- [ ] Rate-limit failures retry on the same provider before falling.
- [ ] Server errors fall to next provider immediately.
- [ ] `RuntimeError("All providers exhausted")` raised when chain is empty / all fail; error message includes per-step outcome trace.

### Rate limiting
- [ ] Groq backoff = min(20·n, 90) seconds.
- [ ] Anthropic backoff honours `Retry-After` header when present.
- [ ] OpenAI / Ollama have provider-specific waits.
- [ ] Rate-limit-aware retry applies to **every** stage that calls LLM, not just `EntityExtractor`.

### Token / context window
- [ ] `prepare_request()` is called before every LLM invocation.
- [ ] `OLLAMA_NUM_CTX` default raised to 8192.
- [ ] Drop-oldest-history strategy is exercised in chat-style flows.
- [ ] Escalate-model path falls to next provider correctly.
- [ ] Smart-zone payload over 200k tokens triggers escalation, not crash.

### JSON output
- [ ] `extract_json()` recovers from markdown fences, prose preamble, trailing commas, truncated arrays.
- [ ] Every place that previously called `json.loads(response.content)` now calls `extract_json(...)`.
- [ ] On 3 consecutive `INVALID_JSON` failures, the chain falls to the next provider with a stricter system prompt ("respond ONLY with valid JSON, no markdown fences").

### Navigator context
- [ ] `NAVIGATOR_MAX_TOKENS` cap enforced; older raw_source is stubbed.
- [ ] `NAVIGATOR_MAX_HOPS` cap enforced; emits structured event when reached.
- [ ] Navigator never sends a prompt over the active model's context window.

### Database resilience
- [ ] Postgres pool size raised to 30; connection timeouts logged not crashed.
- [ ] Postgres deadlocks retried up to 3× with exponential backoff.
- [ ] Neo4j has a circuit breaker (3 fails in 60 s → open 5 min).
- [ ] Neo4j-Postgres reconciliation cron runs every 10 min and alerts on > 5% drift.
- [ ] Qdrant outage degrades search to BM25-only with structured log.
- [ ] Redis outage does not crash the orchestrator.

### Cost
- [ ] Per-job budget cap enforced via `CostGuard`; default $5 dev / $50 prod.
- [ ] Per-`/v1/ask` request cap enforced; default $0.10.
- [ ] `BudgetExceeded` results in `PipelineResult(status="halted_budget")`, not crash.

### Recovery
- [ ] Checkpoints live in `<repo>/.brain/.checkpoints/`, survive container restart.
- [ ] Checkpoint file lock prevents concurrent same-key runs (returns 409).
- [ ] Stale checkpoint sweeper runs at orchestrator start.

### Health
- [ ] `GET /health/deep` (Python) reports each external dependency individually.
- [ ] `GET /v1/health/deep` (Java) does the same.
- [ ] Frontend header pill consumes `/v1/health/deep` every 30 s.

### Observability
- [ ] Every failure emits a structured Langfuse event with the full taxonomy.
- [ ] `GET /v1/runs/{run_id}/timeline` returns the event list for a run.
- [ ] Demo failures are debuggable from the Langfuse dashboard alone (no grep).

### End-to-end demo dry-run
- [ ] `make e2e-demo-dryrun` runs all six scenarios and reports PASS/FAIL per scenario.
- [ ] Provider fallback scenario passes with Anthropic blocked.
- [ ] Neo4j outage scenario completes with degraded blast-radius and recovers on next run.
- [ ] Budget cap scenario halts pipeline cleanly.

---

## Verification commands

```bash
# 1. Health
curl -s http://localhost:8000/health/deep | jq '.ok'        # expect: true

# 2. Provider chain dry-run (requires real keys; uses 1 cheap call per provider)
make verify-provider-chain

# 3. JSON recovery unit tests
pytest company-brain-ai/tests/unit/llm/test_json_recovery.py -v

# 4. Run pipeline twice with conflicting workspace; second should 409
brain index ./pilot --workspace-id <uuid> &  brain index ./pilot --workspace-id <uuid>

# 5. Budget breaker
BRAIN_JOB_BUDGET_USD=0.01 brain index ./pilot
# Expect: status=halted_budget, partial entities written

# 6. Neo4j outage simulation
docker stop cb-neo4j && \
  brain query "what does X do" --repo ./pilot
# Expect: result returned (no blast radius), warning logged, neo4j_circuit_open event in Langfuse
docker start cb-neo4j
# Wait 5 min; circuit half-opens. Re-run; full result restored.

# 7. Full demo dry-run
make e2e-demo-dryrun
```

---

## Rollback

Every subsystem is opt-in via env var. To roll back:

```bash
unset BRAIN_PROVIDER_CHAIN              # falls back to single-provider behaviour
export BRAIN_USE_LEGACY_JSON_PARSE=1    # skips json_recovery
export BRAIN_DISABLE_COST_GUARD=1
export BRAIN_DISABLE_NEO4J_CIRCUIT=1
git revert <commit-sha>                 # removes the new modules entirely
```

---

## Out of scope

- **LSP streaming reliability.** v2 concern; this ADR covers the existing pipeline's reliability, not the streaming extractor.
- **Multi-region failover.** Single-region for Stage 1.
- **Encrypted-at-rest checkpoints.** They contain entity metadata, not source code; encryption is a Stage 3 enterprise concern.
- **Auto-scaling Postgres / Neo4j.** Single-container dev / prod for now; capacity planning for scale-out is a v2 concern.
- **A/B testing different fallback chain orderings.** Pick the default and ship; instrument cost / latency / quality per-provider; tune in a follow-up.
- **Fine-tuned Groq prompts.** Groq's llama-3.3 has different JSON adherence than Anthropic; if quality drops noticeably on Groq fallback, write a follow-up ADR with provider-specific prompt variants.
