"""Unit tests for harness/hooks.py (ADR-0051 P4).

The :mod:`hooks` module fires shell scripts at well-defined points. The tests
verify the resilience contract — a missing, broken, or slow hook never aborts
the run — and the success path (JSON in, JSON out).
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
from companybrain.harness import hooks


def _write_hook(repo: Path, event: str, body: str) -> Path:
    """Create an executable .brain/hooks/<event>.sh script."""
    p = repo / ".brain" / "hooks" / f"{event}.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# ── absent / non-executable ────────────────────────────────────────────────


async def test_fire_returns_empty_when_script_absent(tmp_path: Path):
    out = await hooks.fire("pre_extraction", {"any": "thing"}, repo_path=tmp_path)
    assert out == {}


async def test_fire_returns_empty_when_script_not_executable(tmp_path: Path):
    """A non-executable script is treated as absent — opt-in via chmod +x."""
    p = tmp_path / ".brain" / "hooks" / "pre_extraction.sh"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/sh\necho '{}'\n")
    # No chmod: deliberately non-executable.
    out = await hooks.fire("pre_extraction", {}, repo_path=tmp_path)
    assert out == {}


async def test_fire_rejects_unknown_event(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown hook event"):
        await hooks.fire("not_a_real_event", {}, repo_path=tmp_path)


# ── success path ──────────────────────────────────────────────────────────


async def test_fire_parses_stdout_json(tmp_path: Path):
    """A 0-exit hook with JSON on stdout returns that JSON as a dict."""
    _write_hook(tmp_path, "pre_extraction",
                "#!/bin/sh\ncat >/dev/null\necho '{\"drop_globs\": [\"x\"]}'\n")
    out = await hooks.fire("pre_extraction", {"file": "a.java"}, repo_path=tmp_path)
    assert out == {"drop_globs": ["x"]}


async def test_fire_passes_payload_on_stdin(tmp_path: Path):
    """The payload arrives on stdin as JSON; the hook can read and react to it."""
    _write_hook(
        tmp_path, "post_extraction",
        # The hook echoes the size of the input as a JSON object so we can
        # verify the payload actually reached the script.
        "#!/bin/sh\nINPUT=$(cat)\nLEN=$(printf '%s' \"$INPUT\" | wc -c | tr -d ' ')\n"
        "printf '{\"received_bytes\": %s}\\n' \"$LEN\"\n",
    )
    payload = {"file": "Foo.java", "entities": 7}
    out = await hooks.fire("post_extraction", payload, repo_path=tmp_path)
    assert out["received_bytes"] > 10


# ── failure modes ─────────────────────────────────────────────────────────


async def test_fire_returns_empty_on_nonzero_exit(tmp_path: Path):
    _write_hook(tmp_path, "pre_storage",
                "#!/bin/sh\necho '{\"unused\": 1}'\nexit 7\n")
    out = await hooks.fire("pre_storage", {}, repo_path=tmp_path)
    assert out == {}


async def test_fire_returns_empty_on_invalid_json(tmp_path: Path):
    """Garbage stdout is logged + dropped, never raised — runs must continue."""
    _write_hook(tmp_path, "post_storage",
                "#!/bin/sh\necho 'this is not json'\n")
    out = await hooks.fire("post_storage", {}, repo_path=tmp_path)
    assert out == {}


async def test_fire_returns_empty_on_non_object_json(tmp_path: Path):
    """A list or scalar on stdout still parses as JSON but is rejected."""
    _write_hook(tmp_path, "post_query",
                "#!/bin/sh\necho '[1,2,3]'\n")
    out = await hooks.fire("post_query", {}, repo_path=tmp_path)
    assert out == {}


async def test_fire_returns_empty_on_empty_stdout(tmp_path: Path):
    _write_hook(tmp_path, "session_end", "#!/bin/sh\n:\n")
    out = await hooks.fire("session_end", {"id": "x"}, repo_path=tmp_path)
    assert out == {}


async def test_fire_enforces_timeout(tmp_path: Path):
    """A hook that exceeds the timeout returns ``{}`` and the proc is killed."""
    _write_hook(tmp_path, "session_start", "#!/bin/sh\nsleep 5\necho '{}'\n")
    out = await hooks.fire(
        "session_start", {"id": "x"},
        repo_path=tmp_path, timeout_s=1,
    )
    assert out == {}


# ── EVENTS contract ────────────────────────────────────────────────────────


def test_events_includes_all_lifecycle_points():
    """The 9 documented events must all be present in EVENTS."""
    expected = {
        "session_start", "pre_extraction", "post_extraction",
        "on_truncation", "pre_storage", "post_storage",
        "pre_query", "post_query", "session_end",
    }
    assert set(hooks.EVENTS) == expected
