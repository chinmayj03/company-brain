"""ADR-0061 E2 — re-read on confidence-flag.

When the first Sonnet pass cites an entity with ``confidence < 0.7`` the query
path re-fetches that entity's source from disk and re-runs the answer with
the higher-fidelity content. The re-read step is intentionally cheap (one
extra LLM call, no graph traversal) — it's a salvage move for cases where the
T2 compressor stripped just enough that the model wasn't sure.

Public surface:
  - LOW_FIDELITY_THRESHOLD   — confidence below which a citation is "shaky".
  - identify_shaky_citations — return the URNs whose source we should re-fetch.
  - load_source_excerpts     — read the file for each shaky citation.
  - rerun_with_source        — call the LLM a second time with the excerpts.

The route at /query owns the orchestration: it only invokes ``rerun_with_source``
if at least one shaky citation can be backed by a real on-disk file. That keeps
the cost cap simple — 0 or 1 extra calls per query.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import structlog

from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT
from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.query_response import Citation, Confidence, QueryResponse

log = structlog.get_logger(__name__)


LOW_FIDELITY_THRESHOLD = 0.7         # confidence under this triggers a re-read
MAX_EXCERPT_CHARS = 4000             # per file — keeps the extra prompt small
MAX_FILES_RE_READ = 4                # cap total files we will re-fetch
MAX_REREAD_TOKENS = 4096             # output budget for the re-run


@dataclass
class _Excerpt:
    urn: str
    name: str
    file_path: str
    body: str


# ── Public helpers ────────────────────────────────────────────────────────────

def identify_shaky_citations(
    response: QueryResponse,
    *,
    threshold: float = LOW_FIDELITY_THRESHOLD,
) -> list[Citation]:
    """Return citations from ``affected_entities`` whose confidence < threshold."""
    shaky: list[Citation] = []
    for c in response.affected_entities:
        if c.confidence is None:
            continue
        if c.confidence < threshold:
            shaky.append(c)
    return shaky[:MAX_FILES_RE_READ]


async def load_source_excerpts(
    citations: Iterable[Citation],
    *,
    workspace_id: str,
    repo_path: Optional[str],
) -> list[_Excerpt]:
    """Read the file backing each shaky citation. Returns only those we could
    actually read; silently drops the rest (the original answer stands)."""
    excerpts: list[_Excerpt] = []
    for cit in citations:
        path = await _resolve_entity_file(cit.urn, workspace_id=workspace_id,
                                          repo_path=repo_path)
        if not path:
            log.debug("[query/reread] could not resolve file for citation", urn=cit.urn)
            continue
        try:
            body = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.debug("[query/reread] read failed", path=path, error=str(e))
            continue
        if len(body) > MAX_EXCERPT_CHARS:
            body = body[:MAX_EXCERPT_CHARS] + "\n// ... (truncated for re-read)"
        excerpts.append(_Excerpt(urn=cit.urn, name=cit.name,
                                 file_path=str(path), body=body))
    return excerpts


async def rerun_with_source(
    *,
    question: str,
    excerpts: list[_Excerpt],
    prior: QueryResponse,
) -> QueryResponse:
    """Make a second LLM call carrying the disk-fresh excerpts.

    The system prompt is the existing /query system prompt — we only swap the
    user message. The model is told that the prior answer was low-confidence
    and these are the authoritative excerpts; it is asked to re-emit the
    SAME response shape with corrected citations.
    """
    if not excerpts:
        return prior

    user = _build_reread_user_message(question, excerpts, prior)
    provider = get_provider()
    try:
        resp = await provider.chat(
            messages=[
                ChatMessage(role="system", content=QUERY_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user),
            ],
            role=TaskRole.QUERY,
            max_tokens=MAX_REREAD_TOKENS,
        )
    except Exception as e:
        log.warning("[query/reread] LLM call failed — keeping prior answer",
                    error=str(e))
        return prior

    rewritten = _parse_or_keep(resp.content, prior)
    rewritten.telemetry = dict(prior.telemetry or {})
    rewritten.telemetry["reread_invoked"] = True
    rewritten.telemetry["reread_files"] = [e.file_path for e in excerpts]
    return rewritten


# ── Internals ─────────────────────────────────────────────────────────────────

def _build_reread_user_message(
    question: str,
    excerpts: list[_Excerpt],
    prior: QueryResponse,
) -> str:
    parts: list[str] = [
        "AUTHORITATIVE SOURCE EXCERPTS — read these before answering.",
        "Each block is the verbatim file content for one URN that the previous",
        "draft cited with low confidence. Update any claim that conflicts with",
        "what these excerpts actually say. Keep the same JSON schema.",
        "",
    ]
    for ex in excerpts:
        parts.append(f"### {ex.name}  [{ex.urn}]")
        parts.append(f"file: {ex.file_path}")
        parts.append("```")
        parts.append(ex.body.rstrip())
        parts.append("```")
        parts.append("")
    parts.append("PRIOR DRAFT SUMMARY (for continuity, do not blindly trust):")
    parts.append((prior.summary or "")[:1200])
    parts.append("")
    parts.append(f"QUESTION: {question}")
    return "\n".join(parts)


def _parse_or_keep(raw: str, prior: QueryResponse) -> QueryResponse:
    """Parse the LLM's re-read output back into a QueryResponse. On parse
    failure we keep the prior typed object but append the new free-form text
    to ``summary`` so the user at least sees the corrected wording."""
    text = (raw or "").strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    try:
        data = json.loads(cleaned)
        rewritten = QueryResponse(**data)
        return rewritten
    except Exception as e:
        log.debug("[query/reread] JSON parse failed — appending text",
                  error=str(e))
        merged = QueryResponse(
            summary=text or prior.summary,
            summary_md=prior.summary_md,
            call_chain=prior.call_chain,
            sql_quotes=prior.sql_quotes,
            affected_entities=prior.affected_entities,
            change_risk=prior.change_risk,
            confidence=Confidence(
                level="medium",
                rationale="Re-read returned free-form text; structured "
                          "fields kept from the prior draft.",
            ),
            caveats=prior.caveats,
            follow_up_questions=prior.follow_up_questions,
        )
        return merged


async def _resolve_entity_file(
    urn: str,
    *,
    workspace_id: str,
    repo_path: Optional[str],
) -> Optional[str]:
    """Map a URN to an absolute file path by reading the entity from .brain/.

    Best-effort. If the .brain/ directory is missing, or the entity has no
    ``file`` metadata, return None and let the caller skip the re-read.
    """
    brain_root = _resolve_brain_root(repo_path)
    if brain_root is None:
        return None
    try:
        from companybrain.store.json_store import JsonFileBrainStore
        store = JsonFileBrainStore(brain_root)
        entity = await store.read(urn)
    except Exception as e:
        log.debug("[query/reread] store.read failed", urn=urn, error=str(e))
        return None
    if entity is None:
        return None
    rel = entity.file or ""
    if not rel:
        return None
    # ``file`` is repo-relative. Walk up brain_root → repo root.
    repo_root = brain_root.parent if brain_root.name == ".brain" else brain_root
    candidate = (repo_root / rel).resolve()
    if candidate.is_file():
        return str(candidate)
    return None


def _resolve_brain_root(repo_path: Optional[str]) -> Optional[Path]:
    if repo_path:
        p = Path(repo_path) / ".brain"
        if p.is_dir():
            return p
        if Path(repo_path).is_dir() and Path(repo_path).name == ".brain":
            return Path(repo_path)
    env = os.environ.get("BRAIN_ROOT")
    if env:
        p = Path(env)
        if p.is_dir() and p.name == ".brain":
            return p
        cand = p / ".brain"
        if cand.is_dir():
            return cand
    return None


# ── Orchestrator-friendly facade ──────────────────────────────────────────────

async def maybe_reread(
    *,
    question: str,
    response: QueryResponse,
    workspace_id: str,
    repo_path: Optional[str],
) -> QueryResponse:
    """Single-call helper for the /query route: identify shaky citations,
    load excerpts, and re-run the LLM if there's anything worth re-running on.

    Returns the response unchanged if the prior draft was confident enough
    or no source files could be resolved.
    """
    shaky = identify_shaky_citations(response)
    if not shaky:
        return response
    excerpts = await load_source_excerpts(
        shaky, workspace_id=workspace_id, repo_path=repo_path,
    )
    if not excerpts:
        # Surface that we tried (helps debugging telemetry) but didn't re-run.
        response.telemetry = dict(response.telemetry or {})
        response.telemetry["reread_invoked"] = False
        response.telemetry["reread_skipped"] = "no_source_resolved"
        return response
    return await rerun_with_source(
        question=question, excerpts=excerpts, prior=response,
    )
