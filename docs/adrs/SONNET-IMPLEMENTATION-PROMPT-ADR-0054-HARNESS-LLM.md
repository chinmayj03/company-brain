# Implementation Prompt — Harness-LLM Provider (zero-cost, full-repo verification)

**This is an extension to ADR-0054. The base verification suite (ADR-0054 main prompt) calls the production Anthropic API on every LLM call — that costs ~$1-3 per full-repo run, which means we tend to verify on a 5-endpoint subset. This addendum adds a second mode where the Claude Code session running the suite IS the LLM provider for the brain pipeline. Zero API spend; verify the full 50-endpoint repo as many times as you want.**

**Single-PR Claude Code session. ~1 day. Lands the harness-LLM provider, the bridge protocol, the polling loop, and the `--harness-llm` flag for the verification suite. The base ADR-0054 PR must be merged first (this builds on its gate scaffolding).**

---

## Why this exists

Two real-world problems with production-mode verification:

1. **Cost-bounded coverage.** Full-repo extraction costs ~$1-3 today. To stay safe, we verify on a 5-endpoint subset. That misses bugs in the other 45 endpoints. Demo-prep iteration also burns budget — 5 verification runs in a day = $15.

2. **Slow iteration loop.** Production-mode runs take 20-40 minutes. If a gate fails and you fix it, you wait 20-40 minutes to re-verify.

Both go away if the LLM compute comes from the Claude Code session itself. Same model (Haiku 4.5). Same prompts. Different billing.

---

## Architecture

The brain's `LLMProvider` abstraction already exists. We add a third provider variant that doesn't call any external API — it writes the prompt to a file and waits for the Claude Code agent (orchestrating the verification run) to drop an answer back.

```
┌──────────────────────┐         file bridge        ┌─────────────────────────┐
│ brain pipeline       │  ──── /tmp/llm-bridge ──→  │ Claude Code agent loop  │
│ (Python orchestrator,│                             │ (the verification       │
│  chunk_extractor,    │  ←── /tmp/llm-bridge  ───  │  session running this)  │
│  context_synth, …)   │                             │                         │
└──────────────────────┘                             │  for each .req.json:    │
        │                                            │    spawn Task subagent  │
        │                                            │    write .resp.json     │
        ▼                                            └─────────────────────────┘
   HarnessLLMProvider.chat()
        ↓
   write request, wait for response
```

Both sides talk via a directory of `<uuid>.req.json` (request) and `<uuid>.resp.json` (response) files. The Python provider blocks on response file existence; the agent polls for new requests and replies via Task subagents.

The agent's subagent system prompt is the brain's actual production system prompt for whichever role made the request — so the same prompt, same model class (Haiku), same parsing. The only differences from the production path: zero billing on the brain's account, and the agent can verify the whole repo in 30-40 minutes from inside one Claude Code session.

---

## Pre-flight

```bash
# Confirm ADR-0054 is merged on main
git log --oneline -50 | grep -q "ADR-0054" || { echo "ADR-0054 not merged; block on it"; exit 1; }
test -d tests/demo/gates || { echo "tests/demo/gates missing — ADR-0054 incomplete"; exit 1; }

git checkout -b feature/adr-0054-harness-llm
```

---

## File ownership for THIS PR

Create / modify exclusively:

```
company-brain-ai/src/companybrain/llm/harness_llm_provider.py   # NEW — the provider
company-brain-ai/src/companybrain/llm/bridge_protocol.py        # NEW — request/response shapes
tests/demo/harness_loop.py                                       # NEW — the agent's polling loop
tests/demo/run_demo_verification.py                              # APPEND-ONLY — add --harness-llm flag
Makefile.demo                                                    # APPEND-ONLY — add `verify-demo-harness` target
```

Append-only edits to:

```
company-brain-ai/src/companybrain/llm/factory.py     # register HarnessLLMProvider when LLM_PROVIDER=harness
company-brain-ai/src/companybrain/config.py          # add llm_bridge_dir setting
```

Do NOT modify any other production code. The harness provider is purely additive.

---

## Implementation

### 1. Bridge protocol (`bridge_protocol.py`)

