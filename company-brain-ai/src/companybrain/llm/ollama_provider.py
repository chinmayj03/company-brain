"""
OllamaProvider — local LLM inference via Ollama.

Ollama exposes an OpenAI-compatible API at /v1/chat/completions,
so we use the openai SDK pointed at the Ollama host.

Model assignment per task role:
  FAST        → llama3.1:8b           (fast, cheap, good at structured JSON)
  BALANCED    → deepseek-coder-v2:16b (code-aware, relationship extraction)
  SYNTHESIS   → deepseek-r1:14b       (best local reasoning, context synthesis)
  REASONING   → deepseek-r1:14b       (gap detection, conflict analysis)
  QUERY       → deepseek-r1:14b       (user-facing answers)

All models must already be pulled into Ollama.
Run: docker compose -f docker-compose.infra.yml --profile pull-models up model-puller

Model override: set OLLAMA_MODEL_<ROLE>=<model> in the environment.
  e.g. OLLAMA_MODEL_SYNTHESIS=deepseek-r1:32b
"""

from __future__ import annotations

import os

import httpx
import structlog

from companybrain.llm.base import (
    LLMProvider, TaskRole, ChatMessage, ChatResponse,
    ToolDefinition, ToolCall,
)

log = structlog.get_logger(__name__)

# Context window passed to Ollama on every request.
# Navigator classify prompt: ~1500 tokens input + ~600 tokens output = ~2100 needed.
# Entity extraction prompt:  ~1200 tokens input + ~800 tokens output = ~2000 needed.
# 3072 comfortably covers both without requiring a large KV-cache allocation.
# If you see HTTP 500 from Ollama, reduce: OLLAMA_NUM_CTX=2048
# If you need more context: OLLAMA_NUM_CTX=6144
_NUM_CTX: int = int(os.environ.get("OLLAMA_NUM_CTX", "3072"))

# Request timeout — local inference can be slow, especially first load.
# qwen2.5-coder:7b classify call on a Mac CPU: ~90-150s. Set to 300 for headroom.
# Override: OLLAMA_TIMEOUT=600
_REQUEST_TIMEOUT: float = float(os.environ.get("OLLAMA_TIMEOUT", "300"))

# Default model per role — override via environment variables
_DEFAULT_MODELS: dict[TaskRole, str] = {
    TaskRole.FAST:      "llama3.1:8b",
    TaskRole.BALANCED:  "deepseek-coder-v2:16b",
    TaskRole.SYNTHESIS: "deepseek-r1:14b",
    TaskRole.REASONING: "deepseek-r1:14b",
    TaskRole.QUERY:     "deepseek-r1:14b",
}

# Env var overrides: OLLAMA_MODEL_FAST, OLLAMA_MODEL_SYNTHESIS, etc.
_ENV_OVERRIDES: dict[TaskRole, str] = {
    role: os.environ[f"OLLAMA_MODEL_{role.value.upper()}"]
    for role in TaskRole
    if f"OLLAMA_MODEL_{role.value.upper()}" in os.environ
}


