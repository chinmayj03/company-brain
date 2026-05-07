"""
KnowledgeNavigatorAgent — Agentic codebase navigation for knowledge extraction.

DESIGN:
  This is a proper tool-calling agent. The LLM navigates the codebase by
  deciding which tools to call, what to look at, and when it has enough context.

  Loop:
    1. LLM receives: endpoint URL + entry file path + available tools
    2. LLM calls tools (read_file, find_class, search_code, ...) to explore
    3. Observations are added to context
    4. LLM continues navigating until it calls `submit_knowledge()`
    5. The structured knowledge is returned

  WHY AGENTIC:
    - A Java Spring app is different from a Python FastAPI app.
      The LLM knows both — we don't need to hardcode either.
    - Hexagonal architecture (ports & adapters) is different from MVC.
      The LLM can navigate either by READING the code.
    - The LLM can decide: "I see a delegate pattern here, let me look at the impl"
      A regex parser cannot reason like that.

  TOOLS AVAILABLE TO THE AGENT:
    read_file(path, start_line, end_line)
      — Read a source file. Use start/end_line for large files.

    find_class(class_name)
      — Find where a class or module is defined in the repo.
        Returns: [{file, package, is_interface, stereotype}]

    search_code(query, file_extension)
      — Grep-style search across the repo. Returns matching lines + file paths.

    find_usages(class_name, method_name)
      — Find all places that call a specific method.
        Returns: [{file, line, context}]

    extract_method(file_path, method_name)
      — Extract one method body with its annotations.

    list_directory(path)
      — List files and directories at a path.

    submit_knowledge(knowledge_json)
      — DONE. Submit the extracted knowledge schema. Terminates the agent loop.

  OUTPUT (language-agnostic universal schema):
    {
      "module_name": "...",
      "module_type": "controller|service|repository|model|...",
      "language": "java|python|typescript|go|...",
      "description": "...",
      "endpoints": [...],
      "dependencies": [...],
      "functions": [...],
      "db_queries": [...],
      "gaps": [...]
    }
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

import structlog

from companybrain.llm.base import ChatMessage, TaskRole
from companybrain.llm import get_provider
from companybrain.pipeline.universal_code_extractor import FileKnowledge, UniversalCodeExtractor, _detect_language

log = structlog.get_logger(__name__)

# ── Agent constants ────────────────────────────────────────────────────────────

MAX_TURNS      = 18   # max agent iterations before forcing submit
              # 12 was too tight for Spring hexagonal apps where RepositoryImpl
              # injects multiple secondary repos (plan, provider, etc.) each
              # needing 1-2 turns to read.  18 covers: entry(1) + service(2) +
              # primary repo(2) + impl(2) + up to 4 secondary repos(8) + submit(1).
MAX_FILE_CHARS = 5000 # max chars returned by read_file per call
MAX_RESULTS    = 8    # max search results

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle", ".idea", ".vscode",
    "test", "tests", "__tests__",
})

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM = """You are a code intelligence agent. Your job is to navigate a software repository
and extract structured knowledge about how a specific API endpoint works.

You have access to these tools. Call them by outputting a JSON action:
{"tool": "tool_name", "args": {...}}

TOOLS:
  read_file         args: {path, start_line (opt), end_line (opt)}
                    Read a source file. For large files, use start/end_line.

  find_class        args: {class_name}
                    Find where a class, interface, or module is defined.
                    Returns: [{file, is_interface, stereotype}]

  search_code       args: {query, extension (opt, default ".java")}
                    Search for a pattern across the repo. Like grep.
                    Returns: [{file, line, text}]

  find_usages       args: {class_name, method_name (opt)}
                    Find all callers of a class or method.
                    Returns: [{file, line, context}]

  extract_method    args: {file_path, method_name}
                    Extract one method body with its surrounding annotations.

  list_directory    args: {path}
                    List contents of a directory.

  submit_knowledge  args: {knowledge}
                    DONE. Submit the extracted knowledge. Terminates the session.
                    knowledge must follow the universal schema (see below).

NAVIGATION STRATEGY:
  1. Start with the entry file. Read the handler method for the target endpoint.
  2. Identify what services/repos/functions it calls.
  3. Find those classes and read their relevant methods.
  4. Continue tracing until you reach actual data access (DB queries, external API
     calls, cache reads) or you run out of budget.
  5. CRITICAL — follow ALL collaborators at every layer. A collaborator is any
     custom class this code holds a reference to and delegates work to. They appear
     as:
       - Fields of any non-primitive type (with or without annotations)
       - Constructor / factory parameters of any custom type
       - Local variables returned from factory methods
       - Superclass types (check the parent class too)
     DO NOT filter by naming convention. A collaborator might be called
     PlanFinder, MetricsAggregator, DataFetcher, QueryHandler, NiqEngine —
     anything. If the class is defined in this codebase (not java.*, javax.*,
     org.springframework.*, lombok.*, etc.) and this code calls methods on it,
     read it. Follow the chain until you reach actual I/O or exhaust your budget.
  6. For each layer, extract: what it does, what data it reads/writes, what it accepts.
  7. Identify gaps: questions a developer couldn't answer from the code alone.
  8. Call submit_knowledge() with the complete schema.

