"""ADR-0061 E1 — ExplorationAgent.

A small tool-using sub-agent that fires when SmartZoneAssembler cannot answer
a question with high confidence (sparse zone or initial Sonnet confidence
< 0.6). Mirrors what Claude Code does when its initial context is insufficient:
glob + grep + read + reason, with a strict step cap.

Design points:

* The agent uses a *new* ToolRegistry instance with a curated whitelist of six
  tools (glob_files, grep_code, read_file, query_brain, list_callers,
  read_git_blame). That keeps the principle of least privilege from
  base_agent.py intact and prevents the agent from accidentally invoking
  heavy harness tools.
* All tools are pure read-only and synchronous. They are safe to run
  unattended.
* The agent caps itself at MAX_STEPS=8 tool calls (cost-cap of ~$0.01 per
  hard query as specified in the ADR). When the cap is hit we return whatever
  the model has produced and surface that fact in telemetry.
* Output is free-form text; the caller is responsible for merging it back
  into the QueryResponse summary. We do **not** synthesize JSON here —
  /query already owns that contract.

The agent intentionally does not subclass AgentLoop: its tool surface is
disjoint from the rest of the agent fleet, so building the registry inline
is cleaner than registering six more tools globally.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.llm.base import ToolCall, ToolDefinition, ToolParameter

log = structlog.get_logger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

MAX_STEPS = 8                    # ADR-0061: hard cap on tool invocations.
MAX_GLOB_RESULTS = 40            # don't flood the model with thousands of paths
MAX_GREP_RESULTS = 60
DEFAULT_FILE_CAP = 8000          # chars returned to the model from one read
LOW_CONFIDENCE_THRESHOLD = 0.6   # below this, /query should fire the agent

_SYSTEM_PROMPT = """\
You are ExplorationAgent — a code-archaeology sub-agent for the company-brain
graph. The brain's primary retrieval (SmartZoneAssembler) could not answer the
user's question with high confidence, so you have been spawned with six
read-only tools and a strict {max_steps}-step budget. Your job is to FIND the
answer in source code, the brain graph, and git history, then return a
single, citation-rich final answer.

Tools (all read-only):
  • glob_files   — list files matching a glob (e.g. "**/*.kt", "src/**/api/*.go")
  • grep_code    — ripgrep-style content search; returns matches with file:line
  • read_file    — read a file (or byte range) from disk
  • query_brain  — recursively ask the company-brain pipeline a sub-question
  • list_callers — graph: who calls this method/function URN
  • read_git_blame — git blame summary for a file (last touchers, dates)

Rules:
  1. Plan in your head, then call ONE tool at a time. Read its result before
     deciding the next call.
  2. Prefer narrow tools first (grep + glob) over wide ones (read_file).
  3. Cite every concrete claim with a file:line or URN. Do not invent paths
     that grep_code / glob_files did not return.
  4. When you are confident, STOP calling tools and emit the final answer as
     plain prose. Do not wrap it in JSON or markdown fences.
  5. If after {max_steps} steps you still cannot answer, state what you found
     and what is still ambiguous — partial answers are useful.

