"""Unit tests for per-repo BRAIN.md memory (ADR-0051 P3).

`memory.load` returns the file contents (or "" if missing); `memory.auto_append`
adds timestamped observations under the auto section, with a dedupe window
that suppresses repetitive pipeline spam.
"""
from __future__ import annotations

from pathlib import Path

from companybrain.harness import memory

# ── load ─────────────────────────────────────────────────────────────────────

def test_load_missing_brain_md_returns_empty_string(tmp_path: Path):
    """A repo with no .brain/BRAIN.md must return "" (not raise)."""
    assert memory.load(tmp_path) == ""


def test_load_returns_existing_brain_md(tmp_path: Path):
    """When .brain/BRAIN.md exists, its full contents are returned verbatim."""
    brain = tmp_path / ".brain"
    brain.mkdir()
    body = "# BRAIN.md\n## Curated notes\n- Watch for X.\n"
    (brain / "BRAIN.md").write_text(body)
    assert memory.load(tmp_path) == body


def test_brain_md_path_is_under_dot_brain(tmp_path: Path):
    """`.brain/BRAIN.md` is the canonical location."""
    p = memory.brain_md_path(tmp_path)
    assert p == tmp_path / ".brain" / "BRAIN.md"


# ── auto_append — happy path ─────────────────────────────────────────────────

def test_auto_append_creates_brain_dir_and_seeds_template(tmp_path: Path):
    """First call creates .brain/, seeds from the template, and appends."""
    memory.auto_append(tmp_path, "JsonKeyMapping always dropped")

    p = memory.brain_md_path(tmp_path)
    assert p.parent.is_dir()
    assert p.exists()
    text = p.read_text()
    # Template seed includes the human-edited section header.
    assert "Curated notes" in text
    # Auto-section marker present so future appends know where to land.
    assert "AUTO-APPENDED" in text
    # The observation landed under the auto section, with a leading bullet.
    assert "JsonKeyMapping always dropped" in text
    assert text.rstrip().endswith("JsonKeyMapping always dropped")


def test_auto_append_writes_iso_timestamp(tmp_path: Path):
    """Every appended bullet starts with an ISO-8601 UTC timestamp.

    The timestamp is what makes the auto section auditable across runs.
    """
    memory.auto_append(tmp_path, "first observation")
    text = memory.brain_md_path(tmp_path).read_text()
    last_line = [line for line in text.splitlines() if line.startswith("- ")][-1]
    # `- 2026-05-10T12:34:56+00:00 — first observation`
    assert last_line.startswith("- 20")
    assert "first observation" in last_line


def test_auto_append_dedupes_within_window(tmp_path: Path):
    """The same observation back-to-back lands once, not three times."""
    for _ in range(3):
        memory.auto_append(tmp_path, "JsonKeyMapping always dropped")
    text = memory.brain_md_path(tmp_path).read_text()
    assert text.count("JsonKeyMapping always dropped") == 1


def test_auto_append_keeps_distinct_observations(tmp_path: Path):
    """Two different observations both land — dedupe is per-observation."""
    memory.auto_append(tmp_path, "JsonKeyMapping always dropped")
    memory.auto_append(tmp_path, "ConstantsTable always dropped")
    text = memory.brain_md_path(tmp_path).read_text()
    assert "JsonKeyMapping" in text
    assert "ConstantsTable" in text


def test_auto_append_dedupe_window_is_bounded(tmp_path: Path):
    """An observation older than the dedupe window can be re-appended.

    Pad the file beyond `dedupe_window_chars` and assert the same
    observation lands a second time. This is intentional — pipeline
    state genuinely changes over weeks; we only suppress *recent* spam.
    """
    memory.auto_append(tmp_path, "OldObs")
    p = memory.brain_md_path(tmp_path)
    # Pad with junk so OldObs falls outside the dedupe window.
    p.write_text(p.read_text() + ("x" * 5_000))
    memory.auto_append(tmp_path, "OldObs", dedupe_window_chars=4_000)

    assert p.read_text().count("OldObs") == 2


def test_auto_append_skips_empty_observations(tmp_path: Path):
    """Whitespace-only / empty observations are silently dropped."""
    memory.auto_append(tmp_path, "")
    memory.auto_append(tmp_path, "   ")
    p = memory.brain_md_path(tmp_path)
    assert not p.exists() or "AUTO-APPENDED" not in (p.read_text() if p.exists() else "")


def test_auto_append_to_existing_brain_md_without_marker(tmp_path: Path):
    """A hand-written BRAIN.md missing the auto marker still gets appended.

    Users may have written their own BRAIN.md before P3 shipped. The
    appender must add the marker on first use rather than fail.
    """
    brain = tmp_path / ".brain"
    brain.mkdir()
    (brain / "BRAIN.md").write_text("# BRAIN.md\n\n## Curated notes\n- old note\n")

    memory.auto_append(tmp_path, "new observation")
    text = (brain / "BRAIN.md").read_text()

    # Both the original note and the marker + new observation are present.
    assert "old note" in text
    assert "AUTO-APPENDED" in text
    assert "new observation" in text


def test_auto_append_preserves_curated_section(tmp_path: Path):
    """Auto-append never touches the curated section."""
    brain = tmp_path / ".brain"
    brain.mkdir()
    curated = "# BRAIN.md\n\n## Curated notes\n- DO NOT TOUCH\n\n<!-- AUTO-APPENDED -->\n"
    (brain / "BRAIN.md").write_text(curated)

    memory.auto_append(tmp_path, "new observation")
    text = (brain / "BRAIN.md").read_text()

    assert "DO NOT TOUCH" in text
    assert "new observation" in text
    # Curated section is still ahead of the auto section.
    assert text.index("DO NOT TOUCH") < text.index("new observation")