```python
"""File-based request/response bridge between the brain's LLM provider and
the Claude Code agent acting as the LLM compute source.

Wire format (JSON files in a shared directory):

  <uuid>.req.json   — written by the brain when an LLM call is made:
    {
      "id": "<uuid>",
      "role": "fast" | "balanced" | "synthesis" | "reasoning" | "query",
      "max_tokens": 1200,
      "messages": [
        {"role": "system", "content": "..."},
        {"role": "user",   "content": "..."}
      ],
      "expects_json": true,
      "created_at": "2026-05-11T..."
    }

  <uuid>.resp.json  — written by the agent when the answer is ready:
    {
      "id": "<uuid>",
      "content": "<text response from the model>",
      "stop_reason": "end_turn" | "max_tokens",
      "usage": {"input_tokens": N, "output_tokens": M, "cost_usd": 0.0},
      "completed_at": "2026-05-11T..."
    }

Files are atomic: write to <uuid>.tmp, fsync, rename to final name.
Bridge directory defaults to /tmp/cb-llm-bridge but is configurable.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


DEFAULT_BRIDGE_DIR = Path("/tmp/cb-llm-bridge")


@dataclass
class BridgeRequest:
    id: str
    role: str
    max_tokens: int
    messages: list[dict]
    expects_json: bool
    created_at: str


@dataclass
class BridgeResponse:
    id: str
    content: str
    stop_reason: str
    usage: dict
    completed_at: str


def write_request_atomic(bridge_dir: Path, req: BridgeRequest) -> Path:
    bridge_dir.mkdir(parents=True, exist_ok=True)
    final = bridge_dir / f"{req.id}.req.json"
    tmp = bridge_dir / f"{req.id}.req.tmp"
    tmp.write_text(json.dumps(asdict(req)))
    os.fsync(open(tmp).fileno())
    os.rename(tmp, final)
    return final


def read_response_blocking(bridge_dir: Path, req_id: str,
                            timeout_s: float = 120.0) -> BridgeResponse:
    """Poll the bridge dir until the response file appears or timeout hits."""
    import time
    target = bridge_dir / f"{req_id}.resp.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if target.exists():
            data = json.loads(target.read_text())
            target.unlink()                                   # cleanup after read
            (bridge_dir / f"{req_id}.req.json").unlink(missing_ok=True)
            return BridgeResponse(**data)
        time.sleep(0.1)
    raise TimeoutError(f"No response for request {req_id} within {timeout_s}s")


def list_pending_requests(bridge_dir: Path) -> list[Path]:
    """The agent calls this to find new requests to fulfil."""
    if not bridge_dir.exists():
        return []
    return sorted(bridge_dir.glob("*.req.json"))


def write_response_atomic(bridge_dir: Path, resp: BridgeResponse) -> Path:
    final = bridge_dir / f"{resp.id}.resp.json"
    tmp = bridge_dir / f"{resp.id}.resp.tmp"
    tmp.write_text(json.dumps(asdict(resp)))
    os.fsync(open(tmp).fileno())
    os.rename(tmp, final)
    return final
```

### 2. The harness LLM provider (`harness_llm_provider.py`)

```python
"""HarnessLLMProvider — drop-in replacement for the Anthropic provider that
routes LLM calls through a file bridge to a Claude Code agent.

Usage:
    LLM_PROVIDER=harness  → factory returns this provider.
    Agent must be running tests/demo/harness_loop.py to fulfil requests.
"""
from __future__ import annotations

import datetime
import uuid

from companybrain.config import settings
from companybrain.llm.base import LLMProvider, ChatMessage, TaskRole
from companybrain.llm.bridge_protocol import (
    BridgeRequest, write_request_atomic, read_response_blocking,
    DEFAULT_BRIDGE_DIR,
)


class HarnessLLMProvider(LLMProvider):
    """Drop the LLM call into a file bridge; another agent fulfils it."""

    def __init__(self):
        self._bridge_dir = getattr(settings, "llm_bridge_dir", None) or DEFAULT_BRIDGE_DIR

    async def chat(self, *, messages: list[ChatMessage], role: TaskRole,
                    max_tokens: int, **kwargs) -> str:
        req_id = str(uuid.uuid4())
        req = BridgeRequest(
            id=req_id,
            role=role.value if hasattr(role, "value") else str(role),
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            expects_json=False,
            created_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
        write_request_atomic(self._bridge_dir, req)
        resp = read_response_blocking(self._bridge_dir, req_id)
        return resp.content

    async def chat_json(self, *, messages: list[ChatMessage], role: TaskRole,
                         max_tokens: int, **kwargs) -> str:
        req_id = str(uuid.uuid4())
        req = BridgeRequest(
            id=req_id,
            role=role.value if hasattr(role, "value") else str(role),
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            expects_json=True,
            created_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
        write_request_atomic(self._bridge_dir, req)
        resp = read_response_blocking(self._bridge_dir, req_id)
        return resp.content

    async def chat_with_tools(self, *args, **kwargs):
        """Tool-use is not supported via the harness bridge yet — sub-agents
        can't currently emit structured tool_use blocks via the Task tool.
        Falls back to raising; callers should detect and use chat() instead."""
        raise NotImplementedError(
            "Tool-use harness mode requires the agentic harness (ADR-0051); "
            "use plain chat() for verification."
        )
```

