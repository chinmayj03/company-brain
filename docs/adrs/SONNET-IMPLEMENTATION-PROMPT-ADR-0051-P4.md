# Implementation Prompt — ADR-0051 Phase 4 (hooks + permissions + streaming + introspection)

**Single-PR Claude Code session. ~7 days. Biggest phase — adds the user-visible surface: hooks at 9 events, per-tool permission model, TodoList over SSE, auto-compaction, status line, session resume, per-tool-call cost, brain diff.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` §"Phase 4" + ADR-0052's adoption table for features 5,6,7,8,17,21,22,25,31,34,A5,A6,A7,A9,A11,A15.
2. Verify P3 is on `main`:
   ```bash
   git log --oneline main | head -50 | grep -q "ADR-0051 P3" || exit 1
   ```
3. `git checkout -b feature/adr-0051-p4-hooks-streaming`.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
src/companybrain/harness/hooks.py
src/companybrain/harness/permissions.py
src/companybrain/harness/progress.py
src/companybrain/harness/compaction.py
src/companybrain/harness/session.py
src/companybrain/harness/cost.py
src/companybrain/api/routes/stream.py            # SSE endpoint
.brain-template/hooks/pre_extraction.sh.example
.brain-template/hooks/post_extraction.sh.example
.brain-template/hooks/on_truncation.sh.example
.brain-template/hooks/pre_storage.sh.example
.brain-template/hooks/post_storage.sh.example
.brain-template/hooks/pre_query.sh.example
.brain-template/hooks/post_query.sh.example
.brain-template/hooks/session_start.sh.example
.brain-template/hooks/session_end.sh.example
docs/FEATURE-INDEX.md
tests/unit/test_hooks.py
tests/unit/test_permissions.py
tests/unit/test_compaction.py
tests/acceptance/test_harness_p4_full.py
```

APPEND-ONLY to:

```
src/companybrain/harness/loop.py             # invoke hooks, track cost, check permissions
src/companybrain/harness/subagent.py         # same
src/companybrain/harness/tools/__init__.py   # tools declare required capabilities
src/companybrain/api/main.py                 # mount /stream route
src/companybrain/cli.py                      # add `brain session list/resume/transcript/tools list`
src/companybrain/config.py                   # tunables (compaction_threshold, hook_timeout_s, etc.)
docs/HARNESS.md                              # add 4 new sections
```

---

## Implementation steps

### 1. `harness/hooks.py`

```python
"""Pipeline hooks. Shell scripts at .brain/hooks/<event>.sh invoked at
defined points with JSON on stdin; their JSON stdout can modify behaviour."""
import asyncio
import json
import os
from pathlib import Path
from typing import Any
import structlog

log = structlog.get_logger(__name__)

EVENTS = (
    "session_start", "pre_extraction", "post_extraction",
    "on_truncation", "pre_storage", "post_storage",
    "pre_query", "post_query", "session_end",
)


async def fire(event: str, payload: dict, *, repo_path: Path,
               timeout_s: int = 30) -> dict:
    if event not in EVENTS:
        raise ValueError(f"Unknown hook event: {event}")
    script = repo_path / ".brain" / "hooks" / f"{event}.sh"
    if not script.exists() or not os.access(script, os.X_OK):
        return {}
    try:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=json.dumps(payload).encode()),
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            log.warning("hook.failed", event=event, stderr=stderr.decode()[:500])
            return {}
        if not stdout.strip():
            return {}
        return json.loads(stdout)
    except (asyncio.TimeoutError, json.JSONDecodeError) as e:
        log.warning("hook.error", event=event, error=str(e))
        return {}
```

### 2. `harness/permissions.py`

