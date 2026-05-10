"""
Unit tests for ADR-0017: assumption_miner.mine_assumptions().

Tests cover all seven heuristic patterns, RELIES_ON edge emission,
deduplication, and severity/confidence values.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

import pytest

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.pipeline.assumption_miner import mine_assumptions
from companybrain.store.base import BrainEntity

# Module-level temp dir — cleaned up when the process exits.
_TMP = tempfile.mkdtemp(prefix="test_assumption_miner_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
_COUNTER = iter(range(10_000))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _unit(content: str, file_path: str = "UserCard.tsx") -> CodeUnit:
    """Write content to a real file and return a CodeUnit pointing at it.

    ADR-0045: CodeUnit.content is a lazy file read; a real file must exist
    for mine_assumptions (which reads unit.content) to see the source.
    """
    ext = Path(file_path).suffix or ".tsx"
    fp = Path(_TMP) / f"{next(_COUNTER)}{ext}"
    fp.write_text(content, encoding="utf-8")
    return CodeUnit(
        file_path=str(fp),
        repo_name="r",
        role="component",
        language="typescript",
        class_name="UserCard",
    )


def _parent() -> BrainEntity:
    return BrainEntity(
        id="urn:cb:dev:code:r:component:UserCard",
        entity_type="component",
        repo="r",
        file="UserCard.tsx",
        qualified_name="UserCard",
    )


# ── Pattern tests ─────────────────────────────────────────────────────────────

def test_extracts_jsdoc_assumption():
    unit = _unit("/** @assumption userId is always a UUID */\nfunction f(){}")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any("userId is always a UUID" in a.t1_summary for a in out)


def test_extracts_python_comment_assumption():
    unit = _unit("# ASSUMPTION: caller always authenticated", "auth.py")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any("caller always authenticated" in a.t1_summary for a in out)


def test_extracts_js_comment_assumption():
    unit = _unit("// ASSUME: user has at least one role\nconst role = user.role;")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any("user has at least one role" in a.t1_summary for a in out)


def test_extracts_non_null_assertion():
    unit = _unit("const r = user!.role;")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "non_null_ts" for a in out)


def test_extracts_guard_throw():
    unit = _unit("if (!user) throw new Error('no user');")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "guard_throw" for a in out)


def test_extracts_assert():
    unit = _unit("assert(id != null);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "assert" for a in out)


def test_extracts_invariant():
    unit = _unit("invariant(user.isActive, 'user must be active');")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "invariant" for a in out)


def test_extracts_zod_parse():
    unit = _unit("const data = UserSchema.parse(req.body);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "zod_parse" for a in out)


# ── Edge type ─────────────────────────────────────────────────────────────────

def test_all_assumptions_have_relies_on_edge():
    unit = _unit(
        "assert(id != null);\nif (!user) throw;\nconst r = x!.y;"
    )
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert len(out) > 0
    assert all(a.relationships[0]["edge_type"] == "RELIES_ON" for a in out)


def test_relies_on_target_is_parent_id():
    parent = _parent()
    unit = _unit("assert(x != null);")
    out = mine_assumptions(unit, parent, workspace_id="ws")
    assert out
    assert all(a.relationships[0]["target_id"] == parent.id for a in out)


# ── Entity shape ──────────────────────────────────────────────────────────────

def test_assumption_entity_type():
    unit = _unit("assert(x != null);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert all(a.entity_type == "assumption" for a in out)


def test_assumption_has_urn_id():
    unit = _unit("assert(x != null);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert all(a.id.startswith("urn:cb:") for a in out)


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_no_duplicates_on_same_statement():
    unit = _unit("assert(id != null);\nassert(id != null);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    statements = [a.metadata["statement"] for a in out]
    assert statements.count("id != null") == 1


def test_no_duplicates_case_insensitive():
    unit = _unit("assert(ID != null);\nassert(id != null);")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    # Both normalise to "id != null" after lower-casing
    assert len(out) == 1


# ── Empty content ─────────────────────────────────────────────────────────────

def test_empty_content_returns_empty_list():
    unit = _unit("")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert out == []


def test_no_patterns_returns_empty_list():
    unit = _unit("const x = 1;\nfunction add(a, b) { return a + b; }")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert out == []


# ── Severity and confidence ───────────────────────────────────────────────────

def test_invariant_has_critical_severity():
    unit = _unit("invariant(user.isActive, 'must be active');")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    inv = next((a for a in out if a.metadata["pattern"] == "invariant"), None)
    assert inv is not None
    assert inv.metadata["severity"] == "critical"


def test_guard_throw_has_high_severity():
    unit = _unit("if (!user) throw new Error('no user');")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    g = next((a for a in out if a.metadata["pattern"] == "guard_throw"), None)
    assert g is not None
    assert g.metadata["severity"] == "high"


def test_explicit_jsdoc_has_high_confidence():
    unit = _unit("/** @assumption userId is UUID */")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    exp = next((a for a in out if a.metadata["pattern"] == "explicit_jsdoc"), None)
    assert exp is not None
    assert exp.relationships[0]["confidence"] >= 0.9