UNIVERSAL KNOWLEDGE SCHEMA (output this via submit_knowledge):
{
  "modules": [
    {
      "file": "relative/path/to/File.java",
      "language": "java|python|typescript|go|...",
      "module_name": "ClassName",
      "module_type": "controller|service|repository|model|utility|middleware",
      "description": "One sentence: what does this module do?",
      "dependencies": [
        {"name": "ClassName", "dep_type": "service|repository|client|cache|queue|database", "how_used": "..."}
      ],
      "functions": [
        {
          "name": "methodName",
          "intent_label": "data_read|data_write|orchestration|side_effect|validation|mixed",
          "description": "Plain English description",
          "data_reads": ["TABLE_or_API"],
          "data_writes": ["TABLE_or_API"],
          "side_effects": []
        }
      ],
      "db_queries": [
        {"method": "...", "query_text": "SQL or ORM method", "operation": "SELECT|INSERT|UPDATE|DELETE", "tables": [], "is_native": false}
      ]
    }
  ],
  "endpoints": [
    {
      "http_method": "GET",
      "path": "/api/v1/...",
      "handler_module": "ControllerName",
      "handler_function": "methodName",
      "description": "What this endpoint does",
      "parameters": [
        {
          "name": "paramName",
          "kind": "path|query|body|header",
          "type": "language type",
          "required": true,
          "default_value": null,
          "purpose": "Plain English: what does this control?",
          "is_multiselect": false,
          "valid_values": [],
          "business_rules": [],
          "data_type_hint": "integer ID|enum|date|UUID|boolean flag|free text|comma-separated list"
        }
      ],
      "response_type": "...",
      "response_description": "What the response contains"
    }
  ],
  "call_chain": ["ControllerClass", "ServiceClass", "RepositoryClass"],
  "gaps": [
    {"question": "What does payerType=ALL mean?", "context": "Parameter has no comment or validation"}
  ]
}