```python
"""Per-tool capability declarations × per-workspace grants. Three tiers:
auto / ask / deny."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Capability(str, Enum):
    READ_REPO       = "read_repo"
    READ_BRAIN      = "read_brain"
    WRITE_BRAIN     = "write_brain"
    NETWORK         = "network"
    EXEC_SHELL      = "exec_shell"
    LLM_CALL        = "llm_call"


class Decision(str, Enum):
    AUTO = "auto"     # proceed silently
    ASK  = "ask"      # interactive: prompt; non-interactive: fail unless --yes
    DENY = "deny"


@dataclass
class WorkspaceGrants:
    granted: dict[Capability, Decision]

    @classmethod
    def from_settings(cls, raw: dict) -> "WorkspaceGrants":
        return cls(granted={Capability(k): Decision(v) for k, v in raw.items()})

    def decide(self, required: list[Capability]) -> Decision:
        worst = Decision.AUTO
        for cap in required:
            d = self.granted.get(cap, Decision.ASK)
            if d == Decision.DENY: return Decision.DENY
            if d == Decision.ASK:  worst = Decision.ASK
        return worst


# Default grants: read tools auto; write_brain ask in interactive; network
# auto for known hosts; exec_shell deny by default.
DEFAULT_GRANTS = WorkspaceGrants.from_settings({
    "read_repo":   "auto",
    "read_brain":  "auto",
    "write_brain": "ask",
    "network":     "ask",
    "exec_shell":  "deny",
    "llm_call":    "auto",
})
```

In `harness/tools/__init__.py`, extend the `Tool` class to declare its required capabilities. The dispatcher checks `WorkspaceGrants.decide(tool.requires)` before invoking.

### 3. `harness/progress.py`

```python
"""TodoList — structured task tree updated by sub-agents.
Serialised to SSE stream for the UI."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class TodoItem:
    id: str
    title: str
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    children: list["TodoItem"] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict = field(default_factory=dict)


class TodoList:
    def __init__(self):
        self.root: list[TodoItem] = []
        self._listeners: list = []

    def add(self, item: TodoItem, parent_id: str | None = None):
        if parent_id is None:
            self.root.append(item)
        else:
            self._find(parent_id).children.append(item)
        self._notify("add", item)

    def update(self, id: str, *, status: str | None = None, metadata: dict | None = None):
        item = self._find(id)
        if status:
            item.status = status
            if status == "in_progress" and not item.started_at:
                item.started_at = datetime.utcnow().isoformat(timespec="seconds")
            if status in ("completed", "failed"):
                item.completed_at = datetime.utcnow().isoformat(timespec="seconds")
        if metadata:
            item.metadata.update(metadata)
        self._notify("update", item)

    def subscribe(self, callback):
        self._listeners.append(callback)

    def _notify(self, action, item):
        for cb in self._listeners:
            cb(action, item)

    def _find(self, id):
        # DFS
        def walk(items):
            for i in items:
                if i.id == id: return i
                if (r := walk(i.children)): return r
        return walk(self.root)
```

### 4. `api/routes/stream.py` — SSE endpoint

```python
"""SSE endpoint for live job progress."""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import asyncio, json

from companybrain.harness.session import get_session

router = APIRouter()


@router.get("/pipeline/jobs/{job_id}/stream")
async def stream_progress(request: Request, job_id: str):
    session = get_session(job_id)

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        session.todo.subscribe(lambda action, item: queue.put_nowait({"action": action, "item": item.__dict__}))
        try:
            while True:
                if await request.is_disconnected():
                    break
                ev = await asyncio.wait_for(queue.get(), timeout=15)
                yield f"data: {json.dumps(ev)}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"action\":\"heartbeat\"}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

### 5. `harness/compaction.py`

```python
"""Auto-compact parent harness conversation when it exceeds 80% of context.

Strategy: keep the system prompt + the SpecialistAgent's plan + the most
recent N tool results. Drop completed sub-agent transcripts; replace with
a one-line summary."""
from companybrain.providers import ChatMessage


CONTEXT_LIMIT_TOKENS  = 200_000
COMPACT_THRESHOLD     = 0.80


def needs_compaction(messages: list[ChatMessage], usage_total: int) -> bool:
    return usage_total > int(CONTEXT_LIMIT_TOKENS * COMPACT_THRESHOLD)


