"""Per-entity sticky notes (ADR-0052 P6).

Notes are free-form annotations attached to a node URN, scoped to a workspace.
They surface inline in ``/query`` responses so a curator's "Deprecated 2026-Q4"
or "Owned by payments-team" reaches every consumer, not just the person who
typed it.

Storage is the ``entity_notes`` table created in V14. We hit it directly via
asyncpg rather than SQLAlchemy so the harness module stays thin.

Public surface
--------------

* :func:`add_note` — insert one row.
* :func:`list_notes` — fetch every note attached to a single URN.
* :func:`list_notes_for_urns` — bulk fetch keyed by URN; used by ``/query``.
* :func:`delete_note` — remove a row by id.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

import structlog

log = structlog.get_logger(__name__)


@dataclass
class EntityNote:
    """One row of ``entity_notes``."""
    id: int
    workspace_id: str
    entity_urn: str
    note: str
    author: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: dict) -> "EntityNote":
        return cls(
            id=int(row["id"]),
            workspace_id=str(row["workspace_id"]),
            entity_urn=str(row["entity_urn"]),
            note=str(row["note"]),
            author=row.get("author"),
            created_at=row.get("created_at"),
        )


# ── DB connection helper ─────────────────────────────────────────────────────

def _db_url() -> str:
    raw = os.environ.get("DATABASE_URL", "postgresql://localhost/companybrain")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    """Return an asyncpg connection. Importing inside the helper means tests
    can monkeypatch :func:`add_note` and friends without needing the dep."""
    import asyncpg
    return await asyncpg.connect(_db_url())


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def add_note(
    *,
    workspace_id: str,
    entity_urn: str,
    note: str,
    author: Optional[str] = None,
) -> EntityNote:
    """Insert one note. Returns the persisted row with its assigned id."""
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO entity_notes (workspace_id, entity_urn, note, author)
            VALUES ($1::uuid, $2, $3, $4)
            RETURNING id, workspace_id::text, entity_urn, note, author, created_at
            """,
            workspace_id, entity_urn, note, author,
        )
    finally:
        await conn.close()
    if row is None:                                 # pragma: no cover
        raise RuntimeError("entity_notes INSERT returned no row")
    log.info("notes.add", urn=entity_urn, author=author)
    return EntityNote.from_row(dict(row))


async def list_notes(
    *,
    workspace_id: str,
    entity_urn: str,
) -> list[EntityNote]:
    """Every note for one URN, oldest-first."""
    conn = await _connect()
    try:
        rows = await conn.fetch(
            """
            SELECT id, workspace_id::text, entity_urn, note, author, created_at
              FROM entity_notes
             WHERE workspace_id = $1::uuid AND entity_urn = $2
          ORDER BY created_at ASC, id ASC
            """,
            workspace_id, entity_urn,
        )
    finally:
        await conn.close()
    return [EntityNote.from_row(dict(r)) for r in rows]


async def list_notes_for_urns(
    *,
    workspace_id: str,
    entity_urns: Iterable[str],
) -> dict[str, list[EntityNote]]:
    """Bulk fetch — used by ``/query`` to attach notes to every cited entity.

    Returns a mapping ``{urn: [notes]}``; URNs with no notes are absent so
    callers can ``if urn in result`` without iterating empty lists.
    """
    urn_list = [u for u in entity_urns if u]
    if not urn_list:
        return {}
    conn = await _connect()
    try:
        rows = await conn.fetch(
            """
            SELECT id, workspace_id::text, entity_urn, note, author, created_at
              FROM entity_notes
             WHERE workspace_id = $1::uuid AND entity_urn = ANY($2::text[])
          ORDER BY entity_urn, created_at ASC, id ASC
            """,
            workspace_id, urn_list,
        )
    finally:
        await conn.close()
    out: dict[str, list[EntityNote]] = {}
    for r in rows:
        n = EntityNote.from_row(dict(r))
        out.setdefault(n.entity_urn, []).append(n)
    return out


async def delete_note(*, note_id: int) -> bool:
    """Delete by primary key. Returns True iff a row was actually removed."""
    conn = await _connect()
    try:
        result = await conn.execute(
            "DELETE FROM entity_notes WHERE id = $1",
            note_id,
        )
    finally:
        await conn.close()
    # asyncpg returns ``"DELETE <count>"`` — peel out the count.
    parts = (result or "").split()
    deleted = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    log.info("notes.delete", id=note_id, deleted=deleted)
    return deleted > 0


# ── pure helpers (testable without a DB) ─────────────────────────────────────

def render_for_query(notes: list[EntityNote]) -> list[dict]:
    """Shape notes for the ``/query`` JSON response.

    The route surfaces notes alongside the cited entities; this helper keeps
    the wire shape small (no created_at when unset, no author when unset).
    """
    out: list[dict] = []
    for n in notes:
        item: dict = {"urn": n.entity_urn, "note": n.note}
        if n.author:
            item["author"] = n.author
        if n.created_at is not None:
            item["created_at"] = n.created_at.isoformat()
        out.append(item)
    return out
