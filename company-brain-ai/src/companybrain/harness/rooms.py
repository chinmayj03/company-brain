"""Multi-pane rooms — typed surfaces sub-agents query (ADR-0052 P5).

Each *room* is a URI scheme that exposes one slice of the workspace. Sub-agents
ask :meth:`Rooms.query` for ``"code:foo.java"`` or ``"db:nodes"``; Rooms picks
the right backend, runs the query, and returns a string the agent can paste
into its tool result.

Initial schemes:

  * ``code:<path>``        — file content via FileCache (when present in the
                              context) or ``Path.read_text``.
  * ``db:<query>``         — runs ``psql``-style SQL through asyncpg if the
                              DATABASE_URL is reachable. Read-only verb check
                              keeps writes from leaking through this surface.
  * ``git:<command>``      — ``git <command>`` against the workspace's repo,
                              e.g. ``git:log -n 5 --oneline``.
  * ``api:<METHOD path>``  — HTTP fetch against the running service. The base
                              URL is read from ``settings["api_base_url"]``
                              (with sensible localhost default).
  * ``docs:<name>``        — markdown / ADR file lookup under ``docs/`` and
                              ``docs/adrs/`` of the repo.
  * ``metrics:<key>``      — last-run telemetry, served from the workspace's
                              ``metrics`` snapshot if loaded; otherwise empty.

Each handler is async + bounded (output capped at ``_MAX_OUTPUT_CHARS``) so a
runaway query can't blow up an agent's context window. Failures return a
diagnostic string rather than raising — the agent can re-plan.
"""
from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_MAX_OUTPUT_CHARS = 10_000
_DEFAULT_GIT_TIMEOUT = 10.0
_READONLY_SQL_VERBS = ("select ", "show ", "explain ", "with ")


RoomHandler = Callable[[str], Awaitable[str]]


