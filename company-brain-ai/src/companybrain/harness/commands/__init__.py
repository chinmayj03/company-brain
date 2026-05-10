"""Slash-command registry + parser (ADR-0052 P5).

The harness exposes a small set of *slash commands* — short user-typed shortcuts
that expand into a longer instruction the agent will follow. Commands are
authored as plain markdown files in this directory with a YAML-style
frontmatter block:

    ---
    name: extract
    description: Run the extraction pipeline for one endpoint.
    args:
      - name: endpoint
        type: string
        required: true
      - name: method
        type: string
        default: GET
    ---
    You are extracting an endpoint. Use the canonical pipeline: ...

The parser recognises a leading ``/<name>`` token in a user message, captures
the remainder as positional arguments matched against the declared ``args``
list, and substitutes ``{arg}`` placeholders into the template body before
handing the prepared message to :class:`HarnessLoop`.

Public surface
--------------

* :class:`SlashCommand`              — one parsed command.
* :class:`SlashCommandRegistry`      — name → command lookup with reload.
* :func:`load_default_commands`      — loader that scans this directory.
* :func:`parse_and_render`           — message → (rendered_message, command_name).
* :exc:`SlashCommandError`           — bad invocation (unknown command, missing arg).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_COMMANDS_DIR = Path(__file__).resolve().parent

# Frontmatter is a fenced YAML block at the top of the file. We hand-parse a
# tiny subset (enough for `name`, `description`, and a list of `args`) so
# slash commands don't pull in PyYAML.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n(.*)$", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_LEADING_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)\b")


class SlashCommandError(ValueError):
    """Raised when a slash invocation is malformed (unknown name, missing arg)."""


@dataclass
class SlashArg:
    name: str
    type: str = "string"
    required: bool = True
    default: Any = None


@dataclass
class SlashCommand:
    """One parsed command — frontmatter metadata + body template."""

    name: str
    description: str
    body: str
    args: list[SlashArg] = field(default_factory=list)
    source_path: Path | None = None

    def render(self, raw_args: str) -> str:
        """Fill the body template from the positional argv string.

        The argv splitter is intentionally simple — whitespace-separated, with
        the *last* declared argument soaking up the remainder so commands like
        ``/explain SomeClass.someMethod with the new flag``  work as
        expected.
        """
        parsed = self._parse_args(raw_args)
        # Always have ``raw_args`` available for templates that prefer the
        # untransformed string (``--dry-run``, etc.).
        parsed.setdefault("raw_args", raw_args.strip())
        try:
            return _PLACEHOLDER_RE.sub(lambda m: str(parsed.get(m.group(1), m.group(0))),
                                        self.body)
        except KeyError as exc:
            raise SlashCommandError(f"missing template variable: {exc}") from exc

    # ── internals ──────────────────────────────────────────────────────────

    def _parse_args(self, raw: str) -> dict[str, Any]:
        tokens = raw.strip().split()
        result: dict[str, Any] = {}
        # Positional fill, with the last declared arg eating any remaining tokens.
        for i, arg in enumerate(self.args):
            if i < len(self.args) - 1:
                if i < len(tokens):
                    result[arg.name] = tokens[i]
                elif arg.required:
                    raise SlashCommandError(
                        f"/{self.name}: missing required arg {arg.name!r}"
                    )
                else:
                    result[arg.name] = arg.default
            else:
                # Last arg — collapse the remainder if there's anything left.
                rest = tokens[i:]
                if rest:
                    result[arg.name] = " ".join(rest)
                elif arg.required:
                    raise SlashCommandError(
                        f"/{self.name}: missing required arg {arg.name!r}"
                    )
                else:
                    result[arg.name] = arg.default
        return result


class SlashCommandRegistry:
    """Name → command table loaded from a directory of ``.md`` files."""

    def __init__(self, commands: dict[str, SlashCommand] | None = None):
        self._cmds: dict[str, SlashCommand] = dict(commands or {})

    @property
    def names(self) -> list[str]:
        return sorted(self._cmds.keys())

    def get(self, name: str) -> SlashCommand | None:
        return self._cmds.get(name)

    def all(self) -> list[SlashCommand]:
        return [self._cmds[n] for n in self.names]

    def add(self, cmd: SlashCommand) -> None:
        self._cmds[cmd.name] = cmd

    @classmethod
    def from_directory(cls, directory: Path | str) -> SlashCommandRegistry:
        directory = Path(directory)
        out: dict[str, SlashCommand] = {}
        if not directory.is_dir():
            log.warning("slash.commands.no_directory", directory=str(directory))
            return cls(out)
        for md in sorted(directory.glob("*.md")):
            try:
                cmd = parse_command_file(md)
            except SlashCommandError as exc:
                log.warning("slash.commands.parse_error",
                            path=str(md), error=str(exc))
                continue
            out[cmd.name] = cmd
        return cls(out)


# ── module-level helpers ───────────────────────────────────────────────────


def load_default_commands() -> SlashCommandRegistry:
    """Load the bundled commands shipped with this package."""
    return SlashCommandRegistry.from_directory(_COMMANDS_DIR)


def parse_command_file(path: Path) -> SlashCommand:
    """Parse a single ``.md`` command file. Raises on malformed frontmatter."""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise SlashCommandError(f"{path.name}: missing YAML frontmatter")
    meta = _parse_simple_yaml(m.group(1))
    body = m.group(2).rstrip() + "\n"

    name = str(meta.get("name") or path.stem).strip()
    description = str(meta.get("description") or "").strip()
    args_raw = meta.get("args") or []
    args = [_arg_from_meta(a, path.name) for a in args_raw]
    return SlashCommand(
        name=name,
        description=description,
        body=body,
        args=args,
        source_path=path,
    )


def parse_and_render(
    user_message: str,
    *,
    registry: SlashCommandRegistry | None = None,
) -> tuple[str, str | None]:
    """Detect a slash command at the head of ``user_message`` and expand it.

    Returns ``(rendered_message, command_name)``. When the message does not
    start with a slash, ``command_name`` is ``None`` and the message passes
    through unchanged.
    """
    registry = registry or load_default_commands()
    stripped = user_message.lstrip()
    m = _LEADING_SLASH_RE.match(stripped)
    if not m:
        return user_message, None
    name = m.group(1)
    raw_args = stripped[m.end():].strip()
    cmd = registry.get(name)
    if cmd is None:
        raise SlashCommandError(
            f"Unknown slash command /{name}. Available: {registry.names}"
        )
    rendered_body = cmd.render(raw_args)
    return rendered_body, cmd.name


# ── tiny YAML subset ───────────────────────────────────────────────────────


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the narrow YAML subset used in command frontmatter.

    Supports:
      * Top-level ``key: value`` pairs.
      * A ``key:`` line followed by a list of ``- name: ...`` blocks; each
        block can have its own ``key: value`` lines.

    A purpose-built parser keeps the dependency surface small and the
    behaviour predictable; PyYAML would happily evaluate ``!!python/object``
    constructors that we have no business honouring inside command files.
    """
    out: dict[str, Any] = {}
    lines = [
        ln.rstrip() for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            out[key] = _coerce_scalar(val)
            i += 1
            continue
        # Empty value → look for an indented list block.
        items: list[dict[str, Any]] = []
        i += 1
        while i < len(lines) and lines[i].startswith(" "):
            block_line = lines[i].lstrip()
            if not block_line.startswith("- "):
                break
            entry: dict[str, Any] = {}
            first_kv = block_line[2:].strip()
            if ":" in first_kv:
                k, _, v = first_kv.partition(":")
                entry[k.strip()] = _coerce_scalar(v.strip())
            i += 1
            # Sub-keys at the deeper indent belong to this entry.
            while i < len(lines) and lines[i].startswith("    "):
                sub = lines[i].lstrip()
                if ":" not in sub:
                    i += 1
                    continue
                k, _, v = sub.partition(":")
                entry[k.strip()] = _coerce_scalar(v.strip())
                i += 1
            items.append(entry)
        out[key] = items
    return out


def _coerce_scalar(v: str) -> Any:
    """Map ``true / false / null / 42`` to Python equivalents; else string."""
    if not v:
        return ""
    low = v.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none"}:
        return None
    if v.lstrip("-").isdigit():
        try:
            return int(v)
        except ValueError:
            return v
    return v.strip("'\"")


def _arg_from_meta(meta: dict[str, Any], filename: str) -> SlashArg:
    name = str(meta.get("name") or "").strip()
    if not name:
        raise SlashCommandError(f"{filename}: arg block missing 'name'")
    return SlashArg(
        name=name,
        type=str(meta.get("type") or "string"),
        required=bool(meta.get("required", True)),
        default=meta.get("default"),
    )


__all__ = [
    "SlashCommand",
    "SlashCommandError",
    "SlashCommandRegistry",
    "SlashArg",
    "load_default_commands",
    "parse_and_render",
    "parse_command_file",
]
