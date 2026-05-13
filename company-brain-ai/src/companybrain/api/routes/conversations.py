"""
Conversation history routes — ADR-0072 A1 (History), A2 (Saved Queries), A5 (Audit Log).

GET  /conversations              — list last 50 conversations for a workspace
GET  /conversations/{id}         — full detail including summary_json (QueryResponse)
PATCH /conversations/{id}        — save/unsave a conversation, set a title
DELETE /conversations/{id}       — hard-delete a conversation record
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class ConversationSummary(BaseModel):
    """List-view projection — no heavy summary_json payload."""
    id: UUID
    question: str
    title: Optional[str]
    asked_at: str          # ISO-8601 string (timestamptz rendered as text)
    saved: bool
    actor_id: Optional[str]
    actor_kind: Optional[str]

    class Config:
        from_attributes = True


class ConversationDetail(ConversationSummary):
    """Full detail including the QueryResponse JSON blob."""
    answer_md: Optional[str]
    summary_json: Optional[dict]


class ConversationPatch(BaseModel):
    """Fields the caller may update."""
    saved: Optional[bool] = None
    title: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    workspace_id: str = Query(..., description="UUID of the workspace"),
    saved: Optional[bool] = Query(None, description="Filter to saved=true rows only"),
):
    """
    Return the last 50 conversations for a workspace, newest first.

    Pass ``?saved=true`` to get only bookmarked rows (Saved tab).
    Pass ``?saved=false`` or omit to get the full recent history (History tab).
    The ``actor_id`` / ``actor_kind`` columns power the Audit Log tab on the
    frontend — callers can filter client-side by actor_kind='ci' to surface
    automated queries.
    """
    if saved is not None:
        sql = text("""
            SELECT id, question, title, asked_at, saved, actor_id, actor_kind
            FROM conversations
            WHERE workspace_id = :workspace_id
              AND saved = :saved
            ORDER BY asked_at DESC
            LIMIT 50
        """)
        params: dict = {"workspace_id": workspace_id, "saved": saved}
    else:
        sql = text("""
            SELECT id, question, title, asked_at, saved, actor_id, actor_kind
            FROM conversations
            WHERE workspace_id = :workspace_id
            ORDER BY asked_at DESC
            LIMIT 50
        """)
        params = {"workspace_id": workspace_id}

    async with get_session() as session:
        result = await session.execute(sql, params)
        rows = result.mappings().all()

    return [
        ConversationSummary(
            id=row["id"],
            question=row["question"],
            title=row["title"],
            asked_at=str(row["asked_at"]),
            saved=row["saved"],
            actor_id=row["actor_id"],
            actor_kind=row["actor_kind"],
        )
        for row in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: UUID):
    """
    Return the full conversation record including the QueryResponse JSON blob
    stored in ``summary_json`` and the pre-rendered ``answer_md`` markdown.
    """
    sql = text("""
        SELECT id, question, title, asked_at, saved, actor_id, actor_kind,
               answer_md, summary_json
        FROM conversations
        WHERE id = :id
    """)
    async with get_session() as session:
        result = await session.execute(sql, {"id": str(conversation_id)})
        row = result.mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=row["id"],
        question=row["question"],
        title=row["title"],
        asked_at=str(row["asked_at"]),
        saved=row["saved"],
        actor_id=row["actor_id"],
        actor_kind=row["actor_kind"],
        answer_md=row["answer_md"],
        summary_json=row["summary_json"],
    )


@router.patch("/{conversation_id}", response_model=ConversationDetail)
async def patch_conversation(conversation_id: UUID, body: ConversationPatch):
    """
    Update ``saved`` and/or ``title`` on an existing conversation.

    Used by the frontend when the user bookmarks a query (Saved tab) or renames
    a conversation in the history sidebar. Ignores unknown fields gracefully.
    """
    # Build SET clause dynamically from supplied fields only.
    set_parts: list[str] = []
    params: dict = {"id": str(conversation_id)}

    if body.saved is not None:
        set_parts.append("saved = :saved")
        params["saved"] = body.saved
    if body.title is not None:
        set_parts.append("title = :title")
        params["title"] = body.title

    if not set_parts:
        raise HTTPException(status_code=422, detail="No updatable fields provided")

    update_sql = text(f"""
        UPDATE conversations
        SET {', '.join(set_parts)}
        WHERE id = :id
        RETURNING id, question, title, asked_at, saved, actor_id, actor_kind,
                  answer_md, summary_json
    """)

    async with get_session() as session:
        result = await session.execute(update_sql, params)
        row = result.mappings().first()
        await session.commit()

    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    log.info("Conversation patched", id=str(conversation_id), fields=list(params.keys()))
    return ConversationDetail(
        id=row["id"],
        question=row["question"],
        title=row["title"],
        asked_at=str(row["asked_at"]),
        saved=row["saved"],
        actor_id=row["actor_id"],
        actor_kind=row["actor_kind"],
        answer_md=row["answer_md"],
        summary_json=row["summary_json"],
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID):
    """
    Hard-delete a conversation record.

    Returns 204 No Content on success, 404 if the conversation was not found.
    """
    sql = text("DELETE FROM conversations WHERE id = :id RETURNING id")
    async with get_session() as session:
        result = await session.execute(sql, {"id": str(conversation_id)})
        deleted = result.first()
        await session.commit()

    if deleted is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    log.info("Conversation deleted", id=str(conversation_id))