@dataclass
class Rooms:
    """Typed surface registry for one workspace.

    Construct with the workspace + optional file cache + metrics dict. Plug a
    custom handler for a scheme by passing ``handlers={"foo": my_async_fn}``.
    """

    repo_path: Path
    api_base_url: str = "http://localhost:8000"
    database_url: str | None = None
    file_cache: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)
    extra_handlers: dict[str, RoomHandler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Built-in handlers; extra_handlers can replace any of them.
        self._handlers: dict[str, RoomHandler] = {
            "code":    self._code,
            "db":      self._db,
            "git":     self._git,
            "api":     self._api,
            "docs":    self._docs,
            "metrics": self._metrics,
        }
        self._handlers.update(self.extra_handlers)

    # ── public surface ─────────────────────────────────────────────────────

    @property
    def schemes(self) -> tuple[str, ...]:
        """All registered scheme names — useful for surfacing in the system prompt."""
        return tuple(self._handlers.keys())

    async def query(self, room_uri: str) -> str:
        """Resolve ``scheme:body`` to a string. Returns a diagnostic on errors."""
        scheme, _, body = room_uri.partition(":")
        scheme = scheme.strip().lower()
        if not scheme or not _:
            return f"ERROR: room URI must be 'scheme:body', got {room_uri!r}"
        handler = self._handlers.get(scheme)
        if handler is None:
            return (
                f"ERROR: unknown room scheme {scheme!r}. "
                f"Available: {sorted(self._handlers)}"
            )
        try:
            out = await handler(body.strip())
        except Exception as exc:  # noqa: BLE001 — agent must see the failure
            log.exception("rooms.handler_error", scheme=scheme, body=body)
            return f"ERROR: {scheme}:{body} failed — {type(exc).__name__}: {exc}"
        return _truncate(out)

    # ── handlers ───────────────────────────────────────────────────────────

    async def _code(self, body: str) -> str:
        """`code:<rel-or-abs path>` — return file content."""
        if not body:
            return "ERROR: code: requires a path"
        candidate = Path(body)
        if not candidate.is_absolute():
            candidate = self.repo_path / candidate
        if not candidate.is_file():
            return f"ERROR: not a file: {candidate}"
        if self.file_cache is not None and hasattr(self.file_cache, "read"):
            try:
                return str(self.file_cache.read(str(candidate)))
            except Exception:  # noqa: BLE001
                pass  # fall through to direct read
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: could not read {candidate}: {exc}"

    async def _db(self, body: str) -> str:
        """`db:<sql>` — read-only SQL against ``database_url`` via asyncpg."""
        if not body:
            return "ERROR: db: requires SQL text"
        lower = body.lower().lstrip()
        if not any(lower.startswith(v) for v in _READONLY_SQL_VERBS):
            return (
                f"ERROR: db: refuses non-readonly SQL "
                f"(must start with SELECT/SHOW/EXPLAIN/WITH). Got: {body[:60]!r}"
            )
        if not self.database_url:
            return "ERROR: db: no database_url configured for this workspace"
        try:
            import asyncpg
        except ImportError:
            return "ERROR: db: asyncpg is not installed"
        url = self.database_url.replace("postgresql+asyncpg://", "postgresql://")
        try:
            conn = await asyncpg.connect(url)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: db: connect failed — {type(exc).__name__}: {exc}"
        try:
            rows = await conn.fetch(body)
            return "\n".join(str(dict(r)) for r in rows) or "(no rows)"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: db: query failed — {type(exc).__name__}: {exc}"
        finally:
            await conn.close()

    async def _git(self, body: str) -> str:
        """`git:<command>` — run ``git <command>`` in the workspace repo."""
        if not body:
            return "ERROR: git: requires a subcommand"
        try:
            argv = ["git", "-C", str(self.repo_path), *shlex.split(body)]
        except ValueError as exc:
            return f"ERROR: git: cannot parse arguments — {exc}"
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_DEFAULT_GIT_TIMEOUT,
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return f"ERROR: git: command timed out after {_DEFAULT_GIT_TIMEOUT:.0f}s"
        if proc.returncode != 0:
            return (
                f"ERROR: git exited {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")

    async def _api(self, body: str) -> str:
        """`api:<METHOD path>` — HTTP request to the workspace's API base URL."""
        if not body:
            return "ERROR: api: requires '<METHOD> <path>'"
        try:
            method, path = body.split(maxsplit=1)
        except ValueError:
            return f"ERROR: api: expected '<METHOD> <path>', got {body!r}"
        method_upper = method.upper()
        if method_upper not in {"GET", "HEAD", "OPTIONS"}:
            return (
                f"ERROR: api: refuses non-readonly methods, got {method_upper}. "
                "Use GET / HEAD / OPTIONS."
            )
        url = self.api_base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            import httpx
        except ImportError:
            return "ERROR: api: httpx not installed"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request(method_upper, url)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: api: {type(exc).__name__}: {exc}"
        return f"HTTP {resp.status_code}\n{resp.text}"

    async def _docs(self, body: str) -> str:
        """`docs:<name>` — read a markdown file under ``docs/`` or ``docs/adrs/``."""
        if not body:
            return "ERROR: docs: requires a name"
        # Look in two well-known places. Strip a leading 'docs/' if the agent
        # already qualified the path.
        name = body
        if name.startswith("docs/"):
            name = name[len("docs/"):]
        candidates = [
            self.repo_path / "docs" / name,
            self.repo_path / "docs" / f"{name}.md",
            self.repo_path / "docs" / "adrs" / name,
            self.repo_path / "docs" / "adrs" / f"{name}.md",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    return c.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    return f"ERROR: docs: read failed for {c} — {exc}"
        return f"ERROR: docs: no match for {body!r} under docs/ or docs/adrs/"

    async def _metrics(self, body: str) -> str:
        """`metrics:<key>` — read from the workspace's loaded metrics snapshot."""
        if not body:
            return "ERROR: metrics: requires a key"
        if not self.metrics:
            return "(no metrics loaded for this workspace)"
        if body in self.metrics:
            return str(self.metrics[body])
        # Allow nested lookups like `cost:24h` → metrics["cost"]["24h"].
        cur: Any = self.metrics
        for part in body.split(":"):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return f"(no metric for {body!r})"
        return str(cur)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return (
        text[:_MAX_OUTPUT_CHARS]
        + f"\n... (truncated at {_MAX_OUTPUT_CHARS} chars)"
    )


__all__ = ["Rooms", "RoomHandler"]