def compact(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Keep system + first user + last 10 turns; replace each completed
    sub-agent block with a one-line summary."""
    if len(messages) <= 12:
        return messages
    head = messages[:2]              # system + first user (the task)
    tail = messages[-10:]            # recent context
    middle = messages[2:-10]
    summary = ChatMessage(
        role="user",
        content=f"<compacted>{len(middle)} earlier turns dropped — see job.transcript for details</compacted>",
    )
    return head + [summary] + tail
```

### 6. `harness/session.py`

```python
"""Session management: list / resume / transcript."""
from dataclasses import dataclass, field
from pathlib import Path
import json
from companybrain.harness.progress import TodoList


@dataclass
class Session:
    id: str
    created_at: str
    repo_path: str
    endpoint: str
    method: str
    status: str = "active"
    todo: TodoList = field(default_factory=TodoList)
    transcript: list[dict] = field(default_factory=list)


_SESSIONS: dict[str, Session] = {}


def create(id: str, **kwargs) -> Session:
    s = Session(id=id, **kwargs)
    _SESSIONS[id] = s
    return s


def get_session(id: str) -> Session:
    return _SESSIONS[id]


def list_sessions() -> list[dict]:
    return [{"id": s.id, "created_at": s.created_at, "status": s.status,
             "endpoint": s.endpoint} for s in _SESSIONS.values()]


def save(s: Session, path: Path):
    path.write_text(json.dumps(s.__dict__, default=str))


def load(path: Path) -> Session:
    return Session(**json.loads(path.read_text()))
```

### 7. `harness/cost.py`

```python
"""Per-tool-call cost tracker.

Emitted in the job summary as the `cost.by_tool` breakdown so we can
see 'extract_methods_from_class: $0.012 over 8 calls'."""
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class CostTracker:
    by_tool: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
    }))
    total_cost_usd: float = 0.0

    def add(self, tool: str, *, input_tokens: int, output_tokens: int, cost_usd: float):
        slot = self.by_tool[tool]
        slot["calls"] += 1
        slot["input_tokens"]  += input_tokens
        slot["output_tokens"] += output_tokens
        slot["cost_usd"]      += cost_usd
        self.total_cost_usd   += cost_usd

    def summary(self) -> dict:
        return {
            "total_cost_usd": round(self.total_cost_usd, 4),
            "by_tool": {k: {"calls": v["calls"], "cost_usd": round(v["cost_usd"], 4)}
                        for k, v in sorted(self.by_tool.items(), key=lambda x: -x[1]["cost_usd"])},
        }
```

### 8. CLI status line + `brain tools list` + `brain session …`

Add a click/typer subcommand group in `cli.py` (append-only):

```python
@app.command("session")
def session_cmd(action: str, id: Optional[str] = None):
    """brain session list | resume <id> | transcript <id>"""
    ...

@app.command("tools")
def tools_cmd(action: str = "list"):
    """brain tools list — print the tool registry."""
    from companybrain.harness.tools import TOOL_REGISTRY
    for name, t in TOOL_REGISTRY.items():
        print(f"{name:30s} — {t.description}")
```

For the live status line, use `rich.live.Live` updating a panel with cost/stage/files/ETA.

---

## Acceptance test

```python
@pytest.mark.asyncio
async def test_full_run_with_hooks_streaming_and_permissions(tmp_path, sse_test_client):
    # Install a custom pre_extraction hook that drops *_test.java
    hook = tmp_path / ".brain" / "hooks" / "pre_extraction.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(DROP_TEST_FILES_HOOK)
    hook.chmod(0o755)

    progress = []
    async with sse_test_client.stream(f"/pipeline/jobs/{{job_id}}/stream") as stream:
        result = await run_pipeline_harness(repo=tmp_path, ...)
        async for ev in stream:
            progress.append(ev)
    assert any(e["action"] == "update" for e in progress)
    assert all(not f.endswith("_test.java") for f in result.files_extracted)
    assert result.telemetry["hook_invocations"]["pre_extraction"] == 1
    assert result.telemetry["cost"]["total_cost_usd"] > 0


@pytest.mark.asyncio
async def test_compaction_keeps_plan_after_context_fills():
    result = await run_pipeline_harness(repo="fixtures/synthetic-huge-repo", ...)
    assert result.telemetry["max_context_used"] < 0.85 * 200_000
    assert result.telemetry["compaction_invocations"] >= 1
```

---

## PR description

```
feat(harness): hooks + permissions + streaming + introspection (ADR-0051 P4)

Adds:
- Hooks at 9 events (.brain/hooks/<event>.sh, JSON in/out)
- Per-tool permission model (auto/ask/deny × workspace grants)
- TodoList + SSE streaming on /pipeline/jobs/{id}/stream
- Auto-compaction at 80% context fill (preserves plan + recent context)
- CLI status line with live cost/stage/files/ETA via rich
- brain session list/resume/transcript
- brain tools list (exposes tool registry to user)
- Per-tool-call cost telemetry (cost.by_tool breakdown)
- Brain-diff preview before storage (--yes flag for non-interactive)

After two weeks of green acceptance suite on main: flip BRAIN_USE_HARNESS=true
default and remove the legacy orchestrator stage machine.
```