class OllamaProvider(LLMProvider):
    """
    LLM provider backed by a locally running Ollama instance.
    No API keys required. No external network calls for inference.

    Model availability is checked lazily on the first chat() call.
    Any role whose configured model isn't pulled in Ollama is automatically
    remapped to the best available fallback (llama3.1:8b → llama3:8b → mistral:7b).
    """

    # Candidate fallback models in preference order
    _FALLBACK_CANDIDATES = ["llama3.1:8b", "llama3:8b", "mistral:7b", "phi3:mini"]

    def __init__(self, host: str = "http://localhost:11434"):
        self._host = host.rstrip("/")
        self._model_overrides: dict[TaskRole, str] = dict(_ENV_OVERRIDES)
        self._models_checked = False
        # Persistent client — reused across all calls to avoid TCP handshake overhead
        # and keep Ollama's keep-alive connection warm between requests.
        self._client = httpx.AsyncClient(
            base_url=self._host,
            timeout=_REQUEST_TIMEOUT,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )
        log.info("OllamaProvider initialised", host=host, num_ctx=_NUM_CTX, model_overrides=_ENV_OVERRIDES)

    @property
    def provider_name(self) -> str:
        return "ollama"

    def model_for_role(self, role: TaskRole) -> str:
        return self._model_overrides.get(role, _DEFAULT_MODELS[role])

    async def _check_and_patch_models(self) -> None:
        """
        On first call: list what's available in Ollama and remap any unpulled
        model to the best available fallback. Runs at most once per provider instance.
        """
        if self._models_checked:
            return
        self._models_checked = True

        try:
            available = set(await self.list_available_models())
            if not available:
                log.warning("Ollama returned empty model list — skipping model check")
                return


            # Find the best fallback
            fallback = next(
                (m for m in self._FALLBACK_CANDIDATES if m in available),
                None,
            )

            for role in TaskRole:
                configured = self.model_for_role(role)
                if configured not in available:
                    if fallback:
                        log.warning(
                            "Model not pulled — remapping to fallback",
                            role=role.value,
                            requested=configured,
                            fallback=fallback,
                        )
                        self._model_overrides[role] = fallback
                    else:
                        log.error(
                            "Model not pulled and no fallback available",
                            role=role.value,
                            requested=configured,
                            available=sorted(available),
                        )

            log.info(
                "Model check complete",
                available=sorted(available),
                effective_models={r.value: self.model_for_role(r) for r in TaskRole},
            )
        except Exception as e:
            log.warning("Could not check Ollama model availability", error=str(e))

    async def chat(
        self,
        messages: list[ChatMessage],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> ChatResponse:
        """
        Send a chat request to Ollama's NATIVE /api/chat endpoint via httpx.

        We use the native endpoint (not /v1/chat/completions OpenAI-compat) because:
        - The native endpoint reliably honours `options.num_ctx`
        - The OpenAI-compat wrapper silently ignores extra_body options in many
          Ollama versions, causing InternalServerError when prompt > default num_ctx
        """
        # Lazy model availability check — runs once, patches roles as needed
        await self._check_and_patch_models()

        model = self.model_for_role(role)
        log.debug("Ollama chat", model=model, role=role, messages=len(messages), num_ctx=_NUM_CTX)

        payload = {
            "model":    model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream":   False,
            "options": {
                # num_ctx: total context window (prompt + response tokens).
                # MUST be set here; Ollama default is often 2048 which is too small
                # for code extraction prompts (~1200 input + 1024 output = 2224 needed).
                "num_ctx":     _NUM_CTX,
                "num_predict": max_tokens,   # max output tokens
                "temperature": temperature,
                "top_p":       0.9,
                "stop":        [],
            },
        }

        resp = await self._client.post("/api/chat", json=payload)
        if not resp.is_success:
            log.error(
                "Ollama HTTP error",
                status_code=resp.status_code,
                model=model,
                role=role,
                num_ctx=_NUM_CTX,
                max_tokens=max_tokens,
                response_body=resp.text[:600],
            )
        resp.raise_for_status()

        data = resp.json()
        content = data.get("message", {}).get("content", "")

        return ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
        role: TaskRole = TaskRole.BALANCED,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        """
        Native Ollama tool-calling via /api/chat with `tools` field.
        Supported by llama3.1, qwen2.5-coder, mistral-nemo, and others.

        The model may respond with tool_calls (wants to call a tool) or with
        regular content (final answer).  Callers (AgentLoop) handle the loop.
        """
        await self._check_and_patch_models()

        model = self.model_for_role(role)

        # Serialise messages — include tool_calls and tool results for the loop
        raw_messages: list[dict] = []
        for m in messages:
            if m.role == "tool":
                raw_messages.append({
                    "role": "tool",
                    "content": m.content,
                    # Some Ollama versions also accept tool_call_id
                })
            elif m.tool_calls:
                raw_messages.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {
                            "id": tc.call_id or f"call_{tc.name}",
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                        for tc in m.tool_calls
                    ],
                })
            else:
                raw_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model":    model,
            "messages": raw_messages,
            "tools":    [t.to_ollama_schema() for t in tools],
            "stream":   False,
            "options": {
                "num_ctx":     _NUM_CTX,
                "num_predict": max_tokens,
                "temperature": 0.0,    # deterministic for tool routing
                "top_p":       0.9,
                "stop":        [],
            },
        }

        log.debug("Ollama chat_with_tools", model=model, role=role,
                  messages=len(messages), tools=[t.name for t in tools])

        resp = await self._client.post("/api/chat", json=payload)
        if not resp.is_success:
            log.error(
                "Ollama tool-call HTTP error",
                status_code=resp.status_code,
                response_body=resp.text[:600],
                model=model,
            )
        resp.raise_for_status()

        data = resp.json()
        msg  = data.get("message", {})
        content = msg.get("content", "")

        # Parse tool_calls from the response
        raw_tool_calls = msg.get("tool_calls", [])
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json as _json
                try:
                    args = _json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append(ToolCall(
                name=fn.get("name", ""),
                arguments=args,
                call_id=tc.get("id", f"call_{fn.get('name', 'unknown')}"),
            ))

        return ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            tool_calls=tool_calls,
        )

    async def list_available_models(self) -> list[str]:
        """List models currently pulled in Ollama."""
        resp = await self._client.get("/api/tags")
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]

    async def check_models_ready(self) -> dict[str, bool]:
        """
        Check which pipeline models are available in Ollama.
        Useful at startup to warn the user if a required model is missing.
        """
        available = set(await self.list_available_models())
        needed = set(self.model_for_role(role) for role in TaskRole)
        return {model: model in available for model in needed}