IMPORTANT:
- Work through multiple layers — don't stop at the controller.
- Read the actual repository/data-access layer to find DB queries and table names.
- Use plain English for all descriptions — no Java/Python jargon.
- Emit submit_knowledge() when you have traced all layers (or after 10 tool calls).
- Output ONLY the JSON action, nothing else. One action per turn."""


# ── Tool implementations ───────────────────────────────────────────────────────

class AgentTools:
    """
    Tool implementations available to the KnowledgeNavigatorAgent.
    All tools are language-agnostic (file I/O + search).
    """

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = self.repo_path / path
        try:
            lines = p.read_text(errors="ignore").splitlines()
            if start_line or end_line:
                s = max(0, start_line - 1)
                e = end_line if end_line else len(lines)
                lines = lines[s:e]
            content = "\n".join(lines)
            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + f"\n... (truncated at {MAX_FILE_CHARS} chars)"
            return content or "(empty file)"
        except Exception as ex:
            return f"ERROR: {ex}"

    def find_class(self, class_name: str) -> list[dict]:
        results = []
        seen: set[str] = set()
        for ext in (".java", ".py", ".ts", ".tsx", ".js", ".go", ".kt", ".cs"):
            for f in self.repo_path.rglob(f"*{class_name}{ext}"):
                if any(s in f.parts for s in _SKIP_DIRS):
                    continue
                if str(f) in seen:
                    continue
                seen.add(str(f))
                try:
                    head = f.read_text(errors="ignore")[:800]
                except Exception:
                    head = ""
                stereotype = _detect_stereotype(head)
                is_iface = bool(re.search(r'\b(interface|Protocol|ABC)\b', head[:400]))
                results.append({
                    "file": str(f.relative_to(self.repo_path)),
                    "is_interface": is_iface,
                    "stereotype": stereotype,
                    "language": _detect_language(str(f)),
                })
                if len(results) >= MAX_RESULTS:
                    break
        return results[:MAX_RESULTS]

    def search_code(self, query: str, extension: str = "") -> list[dict]:
        results = []
        try:
            cmd = ["rg", "--line-number", "--no-heading", "--max-count=3"]
            if extension:
                cmd += [f"--glob=*{extension}"]
            cmd += [query, str(self.repo_path)]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines()[:MAX_RESULTS]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    fp = parts[0]
                    if not any(s in Path(fp).parts for s in _SKIP_DIRS):
                        results.append({
                            "file": str(Path(fp).relative_to(self.repo_path)),
                            "line": int(parts[1]),
                            "text": parts[2].strip(),
                        })
        except Exception:
            # Fall back to Python search
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            exts = {extension} if extension else {".java", ".py", ".ts", ".js", ".go"}
            for ext in exts:
                for f in self.repo_path.rglob(f"*{ext}"):
                    if any(s in f.parts for s in _SKIP_DIRS):
                        continue
                    try:
                        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                            if pattern.search(line):
                                results.append({
                                    "file": str(f.relative_to(self.repo_path)),
                                    "line": i,
                                    "text": line.strip(),
                                })
                                if len(results) >= MAX_RESULTS:
                                    return results
                    except Exception:
                        continue
        return results[:MAX_RESULTS]

    def find_usages(self, class_name: str, method_name: str = "") -> list[dict]:
        query = f"{class_name}.{method_name}" if method_name else class_name
        return self.search_code(query)

    def extract_method(self, file_path: str, method_name: str) -> str:
        from companybrain.agents.tools.code_tools import extract_method as _em
        p = file_path if Path(file_path).is_absolute() else str(self.repo_path / file_path)
        result = _em(p, method_name)
        return result or f"Method '{method_name}' not found in {file_path}"

    def list_directory(self, path: str) -> list[str]:
        p = Path(path) if Path(path).is_absolute() else self.repo_path / path
        try:
            items = []
            for item in sorted(p.iterdir()):
                if item.name.startswith(".") or item.name in _SKIP_DIRS:
                    continue
                suffix = "/" if item.is_dir() else ""
                items.append(item.name + suffix)
            return items[:40]
        except Exception as e:
            return [f"ERROR: {e}"]

    def dispatch(self, tool_name: str, args: dict) -> Any:
        """Dispatch a tool call from the agent."""
        dispatch_map = {
            "read_file":      lambda: self.read_file(**args),
            "find_class":     lambda: self.find_class(**args),
            "search_code":    lambda: self.search_code(**args),
            "find_usages":    lambda: self.find_usages(**args),
            "extract_method": lambda: self.extract_method(**args),
            "list_directory": lambda: self.list_directory(**args),
        }
        fn = dispatch_map.get(tool_name)
        if fn:
            return fn()
        return f"Unknown tool: {tool_name}"


# ── Agent ──────────────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Final output from the KnowledgeNavigatorAgent."""
    knowledge: dict                        # universal schema dict
    turns_used: int
    files_visited: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


