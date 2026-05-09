"""
Abstract base class for LLM providers.

All providers expose the same interface so the pipeline code
never needs to know which backend is in use.

Swap providers entirely via the LLM_PROVIDER env var:
  LLM_PROVIDER=ollama      → local models via Ollama (default for dev)
  LLM_PROVIDER=openai      → OpenAI GPT-4o / GPT-4o-mini
  LLM_PROVIDER=anthropic   → Claude Opus / Sonnet / Haiku

--- Tool calling ---
Agents use chat_with_tools() to drive a ReAct-style tool-calling loop.
The provider serialises ToolDefinitions to the backend's native format and
deserialises tool_calls from the response.

Compatible backends:
  Ollama  ≥ 0.3  — native tool calling on llama3.1, qwen2.5-coder, etc.
  OpenAI          — standard function-calling API
  Anthropic       — tool_use content blocks
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import threading

import structlog

_cost_log = structlog.get_logger(__name__)


class TaskRole(str, Enum):
    """
    Named roles in the pipeline. Each role maps to a model.
    Roles are ordered from cheapest/fastest to most capable.
    """
    FAST        = "fast"       # entity extraction (high volume, structured JSON)
    BALANCED    = "balanced"   # agent loops, relationship extraction
    SYNTHESIS   = "synthesis"  # business context synthesis (highest quality)
    REASONING   = "reasoning"  # gap detection / conflict analysis
    QUERY       = "query"      # user-facing query answering


# ── Tool-calling data model ────────────────────────────────────────────────────

@dataclass
class ToolParameter:
    """JSON-Schema description of one parameter of a tool."""
    name: str
    type: str                        # "string" | "integer" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    enum: Optional[list[str]] = None # restrict to known values


@dataclass
class ToolDefinition:
    """
    Describes one tool an agent can call.
    Maps to OpenAI function-calling schema and Ollama tool schema.
    """
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)

    def to_ollama_schema(self) -> dict:
        """Serialise to Ollama's tool schema (OpenAI-compatible)."""
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""
    name: str
    arguments: dict[str, Any]
    call_id: str = ""       # provider-assigned ID (used for multi-call tracking)


@dataclass
class ToolResult:
    """The result of executing a tool call, fed back to the LLM."""
    call_id: str
    name: str
    content: str            # JSON string or plain text


# ── Chat primitives ────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    role: str               # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # set when role == "tool"


@dataclass
class ChatResponse:
    content: str
    model: str
    provider: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tool_call(self) -> bool:
        return len(self.tool_calls) > 0


# ── Cost telemetry ────────────────────────────────────────────────────────────

@dataclass
class LLMCallRecord:
    provider: str
    model: str
    role: str           # TaskRole value
    task: str           # caller-supplied label, e.g. "entity_extraction/CompetitivenessController"
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float     # computed from token counts
    ts: str             # ISO timestamp


_PRICE_TABLE: dict[tuple[str, str], tuple[float, float, float]] = {
    # (provider, model_substring): (input_per_mtok, output_per_mtok, cache_read_per_mtok)
    ("anthropic", "haiku"):    (0.80,  4.00, 0.08),
    ("anthropic", "sonnet"):   (3.00, 15.00, 0.30),
    ("anthropic", "opus"):     (15.0, 75.00, 1.50),
    ("groq", "llama-3.1-8b"): (0.05,  0.08, 0.0),
    ("groq", "llama-4-scout"): (0.11,  0.34, 0.0),
    ("groq", "qwen"):          (0.29,  0.79, 0.0),
    ("openai", "gpt-4o-mini"): (0.15,  0.60, 0.0),
    ("openai", "gpt-4o"):      (2.50, 10.00, 0.0),
}


