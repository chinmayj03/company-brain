"""Unit tests for harness/permissions.py (ADR-0051 P4)."""
from __future__ import annotations

import os

import pytest
from companybrain.harness.permissions import (
    DEFAULT_GRANTS,
    Capability,
    Decision,
    WorkspaceGrants,
    is_auto_approve_env,
    load_workspace_grants,
    resolve_ask,
)

# ── Capability + Decision parsing ───────────────────────────────────────────


def test_capability_enum_round_trip():
    for c in Capability:
        assert Capability.from_str(c.value) == c


def test_capability_unknown_raises_with_helpful_message():
    with pytest.raises(ValueError, match="Unknown capability"):
        Capability.from_str("read_galaxy")


def test_workspace_grants_rejects_unknown_decision():
    with pytest.raises(ValueError, match="Unknown decision"):
        WorkspaceGrants.from_settings({"read_repo": "maybe"})


# ── decide() collapses to the strictest decision ───────────────────────────


def test_decide_empty_required_is_auto():
    """No required caps → auto."""
    assert DEFAULT_GRANTS.decide([]) == Decision.AUTO


def test_decide_returns_auto_when_all_auto():
    grants = WorkspaceGrants.from_settings({"read_repo": "auto", "read_brain": "auto"})
    assert grants.decide([Capability.READ_REPO, Capability.READ_BRAIN]) == Decision.AUTO


def test_decide_collapses_to_ask_when_one_is_ask():
    grants = WorkspaceGrants.from_settings({"read_repo": "auto", "write_brain": "ask"})
    assert grants.decide([Capability.READ_REPO, Capability.WRITE_BRAIN]) == Decision.ASK


def test_decide_collapses_to_deny_when_one_is_deny():
    grants = WorkspaceGrants.from_settings({"read_repo": "auto", "exec_shell": "deny"})
    assert grants.decide([Capability.READ_REPO, Capability.EXEC_SHELL]) == Decision.DENY


def test_decide_unknown_capability_defaults_to_ask():
    """Caps not present in the grants table are safe-by-default ASK."""
    grants = WorkspaceGrants.from_settings({"read_repo": "auto"})
    # NETWORK isn't configured → ASK → collapses ASK with READ_REPO=auto.
    assert grants.decide([Capability.READ_REPO, Capability.NETWORK]) == Decision.ASK


# ── DEFAULT_GRANTS profile ──────────────────────────────────────────────────


def test_default_grants_profile_matches_documented_policy():
    """The defaults must match the policy in HARNESS.md."""
    assert DEFAULT_GRANTS.for_capability(Capability.READ_REPO)   == Decision.AUTO
    assert DEFAULT_GRANTS.for_capability(Capability.READ_BRAIN)  == Decision.AUTO
    assert DEFAULT_GRANTS.for_capability(Capability.WRITE_BRAIN) == Decision.ASK
    assert DEFAULT_GRANTS.for_capability(Capability.NETWORK)     == Decision.ASK
    assert DEFAULT_GRANTS.for_capability(Capability.EXEC_SHELL)  == Decision.DENY
    assert DEFAULT_GRANTS.for_capability(Capability.LLM_CALL)    == Decision.AUTO


# ── ASK resolution ─────────────────────────────────────────────────────────


def test_resolve_ask_auto_approve_overrides_everything():
    assert resolve_ask(interactive=True,  auto_approve=True)  == Decision.AUTO
    assert resolve_ask(interactive=False, auto_approve=True)  == Decision.AUTO


def test_resolve_ask_interactive_keeps_ask():
    assert resolve_ask(interactive=True,  auto_approve=False) == Decision.ASK


def test_resolve_ask_non_interactive_denies():
    """Without --yes and no human attached, ASK must collapse to DENY."""
    assert resolve_ask(interactive=False, auto_approve=False) == Decision.DENY


# ── load_workspace_grants merges defaults + env + overrides ────────────────


def test_load_workspace_grants_returns_defaults_with_no_env():
    monkeypatch_env = os.environ.pop("BRAIN_GRANTS", None)
    try:
        grants = load_workspace_grants()
        assert grants.for_capability(Capability.WRITE_BRAIN) == Decision.ASK
        assert grants.for_capability(Capability.EXEC_SHELL)  == Decision.DENY
    finally:
        if monkeypatch_env is not None:
            os.environ["BRAIN_GRANTS"] = monkeypatch_env


def test_load_workspace_grants_honours_env(monkeypatch):
    """``BRAIN_GRANTS`` lets CI relax defaults without code changes."""
    monkeypatch.setenv("BRAIN_GRANTS", "exec_shell:auto,write_brain:auto")
    grants = load_workspace_grants()
    assert grants.for_capability(Capability.EXEC_SHELL)  == Decision.AUTO
    assert grants.for_capability(Capability.WRITE_BRAIN) == Decision.AUTO


def test_load_workspace_grants_overrides_argument_wins(monkeypatch):
    """Per-call overrides beat the env, which beat the defaults."""
    monkeypatch.setenv("BRAIN_GRANTS", "write_brain:auto")
    grants = load_workspace_grants(overrides={"write_brain": "deny"})
    assert grants.for_capability(Capability.WRITE_BRAIN) == Decision.DENY


def test_is_auto_approve_env_honours_truthy_strings(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", "y", "on"):
        monkeypatch.setenv("BRAIN_AUTOAPPROVE", truthy)
        assert is_auto_approve_env() is True
    for falsy in ("0", "false", "no", ""):
        monkeypatch.setenv("BRAIN_AUTOAPPROVE", falsy)
        assert is_auto_approve_env() is False
