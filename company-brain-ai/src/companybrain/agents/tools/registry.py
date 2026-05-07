"""
Tool registry — wires ToolDefinitions (schemas for the LLM) to Python callables
(the actual implementations).

Each entry in TOOL_REGISTRY is a (ToolDefinition, async_callable) pair.
AgentLoop uses this to:
  1. Send tool schemas to the LLM so it knows what's available
  2. Dispatch ToolCall objects to the right Python function when the LLM calls one

Adding a new tool: add a function to code_tools.py or git_tools.py, then add a
(ToolDefinition, fn) entry below.  No other changes needed — the agent sees it
automatically on next instantiation.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import structlog

from companybrain.llm.base import ToolDefinition, ToolParameter, ToolCall
from companybrain.agents.tools import code_tools, git_tools

log = structlog.get_logger(__name__)


# ── Registry entries ───────────────────────────────────────────────────────────

# Format: (ToolDefinition, sync_callable)
# All callables must be synchronous — AgentLoop runs them in an executor.

_REGISTRY: list[tuple[ToolDefinition, Callable]] = [

    # ── Code navigation ────────────────────────────────────────────────────────

    (
        ToolDefinition(
            name="read_file",
            description=(
                "Read the source code of any file. Use this to understand "
                "what a class or module does before deciding whether to follow it deeper."
            ),
            parameters=[
                ToolParameter("path", "string", "Absolute path to the file to read"),
                ToolParameter("max_chars", "integer",
                              "Maximum characters to return (default 6000)", required=False),
            ],
        ),
        code_tools.read_file,
    ),

    (
        ToolDefinition(
            name="find_file_by_name",
            description=(
                "Locate a class or module file in the repository by its name. "
                "Returns a list of matching file paths. "
                "Use when you know a class name from an import or call but need the file path."
            ),
            parameters=[
                ToolParameter("class_name", "string",
                              "The class or file name to search for (without extension)"),
                ToolParameter("repo_path", "string",
                              "Absolute path to the repository root"),
                ToolParameter("extension", "string",
                              "File extension to search (default .java)", required=False),
            ],
        ),
        code_tools.find_file_by_name,
    ),

    (
        ToolDefinition(
            name="extract_method",
            description=(
                "Extract a specific method's annotations, signature, and body from a file. "
                "Much cheaper than read_file — returns only the relevant method (~200–800 chars). "
                "Use this once you know which method you need."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the source file"),
                ToolParameter("method_name", "string", "Name of the method to extract"),
            ],
        ),
        code_tools.extract_method,
    ),

    (
        ToolDefinition(
            name="get_class_fields",
            description=(
                "List the injected fields and dependencies of a class. "
                "Useful for discovering what services/repositories a controller or service depends on "
                "without reading the whole file."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the source file"),
            ],
        ),
        code_tools.get_class_fields,
    ),

    (
        ToolDefinition(
            name="get_imports",
            description=(
                "Return the import statements from a Java file as a list of "
                "fully-qualified class names. Use to understand what packages a class depends on."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the source file"),
            ],
        ),
        code_tools.get_imports,
    ),

    (
        ToolDefinition(
            name="find_implementations",
            description=(
                "Find concrete classes that implement a given interface. "
                "Critical for hexagonal architecture, DDD ports, or any inversion-of-control pattern "
                "where you see an interface being called but need the actual implementation."
            ),
            parameters=[
                ToolParameter("interface_name", "string",
                              "The interface name to find implementations of"),
                ToolParameter("repo_path", "string",
                              "Absolute path to the repository root"),
            ],
        ),
        code_tools.find_implementations,
    ),

    (
        ToolDefinition(
            name="search_codebase",
            description=(
                "Search for a keyword or class name across the entire codebase. "
                "Returns file paths and line numbers where the keyword appears. "
                "Use when you have a symbol name but don't know which file it's in."
            ),
            parameters=[
                ToolParameter("keyword", "string", "The keyword or symbol to search for"),
                ToolParameter("repo_path", "string",
                              "Absolute path to the repository root"),
                ToolParameter("file_extension", "string",
                              "File type to search (default .java)", required=False),
                ToolParameter("max_results", "integer",
                              "Maximum results to return (default 20)", required=False),
            ],
        ),
        code_tools.search_codebase,
    ),

    (
        ToolDefinition(
            name="list_methods",
            description=(
                "List all method signatures in a file without reading the full content. "
                "Use to get an overview of what a class does before deciding which method to extract."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the source file"),
            ],
        ),
        code_tools.list_methods,
    ),

    (
        ToolDefinition(
            name="find_entry_handler",
            description=(
                "Find the HTTP handler method for a given endpoint path. "
                "Searches for Spring @GetMapping/@PostMapping, FastAPI @router.get, "
                "Express router.get, etc. Returns the file, class, and method name."
            ),
            parameters=[
                ToolParameter("endpoint", "string", "The API endpoint path (e.g. /api/v1/orders)"),
                ToolParameter("http_method", "string",
                              "HTTP method: GET, POST, PUT, DELETE, PATCH"),
                ToolParameter("repo_path", "string",
                              "Absolute path to the repository root"),
            ],
        ),
        code_tools.find_entry_handler,
    ),

    # ── Git / business context ─────────────────────────────────────────────────

    (
        ToolDefinition(
            name="get_recent_commits",
            description=(
                "Get recent git commits that touched a specific file. "
                "Use to understand the change history and business intent behind a piece of code."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the file"),
                ToolParameter("limit", "integer",
                              "Maximum commits to return (default 10)", required=False),
            ],
        ),
        git_tools.get_recent_commits,
    ),

    (
        ToolDefinition(
            name="get_file_contributors",
            description=(
                "Get the list of engineers who have committed to a file. "
                "Useful for identifying ownership and subject-matter experts."
            ),
            parameters=[
                ToolParameter("file_path", "string", "Absolute path to the file"),
            ],
        ),
        git_tools.get_file_contributors,
    ),

    (
        ToolDefinition(
            name="search_commit_messages",
            description=(
                "Search git commit messages for a keyword across the entire repo. "
                "Use to find when a feature was introduced, or what tickets relate to this code."
            ),
            parameters=[
                ToolParameter("keyword", "string", "Keyword to search in commit messages"),
                ToolParameter("repo_path", "string",
                              "Absolute path to the repository root"),
                ToolParameter("limit", "integer",
                              "Maximum commits to return (default 20)", required=False),
            ],
        ),
        git_tools.search_commit_messages,
    ),
]


# ── Public API ─────────────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Holds ToolDefinitions + their implementations.

    Usage:
        registry = ToolRegistry()                        # all tools
        registry = ToolRegistry(names=["read_file", "extract_method"])  # subset
        definitions = registry.definitions              # send to LLM
        result = await registry.execute(tool_call)      # dispatch
    """

    def __init__(self, names: list[str] | None = None):
        """
        names: optional allowlist. If None, all tools are registered.
        """
        self._tools: dict[str, tuple[ToolDefinition, Callable]] = {}
        for defn, fn in _REGISTRY:
            if names is None or defn.name in names:
                self._tools[defn.name] = (defn, fn)

    @property
    def definitions(self) -> list[ToolDefinition]:
        return [defn for defn, _ in self._tools.values()]

    async def execute(self, tool_call: ToolCall) -> str:
        """
        Dispatch a ToolCall to its implementation and return the result as a string.
        Runs the callable synchronously (tools are I/O-bound, not compute-bound).
        """
        entry = self._tools.get(tool_call.name)
        if not entry:
            return json.dumps({"error": f"Unknown tool: {tool_call.name}"})

        defn, fn = entry
        try:
            log.debug("ToolRegistry: executing", tool=tool_call.name, args=tool_call.arguments)
            result = fn(**tool_call.arguments)
            # Serialise to string for the LLM
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log.error("ToolRegistry: tool execution failed",
                      tool=tool_call.name, error=str(e))
            return json.dumps({"error": str(e)})