Repo root: {repo_root}
Workspace: {workspace_id}
"""


# ── Result envelope ───────────────────────────────────────────────────────────

@dataclass
class ExplorationResult:
    """Output of a single ExplorationAgent.run() invocation."""
    text: str = ""
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    capped: bool = False                       # True if hit MAX_STEPS
    error: Optional[str] = None                # set on hard failure


# ── Tools ─────────────────────────────────────────────────────────────────────

_SKIP_DIRS = {".git", ".brain", "node_modules", "target", "build", "dist",
              ".idea", ".gradle", "__pycache__", ".venv", "venv"}


def _glob_files(repo_root: str, pattern: str, limit: int = MAX_GLOB_RESULTS) -> list[str]:
    """List files matching a glob, relative to repo root."""
    root = Path(repo_root)
    if not root.is_dir():
        return []
    out: list[str] = []
    try:
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            out.append(str(p.relative_to(root)))
            if len(out) >= limit:
                break
    except (OSError, ValueError) as e:
        log.debug("exploration.glob_files failed", error=str(e))
    return out


def _grep_code(repo_root: str, pattern: str, glob: str = "",
               limit: int = MAX_GREP_RESULTS) -> list[dict]:
    """Ripgrep-style search; returns [{file, line, text}, ...]."""
    cmd = ["rg", "--line-number", "--no-heading", "--max-count", "5",
           "--max-columns", "300", "-S"]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, repo_root])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("exploration.grep_code failed", error=str(e))
        return []
    results: list[dict] = []
    for line in out.stdout.splitlines()[:limit]:
        parts = line.split(":", 2)
        if len(parts) >= 3:
            try:
                results.append({
                    "file": _rel(parts[0], repo_root),
                    "line": int(parts[1]),
                    "text": parts[2].strip()[:240],
                })
            except ValueError:
                continue
    return results


def _read_file(path: str, repo_root: str, max_chars: int = DEFAULT_FILE_CAP,
               start_line: int = 1, end_line: int = 0) -> str:
    """Read a file (or line range). The agent is told to ask for ranges when
    the file is large; we still cap at max_chars as a defensive backstop."""
    abs_path = Path(path)
    if not abs_path.is_absolute() and repo_root:
        abs_path = Path(repo_root) / path
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return f"ERROR: could not read {path}: {e}"
    if start_line > 1 or end_line > 0:
        lines = content.splitlines()
        lo = max(0, start_line - 1)
        hi = end_line if end_line > 0 else len(lines)
        content = "\n".join(lines[lo:hi])
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n// ... (truncated at {max_chars} chars; ask for a smaller line range)"
    return content


async def _query_brain(workspace_id: str, repo_path: Optional[str],
                       sub_question: str) -> str:
    """Recursive call into POST /query for a focused sub-question.

    This is intentionally cheap and *cannot* itself fire E1 again — the
    nested invocation passes ``no_iterative=True`` so we don't recurse into
    an infinite exploration loop. The brain query API tolerates the unknown
    flag, but we also defensively cap recursion via depth tracking on the
    HTTP layer.
    """
    try:
        # Import here to avoid a hard dependency at module import time.
        from companybrain.api.routes.query import query_graph
        from companybrain.models.entities import QueryRequest

        req = QueryRequest(
            question=sub_question,
            workspace_id=workspace_id,
            repo_path=repo_path,
            include_unverified=False,
        )
        # Mark the request as a nested call so /query skips iterative recursion.
        try:
            setattr(req, "_no_iterative", True)  # noqa: SLF001 (intentional flag)
        except Exception:
            pass
        resp = await query_graph(req)
        return resp.summary or resp.raw_markdown or ""
    except Exception as e:
        log.debug("exploration.query_brain failed", error=str(e))
        return f"ERROR: query_brain failed: {e}"


async def _list_callers(workspace_id: str, urn: str) -> list[dict]:
    """Graph traversal — who calls this URN. Uses the existing tRPC structural
    tool when the MCP backend is reachable; falls back to empty on error."""
    try:
        from companybrain.mcp.tools.structural_v2 import find_callers as sv2_find_callers
        from companybrain.mcp.trpc_client import TrpcClient
        trpc_url = os.environ.get("TRPC_API_URL", "http://cb-api:8090/trpc")
        trpc = TrpcClient(base_url=trpc_url)
        try:
            data = await sv2_find_callers(scope=workspace_id, symbol=urn, trpc=trpc)
        finally:
            await trpc.close()
        callers = data.get("callers") or []
        return [{"urn": c.get("urn", ""), "name": c.get("name", "")} for c in callers[:20]]
    except Exception as e:
        log.debug("exploration.list_callers failed", error=str(e))
        return []


def _read_git_blame(repo_root: str, file_path: str,
                    start_line: int = 1, end_line: int = 0) -> list[dict]:
    """Return a compact blame summary: distinct authors + last-touched dates."""
    abs_path = Path(file_path)
    if not abs_path.is_absolute() and repo_root:
        abs_path = Path(repo_root) / file_path
    if not abs_path.exists():
        return []
    cmd = ["git", "-C", str(abs_path.parent), "log",
           "--pretty=format:%h|%an|%ad|%s", "--date=short", "--", str(abs_path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    entries: list[dict] = []
    for line in out.stdout.splitlines()[:30]:
        parts = line.split("|", 3)
        if len(parts) == 4:
            entries.append({
                "commit": parts[0], "author": parts[1],
                "date": parts[2], "subject": parts[3][:120],
            })
    return entries


# ── ToolDefinition builders ──────────────────────────────────────────────────

def _tool_defs() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="glob_files",
            description=(
                "List files in the repository matching a glob pattern (relative paths). "
                "Use to discover where a kind of file lives (e.g. '**/*.proto')."
            ),
            parameters=[
                ToolParameter("pattern", "string", "Glob pattern, e.g. '**/*.kt'."),
                ToolParameter("limit", "integer", "Max results (default 40).",
                              required=False),
            ],
        ),
        ToolDefinition(
            name="grep_code",
            description=(
                "Ripgrep across the repository. Returns matches as "
                "{file, line, text}. Use a glob to scope (e.g. '*.java')."
            ),
            parameters=[
                ToolParameter("pattern", "string", "Regex or literal to search."),
                ToolParameter("glob", "string", "Optional file glob, e.g. '*.py'.",
                              required=False),
            ],
        ),
        ToolDefinition(
            name="read_file",
            description=(
                "Read a file (or a line range) from disk. Pass relative path "
                "from the repo root, optional start_line/end_line."
            ),
            parameters=[
                ToolParameter("path", "string", "Relative or absolute path."),
                ToolParameter("start_line", "integer", "1-indexed first line.",
                              required=False),
                ToolParameter("end_line", "integer",
                              "1-indexed last line (0 = end of file).",
                              required=False),
            ],
        ),
        ToolDefinition(
            name="query_brain",
            description=(
                "Recursively ask the company-brain graph a focused sub-question. "
                "Use when you need a structured answer for a smaller piece of "
                "the puzzle. Will NOT itself trigger another exploration agent."
            ),
            parameters=[
                ToolParameter("sub_question", "string", "Plain English question."),
            ],
        ),
        ToolDefinition(
            name="list_callers",
            description=(
                "Graph traversal: list direct callers of a method/function URN."
            ),
            parameters=[
                ToolParameter("urn", "string", "Entity URN, e.g. 'urn:cb:method:...'."),
            ],
        ),
        ToolDefinition(
            name="read_git_blame",
            description=(
                "Compact git blame for a file — last authors, dates, commit "
                "subjects. Use to answer 'who/when' questions."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Path relative to repo root."),
            ],
        ),
    ]


# ── Agent ─────────────────────────────────────────────────────────────────────

class ExplorationAgent:
    """Tool-using sub-agent invoked by /query on hard questions.

    Usage:
        agent = ExplorationAgent(workspace_id="...", repo_path="/abs/path")
        result = await agent.run("which 4 places use a literal 'lob'?")
    """

    ROLE: TaskRole = TaskRole.BALANCED          # Sonnet — needs reasoning quality.

    def __init__(
        self,
        *,
        workspace_id: str,
        repo_path: Optional[str] = None,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.workspace_id = workspace_id
        self.repo_root = self._resolve_repo_root(repo_path)
        self.max_steps = max_steps
        self._provider = get_provider()
        self._tools = _tool_defs()
        self._dispatch: dict[str, Callable] = {
            "glob_files":      self._t_glob,
            "grep_code":       self._t_grep,
            "read_file":       self._t_read,
            "query_brain":     self._t_query_brain,
            "list_callers":    self._t_list_callers,
            "read_git_blame":  self._t_blame,
        }

    async def run(self, question: str) -> ExplorationResult:
        """Drive the ReAct loop. Returns the final text and call telemetry."""
        system = _SYSTEM_PROMPT.format(
            max_steps=self.max_steps,
            repo_root=self.repo_root or "(unknown)",
            workspace_id=self.workspace_id,
        )
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=question),
        ]
        result = ExplorationResult()

        for step in range(1, self.max_steps + 1):
            result.steps = step
            try:
                response = await self._provider.chat_with_tools(
                    messages=messages,
                    tools=self._tools,
                    role=self.ROLE,
                    max_tokens=1500,
                )
            except Exception as e:
                log.warning("exploration_agent.chat_failed",
                            step=step, error=str(e))
                result.error = str(e)
                break

            if not response.wants_tool_call:
                result.text = (response.content or "").strip()
                log.info("exploration_agent.done",
                         steps=step, output_len=len(result.text))
                return result

            messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                tool_text = await self._execute(tc)
                result.tool_calls.append({
                    "step": step,
                    "tool": tc.name,
                    "args": tc.arguments,
                    "preview": tool_text[:160],
                })
                messages.append(ChatMessage(
                    role="tool",
                    content=tool_text,
                    tool_call_id=tc.call_id,
                ))

        # Step cap hit — salvage the last assistant text we saw.
        result.capped = True
        last = next(
            (m.content for m in reversed(messages)
             if m.role == "assistant" and m.content),
            "",
        )
        result.text = last.strip()
        log.info("exploration_agent.capped",
                 steps=result.steps, salvaged_len=len(result.text))
        return result

    # ── tool dispatch (sync wrappers serialise to text for the LLM) ────────

    async def _execute(self, tc: ToolCall) -> str:
        fn = self._dispatch.get(tc.name)
        if fn is None:
            return json.dumps({"error": f"unknown tool: {tc.name}"})
        try:
            output = await fn(**(tc.arguments or {}))
        except TypeError as e:
            return json.dumps({"error": f"bad arguments: {e}"})
        except Exception as e:
            log.warning("exploration_agent.tool_failed",
                        tool=tc.name, error=str(e))
            return json.dumps({"error": str(e)})
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False, default=str)

    async def _t_glob(self, pattern: str, limit: int = MAX_GLOB_RESULTS) -> list[str]:
        if not self.repo_root:
            return []
        return _glob_files(self.repo_root, pattern, limit=limit)

    async def _t_grep(self, pattern: str, glob: str = "") -> list[dict]:
        if not self.repo_root:
            return []
        return _grep_code(self.repo_root, pattern, glob=glob)

    async def _t_read(self, path: str, start_line: int = 1, end_line: int = 0) -> str:
        return _read_file(path, self.repo_root or "",
                          max_chars=DEFAULT_FILE_CAP,
                          start_line=start_line, end_line=end_line)

    async def _t_query_brain(self, sub_question: str) -> str:
        return await _query_brain(self.workspace_id, self.repo_root, sub_question)

    async def _t_list_callers(self, urn: str) -> list[dict]:
        return await _list_callers(self.workspace_id, urn)

    async def _t_blame(self, file_path: str) -> list[dict]:
        if not self.repo_root:
            return []
        return _read_git_blame(self.repo_root, file_path)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_repo_root(repo_path: Optional[str]) -> Optional[str]:
        if repo_path and Path(repo_path).is_dir():
            return str(Path(repo_path).resolve())
        env = os.environ.get("BRAIN_REPO_ROOT") or os.environ.get("BRAIN_ROOT")
        if env and Path(env).is_dir():
            return str(Path(env).resolve())
        return None


def _rel(path: str, root: str) -> str:
    """Best-effort: render absolute path relative to root for readability."""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return path


# ── Public helper for /query ──────────────────────────────────────────────────

def should_fire(initial_confidence: float, zone_tokens_used: int,
                low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD) -> bool:
    """Decision rule for whether /query should invoke the agent.

    Fires when:
      - initial Sonnet confidence < threshold, OR
      - the smart-zone produced essentially no context (< 200 tokens).
    """
    if zone_tokens_used < 200:
        return True
    return initial_confidence < low_confidence_threshold
