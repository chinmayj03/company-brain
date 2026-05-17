"""
Resolution API routes — ADR-0093.

Endpoints
---------
GET  /resolution/suggestions            — list pending matches awaiting confirmation
POST /resolution/confirm/{id}           — human confirms a suggested match
POST /resolution/reject/{id}            — human rejects a suggested match
GET  /resolution/entity/{domain_urn}    — all artifacts resolved to a domain entity

The store is instantiated per-request from the ``RESOLUTION_STORE_PATH``
setting (default: ``.resolution/`` relative to the CWD).  In production
this should point to a shared volume or be backed by a real DB; for now
the JSON-file store is the source of truth (same pattern as JsonFileBrainStore).
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Optional

import structlog
from fastapi import APIRouter, Body, HTTPException, Path as FPath
from pydantic import BaseModel

from companybrain.config import settings
from companybrain.resolution.store import ResolutionStore

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_store() -> ResolutionStore:
    store_path = Path(settings.resolution_store_path)
    return ResolutionStore(store_path)


# ── Request / Response models ─────────────────────────────────────────────────

class ConfirmRequest(BaseModel):
    domain_urn: Optional[str] = None   # override domain URN (optional)


class SuggestionsResponse(BaseModel):
    pending: list[dict]
    total: int


class EntityArtifactsResponse(BaseModel):
    domain_urn: str
    artifacts: list[str]
    total: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "/resolution/suggestions",
    response_model=SuggestionsResponse,
    summary="List pending resolution suggestions awaiting human confirmation",
)
async def list_suggestions() -> SuggestionsResponse:
    """
    Return all resolution matches in ``status=pending`` that were scored in
    the 0.60–0.80 confidence band (suggest, not auto-resolved).
    """
    store = _get_store()
    pending = store.get_pending_matches()
    return SuggestionsResponse(pending=pending, total=len(pending))


@router.post(
    "/resolution/confirm/{match_id}",
    summary="Human confirms a suggested entity resolution match",
)
async def confirm_match(
    match_id: str = FPath(..., description="Match id returned by /resolution/suggestions"),
    body: ConfirmRequest = Body(default=ConfirmRequest()),
) -> dict:
    """
    Confirm that the two artifacts in *match_id* refer to the same entity.

    Optionally supply a ``domain_urn`` override; otherwise the domain URN
    already recorded on the match is used.
    """
    store = _get_store()
    match = store.get_match_by_id(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found")

    domain_urn = body.domain_urn or match["domain_urn"]
    artifact_a = match["candidate_a"]["artifact_urn"]
    artifact_b = match["candidate_b"]["artifact_urn"]

    store.record_human_confirmation(artifact_a, domain_urn, match_id=match_id)
    store.record_human_confirmation(artifact_b, domain_urn, match_id=match_id)

    log.info("resolution.confirmed", match_id=match_id, domain_urn=domain_urn)
    return {"status": "confirmed", "match_id": match_id, "domain_urn": domain_urn}


@router.post(
    "/resolution/reject/{match_id}",
    summary="Human rejects a suggested entity resolution match",
)
async def reject_match(
    match_id: str = FPath(..., description="Match id returned by /resolution/suggestions"),
) -> dict:
    """
    Reject the suggested match — the two artifacts remain as separate entities.
    """
    store = _get_store()
    match = store.get_match_by_id(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found")

    store.record_human_rejection(match_id)
    log.info("resolution.rejected", match_id=match_id)
    return {"status": "rejected", "match_id": match_id}


@router.get(
    "/resolution/entity/{domain_urn:path}",
    response_model=EntityArtifactsResponse,
    summary="List all artifacts resolved to a domain entity",
)
async def get_entity_artifacts(
    domain_urn: str = FPath(..., description="domain://slug@workspace"),
) -> EntityArtifactsResponse:
    """
    Return all artifact URNs that have been resolved to *domain_urn*.

    The ``domain_urn`` parameter is URL-encoded in the path, e.g.
    ``/resolution/entity/domain%3A%2F%2Fpayer_module%40acme``.
    """
    # FastAPI's {domain_urn:path} captures slashes but not the scheme separator;
    # accept both raw and percent-encoded forms.
    decoded = urllib.parse.unquote(domain_urn)
    store = _get_store()
    artifacts = store.get_artifacts_for_entity(decoded)
    return EntityArtifactsResponse(
        domain_urn=decoded,
        artifacts=artifacts,
        total=len(artifacts),
    )