def compute_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Compute approximate cost in USD from token counts using the price table."""
    model_lower = model.lower()
    input_price = output_price = cache_read_price = None
    for (prov, model_sub), (ip, op, crp) in _PRICE_TABLE.items():
        if prov == provider.lower() and model_sub in model_lower:
            input_price, output_price, cache_read_price = ip, op, crp
            break
    if input_price is None:
        input_price, output_price, cache_read_price = 1.0, 3.0, 0.0
    return (
        (input_tokens / 1_000_000) * input_price
        + (output_tokens / 1_000_000) * output_price
        + (cache_read_tokens / 1_000_000) * cache_read_price
    )


def log_llm_call(
    record: LLMCallRecord,
    *,
    job_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    stage: Optional[str] = None,
) -> None:
    """Emit a structured log, update the run-level UsageTracker, forward to Langfuse,
    and fire-and-forget a write to the llm_call_log table for cost telemetry."""
    import structlog as _sl
    _sl.get_logger(__name__).info(
        "llm_call",
        provider=record.provider,
        model=record.model,
        role=record.role,
        task=record.task,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_creation_tokens=record.cache_creation_tokens,
        extraction_cost_usd=record.cost_usd,
    )
    # Accumulate into run-level tracker
    _usage_tracker.record(record)
    # Forward to Langfuse (no-op if not configured)
    try:
        from companybrain.observability.langfuse_client import get_tracker
        tracker = get_tracker()
        if tracker.enabled:
            tracker.generation(
                trace_id=record.task or "anonymous",
                name=record.task or f"{record.provider}/{record.role}",
                model=record.model,
                prompt_tokens=record.input_tokens,
                completion_tokens=record.output_tokens,
                cost_usd=record.cost_usd,
            )
    except Exception:
        pass   # observability must never crash the pipeline

    # Persist to llm_call_log for make -f Makefile.demo cost queries.
    # Fire-and-forget from the running asyncio event loop (providers call this
    # from within async chat() methods, so a loop is always available).
    import asyncio as _asyncio
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_persist_llm_call_log(record, job_id=job_id,
                                                    workspace_id=workspace_id,
                                                    stage=stage))
    except Exception:
        pass   # never crash the pipeline for telemetry


async def _persist_llm_call_log(
    record: LLMCallRecord,
    *,
    job_id: Optional[str],
    workspace_id: Optional[str],
    stage: Optional[str],
) -> None:
    """Write one row to llm_call_log. Non-fatal — swallows all errors."""
    try:
        import asyncpg
        from companybrain.config import settings

        # Convert postgresql+asyncpg:// URL to plain postgresql:// for asyncpg
        db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """INSERT INTO llm_call_log
                   (workspace_id, job_id, stage, provider, model, role,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, cost_usd)
                   VALUES ($1::uuid, $2, $3, $4, $5, $6,
                           $7, $8, $9, $10, $11)""",
                workspace_id, job_id, stage,
                record.provider, record.model, record.role,
                record.input_tokens, record.output_tokens,
                record.cache_read_tokens, record.cache_creation_tokens,
                record.cost_usd,
            )
        finally:
            await conn.close()
    except Exception:
        pass   # observability never crashes the pipeline


# ── Run-level usage tracker ───────────────────────────────────────────────────

class UsageTracker:
    """
    Accumulates LLM token counts and costs across an entire pipeline run.
    Thread-safe. Call reset() at the start of each run, summary() at the end.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[LLMCallRecord] = []

    def record(self, r: LLMCallRecord) -> None:
        with self._lock:
            self._records.append(r)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()

    def summary(self) -> dict:
        with self._lock:
            records = list(self._records)

        if not records:
            return {
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cost_usd": 0.0,
                "by_model": {},
            }

        total_input      = sum(r.input_tokens        for r in records)
        total_output     = sum(r.output_tokens       for r in records)
        total_cache_read = sum(r.cache_read_tokens   for r in records)
        total_cost       = sum(r.cost_usd            for r in records)

        by_model: dict[str, dict] = {}
        for r in records:
            key = f"{r.provider}/{r.model}"
            if key not in by_model:
                by_model[key] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_model[key]["calls"]             += 1
            by_model[key]["input_tokens"]      += r.input_tokens
            by_model[key]["output_tokens"]     += r.output_tokens
            by_model[key]["cache_read_tokens"] += r.cache_read_tokens
            by_model[key]["cost_usd"]          += r.cost_usd

        # Round cost values for display
        for v in by_model.values():
            v["cost_usd"] = round(v["cost_usd"], 6)

        return {
            "total_calls":             len(records),
            "total_input_tokens":      total_input,
            "total_output_tokens":     total_output,
            "total_cache_read_tokens": total_cache_read,
            "total_cost_usd":          round(total_cost, 6),
            "by_model":                by_model,
        }

    def log_summary(self, log, label: str = "Pipeline") -> None:
        """Print a clean usage summary to the structured logger."""
        s = self.summary()
        log.info(
            "━━━ LLM Usage Summary ━━━",
            label=label,
            total_calls=s["total_calls"],
            total_input_tokens=f"{s['total_input_tokens']:,}",
            total_output_tokens=f"{s['total_output_tokens']:,}",
            total_cache_read_tokens=f"{s['total_cache_read_tokens']:,}",
            total_cost_usd=f"${s['total_cost_usd']:.4f}",
        )
        for model_key, m in s["by_model"].items():
            log.info(
                "  ↳ model breakdown",
                model=model_key,
                calls=m["calls"],
                input_tokens=f"{m['input_tokens']:,}",
                output_tokens=f"{m['output_tokens']:,}",
                cost_usd=f"${m['cost_usd']:.4f}",
            )


