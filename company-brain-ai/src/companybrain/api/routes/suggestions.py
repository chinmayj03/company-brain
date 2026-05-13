"""
Suggested Questions — ADR-0072 item A6.

GET /suggestions?workspace_id={id}&repo_path={path}
    Returns exactly 4 suggested question chips for the UI.

Heuristic:
  1. Scan .brain/ directory under repo_path for entity JSON files.
  2. Pick entities with the highest blast_radius or most recently modified.
  3. Generate question strings from entity names.
  4. Fall back to 4 static defaults on any error or empty brain.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Query

log = structlog.get_logger(__name__)
router = APIRouter()

_NUM_CHIPS = 4

_FALLBACK_QUESTIONS: List[str] = [
    "What breaks if I drop the lobName column?",
    "Who owns the payment processing flow?",
    "Which services depend on the auth middleware?",
    "What's the blast radius of changing the user schema?",
]

_QUESTION_TEMPLATES = [
    "What breaks if {name} changes?",
    "Who owns {name}?",
    "Which services depend on {name}?",
    "What's the blast radius of {name}?",
]


def _load_brain_entities(brain_dir: Path) -> List[dict]:
    """
    Scan .brain/ for *.json entity files. Returns list of entity dicts,
    sorted by blast_radius descending then mtime descending.
    Silently ignores unreadable / malformed files.
    """
    entities = []
    try:
        for entry in brain_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".json":
                continue
            try:
                with entry.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                # Attach mtime for secondary sort
                data["_mtime"] = entry.stat().st_mtime
                entities.append(data)
            except Exception:
                pass  # skip malformed files
    except Exception:
        pass
    return entities


def _pick_top_entities(entities: List[dict], n: int) -> List[dict]:
    """Sort by blast_radius desc, mtime desc, return top n."""
    return sorted(
        entities,
        key=lambda e: (-(e.get("blast_radius") or 0), -e.get("_mtime", 0)),
    )[:n]


def _entity_name(entity: dict) -> Optional[str]:
    """Extract a human-readable name from an entity dict."""
    for key in ("name", "entity_name", "qualified_name", "display_name"):
        val = entity.get(key)
        if val and isinstance(val, str):
            return val
    return None


def _generate_questions(entities: List[dict]) -> List[str]:
    """Generate one question per entity using rotating templates."""
    questions = []
    for i, entity in enumerate(entities):
        name = _entity_name(entity)
        if not name:
            continue
        template = _QUESTION_TEMPLATES[i % len(_QUESTION_TEMPLATES)]
        questions.append(template.format(name=name))
    return questions


@router.get("/suggestions", response_model=List[str])
async def get_suggestions(
    workspace_id: UUID = Query(..., description="Workspace UUID"),
    repo_path: Optional[str] = Query(None, description="Absolute path to the repo root"),
) -> List[str]:
    """
    Return exactly 4 suggested question chips.

    Priority:
      1. Generate from top brain entities (highest blast_radius / newest).
      2. Fall back to static defaults if brain is missing or empty.

    Always returns in ≤ 200 ms (fallback guards against slow I/O).
    """
    try:
        if repo_path:
            brain_dir = Path(repo_path) / ".brain"
            if brain_dir.is_dir():
                entities = _load_brain_entities(brain_dir)
                if entities:
                    top = _pick_top_entities(entities, _NUM_CHIPS)
                    questions = _generate_questions(top)
                    if len(questions) >= _NUM_CHIPS:
                        log.info(
                            "Suggestions generated from brain",
                            workspace_id=str(workspace_id),
                            repo_path=repo_path,
                            count=len(questions),
                        )
                        return questions[:_NUM_CHIPS]
    except Exception:
        log.warning(
            "Suggestions brain scan failed, using fallback",
            workspace_id=str(workspace_id),
            exc_info=True,
        )

    log.info(
        "Suggestions using static fallback",
        workspace_id=str(workspace_id),
        repo_path=repo_path,
    )
    return _FALLBACK_QUESTIONS
