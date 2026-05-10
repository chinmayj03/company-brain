"""Per-tool capability model (ADR-0051 P4).

A capability is a coarse permission label like ``write_brain`` or
``exec_shell``. Each tool declares which capabilities it requires, and each
workspace's grants table maps capability → :class:`Decision` (auto / ask /
deny). When the harness is about to dispatch a tool, it asks the workspace's
grants what to do — collapsing the answer for *all* required capabilities to
the strictest decision.

This replaces the scattered ``BRAIN_*`` env-flag checks that previously gated
side-effecting behaviour in pipeline code (``BRAIN_USE_HARNESS``,
``BRAIN_SKIP_INTENT_ROUTER``, etc. remain as runtime knobs but no longer carry
authorization meaning).

Public surface
--------------

* :class:`Capability`  — the closed enum of capabilities tools may declare.
* :class:`Decision`    — auto / ask / deny.
* :class:`WorkspaceGrants` — the per-workspace grants table, with
  :meth:`decide` returning the worst-case decision across a list of caps.
* :data:`DEFAULT_GRANTS` — what an unconfigured workspace inherits.
* :func:`load_workspace_grants` — read grants from settings + env override.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """Closed set of capability labels tools may require.

    Keep this set small. Splitting hairs (``read_class_file`` vs.
    ``read_repo_root``) reproduces the old env-flag mess. The unit of
    granularity is "what would a security-conscious operator want a separate
    yes/no for".
    """

    READ_REPO   = "read_repo"     # filesystem reads under repo_path
    READ_BRAIN  = "read_brain"    # reads from .brain/ + Postgres/Neo4j/Qdrant
    WRITE_BRAIN = "write_brain"   # mutations to .brain/ + projection stores
    NETWORK     = "network"       # arbitrary outbound HTTP (not provider LLM)
    EXEC_SHELL  = "exec_shell"    # spawning subprocesses (e.g. git, ripgrep)
    LLM_CALL    = "llm_call"      # outbound LLM provider calls

    @classmethod
    def from_str(cls, value: str) -> Capability:
        """Parse a string into a :class:`Capability` with a friendlier error."""
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(
                f"Unknown capability: {value!r}. Valid: "
                f"{[c.value for c in cls]}"
            ) from exc


class Decision(str, Enum):
    """Three-tier permission decision.

    ``auto``  — proceed silently.
    ``ask``   — interactive sessions prompt; non-interactive (CI, server)
                converts to deny unless ``--yes`` / ``BRAIN_AUTOAPPROVE=true``.
    ``deny``  — refuse the tool call; surface as an error to the model so it
                can re-plan.
    """

    AUTO = "auto"
    ASK  = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class WorkspaceGrants:
    """Capability → Decision mapping for one workspace."""

    granted: dict[Capability, Decision] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, raw: dict[str, str]) -> WorkspaceGrants:
        """Build from a plain ``{"write_brain": "ask"}`` dict.

        Unknown capability names raise; unknown decisions raise. We prefer a
        loud config error to silently accepting a typo.
        """
        out: dict[Capability, Decision] = {}
        for k, v in raw.items():
            cap = Capability.from_str(k)
            try:
                out[cap] = Decision(v)
            except ValueError as exc:
                raise ValueError(
                    f"Unknown decision for {k!r}: {v!r}. "
                    f"Valid: {[d.value for d in Decision]}"
                ) from exc
        return cls(granted=out)

    def for_capability(self, cap: Capability) -> Decision:
        """Look up one capability. Default = ASK (safe-by-default)."""
        return self.granted.get(cap, Decision.ASK)

    def decide(self, required: list[Capability] | tuple[Capability, ...]) -> Decision:
        """Collapse decisions across required capabilities to the strictest one.

        ``deny > ask > auto``. An empty ``required`` list means "no caps
        needed" and returns ``auto``.
        """
        worst = Decision.AUTO
        for cap in required:
            d = self.for_capability(cap)
            if d == Decision.DENY:
                return Decision.DENY
            if d == Decision.ASK:
                worst = Decision.ASK
        return worst


# Defaults: read tools auto, mutations require an explicit ask, shell denied.
# Tweak per workspace, not here.
DEFAULT_GRANTS = WorkspaceGrants.from_settings({
    "read_repo":   "auto",
    "read_brain":  "auto",
    "write_brain": "ask",
    "network":     "ask",
    "exec_shell":  "deny",
    "llm_call":    "auto",
})


def load_workspace_grants(
    workspace_id: str | None = None,
    *,
    overrides: dict[str, str] | None = None,
) -> WorkspaceGrants:
    """Resolve the :class:`WorkspaceGrants` for one workspace.

    Resolution order (later wins):
      1. :data:`DEFAULT_GRANTS`.
      2. Environment override ``BRAIN_GRANTS=write_brain:auto,exec_shell:auto``.
         Useful for CI / acceptance tests.
      3. Per-call ``overrides`` dict.

    Workspace-scoped persistence (per-workspace_id) is intentionally out of
    scope for P4; we wire a hook here so future phases can drop in a DB read
    without changing tool-dispatch sites.
    """
    merged: dict[str, str] = {c.value: d.value for c, d in DEFAULT_GRANTS.granted.items()}

    env_raw = os.environ.get("BRAIN_GRANTS", "").strip()
    if env_raw:
        for entry in env_raw.split(","):
            if ":" not in entry:
                continue
            k, v = entry.split(":", 1)
            merged[k.strip()] = v.strip()

    if overrides:
        merged.update(overrides)

    # `workspace_id` is forwarded for future per-workspace persistence; no DB
    # read in P4 so we just acknowledge it for tracing.
    _ = workspace_id

    return WorkspaceGrants.from_settings(merged)


# ── ASK-resolution helpers ────────────────────────────────────────────────────


def resolve_ask(
    *,
    interactive: bool,
    auto_approve: bool,
) -> Decision:
    """Convert a raw ``ASK`` decision to the effective one for this run.

    The harness asks the human at most when the run is interactive AND no
    auto-approve flag is set. Otherwise:
      * ``interactive=True, auto_approve=True``   → AUTO
      * ``interactive=True, auto_approve=False``  → ASK (caller prompts)
      * ``interactive=False, auto_approve=True``  → AUTO
      * ``interactive=False, auto_approve=False`` → DENY (no one to ask)
    """
    if auto_approve:
        return Decision.AUTO
    if interactive:
        return Decision.ASK
    return Decision.DENY


def is_auto_approve_env() -> bool:
    """``BRAIN_AUTOAPPROVE=true|1|yes`` → ``True``. Default ``False``.

    Centralised here so CLI ``--yes`` and the env knob agree.
    """
    raw = os.environ.get("BRAIN_AUTOAPPROVE", "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


__all__ = [
    "Capability",
    "Decision",
    "WorkspaceGrants",
    "DEFAULT_GRANTS",
    "load_workspace_grants",
    "resolve_ask",
    "is_auto_approve_env",
]