class KnowledgeNavigatorAgent:
    """
    Agentic codebase navigator. The LLM decides what to read, what to follow,
    and when it has enough context to submit the knowledge schema.

    Language-agnostic: works for Java, Python, TypeScript, Go, etc.
    """

    def __init__(self):
        self._provider = get_provider()

    async def navigate(
        self,
        entry_file: str,
        entry_method: str,
        endpoint: str,
        http_method: str,
        repo_path: str,
        repo_name: str,
    ) -> Optional[AgentResult]:
        """
        Navigate the codebase starting from entry_file/entry_method,
        using an agentic LLM loop to discover all layers.

        Returns an AgentResult with the universal knowledge schema.
        """
        tools = AgentTools(repo_path)

        # Bootstrap: read the entry file to give the agent a starting point
        entry_content = tools.read_file(entry_file)
        language = _detect_language(entry_file)

        # Initial context
        system_msg = ChatMessage(role="system", content=_SYSTEM)
        first_user = ChatMessage(
            role="user",
            content=(
                f"Repository: {repo_name}\n"
                f"Target endpoint: {http_method} {endpoint}\n"
                f"Entry file: {entry_file}\n"
                f"Entry method: {entry_method or '(detect from endpoint)'}\n"
                f"Language: {language}\n\n"
                f"Entry file content:\n```{language}\n{entry_content}\n```\n\n"
                f"Start navigating. What do you want to look at next?\n"
                f"Remember: trace all layers (controller → service → repository/DB).\n"
                f"Output a single JSON action."
            ),
        )

        messages: list[ChatMessage] = [system_msg, first_user]
        tool_call_log: list[dict] = []
        files_visited: list[str] = [entry_file]

        log.info(
            "KnowledgeNavigatorAgent: starting",
            endpoint=endpoint,
            entry_file=Path(entry_file).name,
            repo=repo_name,
        )

        # ── Agent loop ─────────────────────────────────────────────────────────
        for turn in range(MAX_TURNS):
            try:
                response = await self._provider.chat(
                    messages=messages,
                    role=TaskRole.BALANCED,     # deterministic navigation
                    max_tokens=1500,
                    temperature=0.0,
                )
            except Exception as e:
                log.error("KnowledgeNavigatorAgent: LLM call failed", turn=turn, error=str(e))
                break

            llm_text = response.content.strip()

            # Parse the action JSON
            action = _parse_action(llm_text)
            if not action:
                log.warning("KnowledgeNavigatorAgent: could not parse action",
                            turn=turn, preview=llm_text[:200])
                # Add clarification and retry once
                messages.append(ChatMessage(role="assistant", content=llm_text))
                messages.append(ChatMessage(
                    role="user",
                    content='Please output a single JSON action like {"tool": "...", "args": {...}}'
                ))
                continue

            tool_name = action.get("tool", "")
            args = action.get("args", {})

            log.info("KnowledgeNavigatorAgent: tool call",
                     turn=turn, tool=tool_name, args=str(args)[:120])
            tool_call_log.append({"turn": turn, "tool": tool_name, "args": args})

            # ── Termination ────────────────────────────────────────────────────
            if tool_name == "submit_knowledge":
                knowledge = args.get("knowledge", {})
                if isinstance(knowledge, str):
                    try:
                        knowledge = json.loads(knowledge)
                    except Exception:
                        knowledge = {}

                log.info(
                    "KnowledgeNavigatorAgent: knowledge submitted",
                    turns=turn + 1,
                    modules=len(knowledge.get("modules", [])),
                    endpoints=len(knowledge.get("endpoints", [])),
                    gaps=len(knowledge.get("gaps", [])),
                )
                return AgentResult(
                    knowledge=knowledge,
                    turns_used=turn + 1,
                    files_visited=files_visited,
                    tool_calls=tool_call_log,
                )

            # ── Tool dispatch ──────────────────────────────────────────────────
            try:
                observation = tools.dispatch(tool_name, args)
                # Track visited files
                if tool_name in ("read_file", "extract_method"):
                    fp = args.get("path") or args.get("file_path", "")
                    if fp and fp not in files_visited:
                        files_visited.append(fp)
            except TypeError as e:
                observation = f"Tool call error (bad args): {e}"
            except Exception as e:
                observation = f"Tool error: {e}"

            # Serialise observation
            if isinstance(observation, list):
                obs_text = json.dumps(observation, indent=2)
            elif isinstance(observation, dict):
                obs_text = json.dumps(observation, indent=2)
            else:
                obs_text = str(observation)

            # Cap observation length
            if len(obs_text) > 3000:
                obs_text = obs_text[:3000] + "\n... (truncated)"

            # Add turn to messages
            messages.append(ChatMessage(role="assistant", content=llm_text))
            messages.append(ChatMessage(
                role="user",
                content=f"Tool result ({tool_name}):\n{obs_text}\n\nContinue. Output next action."
            ))


        # ── Fallback: LLM ran out of turns ────────────────────────────────────
        log.warning("KnowledgeNavigatorAgent: max turns reached, requesting final submit",
                    turns=MAX_TURNS, endpoint=endpoint)

        messages.append(ChatMessage(
            role="user",
            content=(
                "You have reached the maximum number of navigation turns. "
                "Call submit_knowledge() now with everything you have learned so far. "
                "Even partial knowledge is valuable. Output the submit_knowledge action."
            ),
        ))
        try:
            final_response = await self._provider.chat(
                messages=messages,
                role=TaskRole.BALANCED,
                max_tokens=3000,
                temperature=0.0,
            )
            action = _parse_action(final_response.content)
            if action and action.get("tool") == "submit_knowledge":
                knowledge = action.get("args", {}).get("knowledge", {})
                if isinstance(knowledge, str):
                    try:
                        knowledge = json.loads(knowledge)
                    except Exception:
                        knowledge = {}
                return AgentResult(
                    knowledge=knowledge,
                    turns_used=MAX_TURNS,
                    files_visited=files_visited,
                    tool_calls=tool_call_log,
                )
        except Exception as e:
            log.error("KnowledgeNavigatorAgent: final submit failed", error=str(e))

        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_action(text: str) -> Optional[dict]:
    """Extract the JSON action from the LLM response."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find JSON object in response
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _detect_stereotype(content: str) -> str:
    """Detect Spring/FastAPI/NestJS stereotype from file header."""
    patterns = [
        (r'@RestController|@Controller', "controller"),
        (r'@Service', "service"),
        (r'@Repository', "repository"),
        (r'@Component', "component"),
        (r'@router\.(get|post|put|delete)', "controller"),   # FastAPI
        (r'@Controller\(\)', "controller"),                   # NestJS
        (r'@Injectable\(\)', "service"),                      # NestJS
    ]
    for pattern, label in patterns:
        if re.search(pattern, content):
            return label
    return ""
