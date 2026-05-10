"""Unit tests for per-entity notes (ADR-0052 P6).

The CRUD path opens a Postgres connection — we don't have one in unit tests,
so the tests here cover:

  * the pure render_for_query() helper (no DB)
  * the data-class round-trip (EntityNote.from_row)
  * the asyncpg URL translation

Acceptance covers the actual round-trip against a live DB.
"""
from __future__ import annotations

from datetime import datetime, timezone

from companybrain.harness import notes as notes_mod


def test_entity_note_from_row_keeps_optional_fields():
    """from_row tolerates absent author / created_at columns."""
    row = {
        "id": 1, "workspace_id": "ws", "entity_urn": "urn:cb:dev:code:r:m:F.b",
        "note": "deprecated", "author": None, "created_at": None,
    }
    n = notes_mod.EntityNote.from_row(row)
    assert n.id == 1
    assert n.author is None
    assert n.created_at is None


def test_render_for_query_minimises_optional_keys():
    """Wire shape stays small — author/created_at omitted when unset."""
    notes = [
        notes_mod.EntityNote(
            id=1, workspace_id="ws", entity_urn="u1", note="bare",
        ),
        notes_mod.EntityNote(
            id=2, workspace_id="ws", entity_urn="u2", note="rich",
            author="alice", created_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        ),
    ]
    rendered = notes_mod.render_for_query(notes)
    assert rendered[0] == {"urn": "u1", "note": "bare"}
    assert rendered[1] == {
        "urn": "u2",
        "note": "rich",
        "author": "alice",
        "created_at": "2026-01-02T03:04:00+00:00",
    }


def test_db_url_strips_asyncpg(monkeypatch):
    """The CLI uses asyncpg directly; the +asyncpg dialect must be peeled off."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    assert notes_mod._db_url() == "postgresql://u:p@h:5432/db"


def test_db_url_default(monkeypatch):
    """Default URL is the same as the rest of the AI service."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert notes_mod._db_url() == "postgresql://localhost/companybrain"