# Global singleton — one per process, reset at the start of each pipeline run
_usage_tracker = UsageTracker()


def get_usage_tracker() -> UsageTracker:
    return _usage_tracker


# ── Provider base class ────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    Base class for all LLM providers.
    Concrete implementations: OllamaProvider, OpenAIProvider, AnthropicProvider.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def model_for_role(self, role: TaskRole) -> str: ...

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> ChatResponse: ...

    async def chat_json(
        self,
        messages: list[ChatMessage],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4096,
    ) -> str:
        """Convenience wrapper: chat + strip markdown fences → raw JSON string."""
        response = await self.chat(messages, role=role, max_tokens=max_tokens, temperature=0.0)
        return _strip_code_fence(response.content)

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        """
        Send a chat request that may include tool calls.

        Default implementation falls back to plain chat() — subclasses override
        this to use native tool-calling APIs for better reliability.

        The base fallback asks the model to emit tool calls as JSON in its text
        response, which is less reliable but works on any provider.
        """
        # Build a text description of available tools as a fallback
        tool_descriptions = "\n".join(
            f"- {t.name}({', '.join(p.name for p in t.parameters)}): {t.description}"
            for t in tools
        )
        # Inject tool list into the last user message
        msgs = list(messages)
        if msgs and msgs[-1].role == "user":
            msgs[-1] = ChatMessage(
                role="user",
                content=msgs[-1].content
                + f"\n\nAvailable tools:\n{tool_descriptions}\n"
                + "To call a tool respond with: TOOL_CALL: {\"name\": \"tool_name\", \"arguments\": {...}}",
            )
        return await self.chat(msgs, role=role, max_tokens=max_tokens, temperature=0.0)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_code_fence(text: str) -> str:
    """
    Extract the first JSON object or array from a model response.
    Handles:
      - Pure JSON
      - ```json ... ``` fenced blocks
      - Prose preamble followed by a JSON block (llama3.1:8b habit)
    """
    import re
    text = text.strip()

    # 1. Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()

    # 2. If it starts cleanly with { or [ we're done
    if text.startswith("{") or text.startswith("["):
        return text

    # 3. Find first { or [ in the text (handles prose preamble)
    m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if m:
        return m.group(1).strip()

    return text
