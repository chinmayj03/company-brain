"""Tool registry for the harness loop.

Each tool registers itself via @register_tool. The decorator binds the JSON
schema (delivered to the LLM) to an async handler that the HarnessLoop dispatches
when the model emits a tool_call with that name.

Adding a new tool:
    @register_tool(
        name="my_tool",
        description="What the tool does, in one sentence the model will read.",
        parameters=[
            ToolParameter("arg", "string", "What the arg is for."),
        ],
    )
    async def handler(args: dict[str, Any], context: dict[str, Any]) -> Any:
        ...

The handler returns any JSON-serialisable value; HarnessLoop converts it to
the string content of the tool result message.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from companybrain.llm.base import ToolDefinition, ToolParameter

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


@dataclass
class Tool:
    """One registered harness tool — schema for the model + handler for the harness."""
    definition: ToolDefinition
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    async def invoke(self, args: dict[str, Any], *, context: dict[str, Any]) -> Any:
        return await self.handler(args, context)


TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(
    *,
    name: str,
    description: str,
    parameters: list[ToolParameter] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator: bind a (name, schema, handler) triple into TOOL_REGISTRY.

    Re-registration overwrites the prior entry — fine for hot-reload during
    tests but raises if the new and old handlers come from different modules
    (a sign of an accidental name collision).
    """
    def deco(fn: ToolHandler) -> ToolHandler:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Tool handler {name!r} must be async.")
        existing = TOOL_REGISTRY.get(name)
        if existing is not None and existing.handler.__module__ != fn.__module__:
            raise ValueError(
                f"Tool name collision: {name!r} already registered by "
                f"{existing.handler.__module__}, refusing to overwrite "
                f"from {fn.__module__}."
            )
        TOOL_REGISTRY[name] = Tool(
            definition=ToolDefinition(
                name=name,
                description=description,
                parameters=list(parameters or []),
            ),
            handler=fn,
        )
        return fn
    return deco


# Import every tool module so its @register_tool calls fire at package load.
# Order is irrelevant; each module is independent. P1 tools register the
# concrete pipeline calls (read_file, extract_methods_from_class, ...); the
# P2 spawn_* tools build sub-agent allowlists from those P1 names.
from companybrain.harness.tools import (  # noqa: E402,F401
    discover_routes,
    extract_methods_from_class,
    finalize_brain,
    find_entry_handler,
    glob_files,
    grep_code,
    list_candidate_files,
    read_file,
    spawn_extractor,
    spawn_research,
    spawn_verifier,
    write_to_brain,
)

__all__ = ["TOOL_REGISTRY", "Tool", "register_tool", "ToolHandler"]