Register in `factory.py`:

```python
def get_provider() -> LLMProvider:
    name = settings.llm_provider.lower()
    if name == "harness":
        from companybrain.llm.harness_llm_provider import HarnessLLMProvider
        return HarnessLLMProvider()
    if name == "anthropic":
        from companybrain.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    # ...existing providers...
```

Add to `config.py`:

```python
llm_bridge_dir: str = "/tmp/cb-llm-bridge"   # BRIDGE_DIR env var
```

### 3. The agent's polling loop (`tests/demo/harness_loop.py`)

This is the loop the Claude Code agent runs. It polls the bridge directory, picks up requests, fulfills them via internal model calls (the agent's own Task subagents), and writes responses.

```python
"""Harness-mode agent loop — the Claude Code session running this becomes
the LLM compute source for the brain pipeline.

Usage from a Claude Code session:

    1. In one terminal: start the brain's verification suite with --harness-llm:
         make -f Makefile.demo verify-demo-harness

    2. In the same Claude Code session that started step 1, the agent
       automatically picks up requests from /tmp/cb-llm-bridge and fulfils
       them via Task subagents.

The agent's job per request:
    - Read the .req.json file
    - Construct the same prompt the production system would have sent
    - Spawn a Task subagent (or do inline reasoning) with that prompt
    - Capture the subagent's reply
    - Write a .resp.json file
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from pathlib import Path

from companybrain.llm.bridge_protocol import (
    BridgeResponse, list_pending_requests, write_response_atomic,
    DEFAULT_BRIDGE_DIR,
)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-dir", default=str(DEFAULT_BRIDGE_DIR))
    parser.add_argument("--idle-timeout-s", type=int, default=600,
                        help="Exit if no requests appear for this long")
    args = parser.parse_args()

    bridge = Path(args.bridge_dir)
    bridge.mkdir(parents=True, exist_ok=True)
    print(f"⤷ Harness loop watching {bridge}")
    print("   The Claude Code agent (this session) is now the LLM provider")
    print("   for any verification job pointed at this bridge directory.")
    print("")
    print("   Waiting for requests…")

    import time
    last_activity = time.monotonic()
    while True:
        pending = list_pending_requests(bridge)
        if pending:
            last_activity = time.monotonic()
            for req_path in pending:
                try:
                    handle_request(bridge, req_path)
                except Exception as e:
                    # Mark failed; the brain side will see a stub response
                    write_failure(bridge, req_path, str(e))
        else:
            if time.monotonic() - last_activity > args.idle_timeout_s:
                print("⤷ Idle timeout — exiting harness loop")
                return
            await asyncio.sleep(0.5)


def handle_request(bridge: Path, req_path: Path) -> None:
    """ATTENTION CLAUDE CODE AGENT:

    When this loop runs and you see a request file appear, your job:

    1. Read the file:
         req = json.loads(req_path.read_text())

    2. The request contains the exact prompt the brain wants you to fulfil.
       It looks like:
         {"role": "balanced", "max_tokens": 1200, "expects_json": true,
          "messages": [{"role":"system","content":"...edge taxonomy + JSON schema..."},
                       {"role":"user","content":"<class_header>...</class_header><method>...</method>"}]}

    3. Spawn a Task subagent using the Task tool with:
         - description: "Brain extraction call for {role}"
         - subagent_type: "general-purpose"
         - prompt: the system + user content concatenated, plus
                   "Return ONLY valid JSON matching the schema in the system prompt."
                   (when expects_json=true)
                   OR
                   "Return ONLY the requested text."
                   (when expects_json=false)

    4. The subagent returns text. Construct a BridgeResponse:
         resp = BridgeResponse(
             id=req["id"],
             content=subagent_output_text,
             stop_reason="end_turn",
             usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
             completed_at=datetime.datetime.utcnow().isoformat() + "Z",
         )

    5. Write the response with write_response_atomic(bridge, resp).

    The brain pipeline blocks on the response file appearing. As soon as
    you write it, the pipeline unblocks and continues.

    NOTE: this function is a placeholder. The agent inspects requests
    interactively. Do NOT try to fulfil this in pure Python — the agent
    needs to use its own model access via the Task tool.
    """
    print(f"  ▶ Pending request: {req_path.name}")
    req = json.loads(req_path.read_text())
    print(f"     role={req['role']}, max_tokens={req['max_tokens']}, "
          f"system_chars={len(req['messages'][0]['content'])}, "
          f"user_chars={len(req['messages'][-1]['content'])}")
    print(f"     ⤷ Use Task tool to fulfil; write response.")
    # Agent picks it up from here.


def write_failure(bridge: Path, req_path: Path, error: str) -> None:
    req = json.loads(req_path.read_text())
    resp = BridgeResponse(
        id=req["id"],
        content=json.dumps({"error": error, "entities": [], "edges": []}),
        stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        completed_at=datetime.datetime.utcnow().isoformat() + "Z",
    )
    write_response_atomic(bridge, resp)


if __name__ == "__main__":
    asyncio.run(main())
```

The trick here is that `harness_loop.py` IS the agent's coordination point — the Claude Code session running it sees the print statements describing each pending request, then uses its Task tool to fulfil each one. The Python loop is the bridge; the agent is the worker.

**To make this fully autonomous** (instead of agent-interactive), the agent can run a script that:
1. Loops over `list_pending_requests`
2. For each, opens the file, reads the prompt
3. Spawns a Task subagent with that exact prompt
4. Captures the subagent's text response
5. Writes the response file

This is exactly the inner Task-tool loop a Claude Code agent does naturally — you don't need to instruct it deeply, just point it at the bridge directory.

### 4. Wire `--harness-llm` into the verification suite

In `tests/demo/run_demo_verification.py`:

```python
parser.add_argument("--harness-llm", action="store_true",
    help="Route LLM calls through /tmp/cb-llm-bridge for zero-cost verification "
         "(the Claude Code session running this becomes the LLM provider).")

# Before running gates, set the env var so the brain pipeline picks up
# the harness provider:
import os
if args.harness_llm:
    os.environ["LLM_PROVIDER"] = "harness"
    os.environ["BRAIN_LLM_BRIDGE_DIR"] = "/tmp/cb-llm-bridge"
    Path("/tmp/cb-llm-bridge").mkdir(parents=True, exist_ok=True)
    print("⤷ HARNESS-LLM MODE: brain LLM calls will be served by the agent.")
    print("   Make sure tests/demo/harness_loop.py is running in another terminal")
    print("   inside the same Claude Code session.")
```

Add `Makefile.demo` target:

```makefile
.PHONY: verify-demo-harness
verify-demo-harness:
	@echo "$(CYAN)→ Demo verification (HARNESS LLM MODE — \$$0 spend)$(NC)"
	@echo "$(YEL)Open another terminal and start the harness loop FIRST:$(NC)"
	@echo "  cd company-brain-ai && .venv/bin/python tests/demo/harness_loop.py"
	@echo ""
	@read -p "Press enter once the harness loop is running…"
	@cd company-brain-ai && .venv/bin/python tests/demo/run_demo_verification.py \
	  --repo $(TARGET_REPO) --workspace $(WORKSPACE_ID) \
	  --report ../demo-verification-report-harness.md \
	  --harness-llm
	@echo "$(GREEN)→ Report: demo-verification-report-harness.md$(NC)"
```

---

## How a Claude Code session uses this end-to-end

The user opens a Claude Code session inside the repo and asks: *"Run the demo verification suite in harness mode against the full repo."*

The agent does:

1. **Terminal A** — Start the harness loop:
   ```bash
   cd company-brain-ai
   .venv/bin/python tests/demo/harness_loop.py
   ```
2. **Terminal B** — Start the verification suite:
   ```bash
   make -f Makefile.demo verify-demo-harness TARGET_REPO=/Users/.../network-iq-backend-java
   ```
3. **Agent's main loop** — Watch terminal A's output. Whenever a `▶ Pending request: <uuid>.req.json` line appears, the agent:
   - Opens the file
   - Reads the prompt (system + user)
   - Spawns a `Task` subagent with that exact prompt
   - Captures the subagent's response text
   - Writes the response back via `bridge_protocol.write_response_atomic`

The brain pipeline calls `LLMProvider.chat()` → blocks on response file → agent fulfils via Task subagent → response file appears → pipeline unblocks → continues. From the brain's perspective it just looks like an LLM provider that's a bit slow; from the agent's perspective it's a queue of small inference jobs.

When all gates finish, the verification suite produces `demo-verification-report-harness.md`. The agent stops the harness loop with Ctrl-C.

**Total Anthropic-API spend: $0.** Brain runs full extraction + 5 query gates + onboarding + reproducibility + MCP smoke-test, all served by the Claude Code session's compute.

---

## Acceptance test

`tests/demo/test_harness_llm_smoke.py`:

```python
"""Smoke test: harness provider serves a request end-to-end."""
import asyncio
import json
import threading
from pathlib import Path

from companybrain.llm.harness_llm_provider import HarnessLLMProvider
from companybrain.llm.base import ChatMessage, TaskRole
from companybrain.llm.bridge_protocol import (
    BridgeResponse, write_response_atomic, list_pending_requests,
    DEFAULT_BRIDGE_DIR,
)


def fake_agent_loop(stop_event):
    """Pretends to be the Claude Code agent — fulfils requests with a stub."""
    import time
    while not stop_event.is_set():
        for req_path in list_pending_requests(DEFAULT_BRIDGE_DIR):
            req = json.loads(req_path.read_text())
            resp = BridgeResponse(
                id=req["id"],
                content='{"entities": [{"type":"Method","name":"test"}]}',
                stop_reason="end_turn",
                usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                completed_at="2026-05-11T00:00:00Z",
            )
            write_response_atomic(DEFAULT_BRIDGE_DIR, resp)
        time.sleep(0.05)


async def test_harness_round_trip():
    stop = threading.Event()
    t = threading.Thread(target=fake_agent_loop, args=(stop,), daemon=True)
    t.start()
    try:
        provider = HarnessLLMProvider()
        out = await provider.chat_json(
            messages=[
                ChatMessage(role="system", content="extract"),
                ChatMessage(role="user", content="<method>foo</method>"),
            ],
            role=TaskRole.BALANCED,
            max_tokens=600,
        )
        data = json.loads(out)
        assert data["entities"][0]["name"] == "test"
    finally:
        stop.set()
```

---

## When to use which mode

| Mode | Cost | Speed | Use case |
|---|---|---|---|
| `verify-demo` (production LLM) | $1-3/run | 25-40 min | Pre-prod regression check; CI nightly |
| `verify-demo-harness` (Claude Code session as LLM) | $0/run | 30-40 min | Demo-prep iteration; full-repo coverage; experimenting with prompts |

The harness mode is NOT a substitute for the production-mode run — it doesn't catch regressions in the Anthropic provider itself, the cache_control wire-up (G1), or rate-limit handling. Run production-mode at least once before the actual investor meeting.

---

## Future extension

Once ADR-0051 P5 (slash commands) lands, this becomes a single command:

```
/verify-demo --harness
```

The slash command spins up both terminals automatically and stops on completion. The current implementation requires the two-terminal setup as a hand-step. That's fine for the seed-stage MVP.

---

## PR description

```
feat(verification): harness-LLM provider for zero-cost full-repo verification (ADR-0054 addendum)

Adds a third LLM provider (HarnessLLMProvider) that routes LLM calls
through a file bridge instead of the Anthropic API. When the verification
suite runs with --harness-llm and the harness_loop.py polling script
runs in a sibling terminal inside the same Claude Code session, the
session's own model access fulfils every request the brain makes.

Result: full-repo demo verification at $0 marginal cost. Whole-repo
coverage no longer budget-bounded; iteration cycle drops from
"verify on 5 endpoints because cost" to "verify on all 50 because free".

Production-mode verification (--no-harness) unchanged — still uses the
Anthropic API for pre-deployment regression checking.

Acceptance: harness round-trip smoke test passes; tests/demo/run_demo_verification.py
--harness-llm completes all 10 gates against fixture repo with zero
external API spend logged in any LLM provider's telemetry.
```
