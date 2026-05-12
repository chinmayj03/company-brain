"""ADR-0061 E5 — clarification round-trip on ambiguous queries.

The goal is small: detect the obvious ambiguity classes (a term that lives
both as a JSON key and a DB column, or both as a frontend prop and a
backend field) and *return* a structured prompt instead of answering wrong.

The detector is intentionally deterministic and fast — no LLM. It looks at
two signals only:

  1. Lexical: the question contains a "rename / change / what is" verb and
     names a token shorter than ~12 chars (the ambiguity classes are short
     business nouns: ``lob``, ``status``, ``user``).
  2. Brain-side: the term hits entities of multiple *distinct semantic
     buckets* (JSON-key, DB-column, API-field, frontend-prop) once we cross
     a minimum-population threshold.

If signal 1 fires AND signal 2 returns >= 2 buckets, we emit a structured
ClarificationResponse. Otherwise the caller proceeds to the normal answer
path. Clients (web, VS Code, CLI) render the response as quick-pick chips
and re-issue the question with ``QueryRequest.interpret`` set.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import structlog

log = structlog.get_logger(__name__)


# ── Bucket taxonomy ───────────────────────────────────────────────────────────
# Each bucket maps to a stable id the client passes back on the retry.

BUCKET_JSON_KEY   = "json_key"
BUCKET_DB_COLUMN  = "db_column"
BUCKET_API_FIELD  = "api_field"
BUCKET_FRONT_PROP = "frontend_prop"

BUCKET_LABELS: dict[str, str] = {
    BUCKET_JSON_KEY:   "JSON property in API requests/responses",
    BUCKET_DB_COLUMN:  "Database column",
    BUCKET_API_FIELD:  "API contract field (OpenAPI / Proto / GraphQL)",
    BUCKET_FRONT_PROP: "Frontend component prop or form input",
}

# Map entity types in the brain → buckets.
ENTITY_TYPE_TO_BUCKET: dict[str, str] = {
    "DatabaseColumn":  BUCKET_DB_COLUMN,
    "data_model":      BUCKET_DB_COLUMN,
    "OpenAPISchema":   BUCKET_API_FIELD,
    "OpenAPIOperation": BUCKET_API_FIELD,
    "ProtoMessage":    BUCKET_API_FIELD,
    "GraphQLType":     BUCKET_API_FIELD,
    "GraphQLField":    BUCKET_API_FIELD,
    "api_contract":    BUCKET_API_FIELD,
    "screen":          BUCKET_FRONT_PROP,
    "FrontendComponent": BUCKET_FRONT_PROP,
    "component":       BUCKET_FRONT_PROP,
}

_AMBIGUOUS_VERBS = (
    "rename", "change", "what is", "where is", "find the", "remove",
    "delete", "update", "rename the", "change the",
)

# Tokens we deliberately ignore — they are valid identifiers but never
# ambiguous business nouns.
_STOPWORDS = {
    "the", "a", "an", "is", "of", "in", "to", "on", "and", "or",
    "rename", "change", "delete", "update", "remove",
    "code", "field", "column", "key", "what", "where", "how", "find",
}

# Heuristic: terms shorter than this are the ones that tend to collide
# across layers (lob, status, user, payer).
_MIN_TOKEN_LEN = 2
_MAX_TOKEN_LEN = 14


@dataclass
class ClarificationResponse:
    """Structured response returned when the query is ambiguous."""
    ambiguous: bool = False
    term: str = ""
    interpretations: list[dict] = None              # type: ignore[assignment]
    suggested_followup: str = ""

    def to_dict(self) -> dict:
        return {
            "ambiguity": self.ambiguous,
            "term": self.term,
            "interpretations": self.interpretations or [],
            "suggested_followup": self.suggested_followup,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def detect_ambiguity(
    question: str,
    *,
    repo_path: Optional[str],
    workspace_id: str = "",
    min_buckets: int = 2,
) -> ClarificationResponse:
    """Return a non-empty ClarificationResponse iff the question is ambiguous.

    The decision happens locally — no LLM, no Neo4j round-trip. We grep the
    .brain/ snapshot on disk for entities matching the candidate term and
    count distinct buckets.
    """
    lowered = question.lower()
    if not any(verb in lowered for verb in _AMBIGUOUS_VERBS):
        return ClarificationResponse()
    candidate = _extract_candidate_token(question)
    if not candidate:
        return ClarificationResponse()
    brain_root = _resolve_brain_root(repo_path)
    if brain_root is None:
        return ClarificationResponse()
    buckets = _bucket_hits_for(candidate, brain_root)
    if len(buckets) < min_buckets:
        return ClarificationResponse()
    interpretations = _build_interpretations(candidate, buckets)
    return ClarificationResponse(
        ambiguous=True,
        term=candidate,
        interpretations=interpretations,
        suggested_followup=(
            f"/query --interpret=both '{question.strip()}'"
        ),
    )


def interpretation_hint(interpret: str, term: str = "") -> str:
    """Render a one-line prompt prefix the /query route injects into the
    user message when the client re-issues with a chosen interpretation."""
    if not interpret:
        return ""
    label = BUCKET_LABELS.get(interpret)
    if interpret == "both":
        return (
            "INTERPRETATION HINT: treat the question as referring to BOTH the "
            f"JSON/API key '{term}' AND the database column '{term}'. Cover "
            "both stacks in your answer."
        )
    if label:
        return (
            f"INTERPRETATION HINT: the user means the {label.lower()} "
            f"interpretation of '{term}'. Restrict the answer accordingly."
        )
    return ""


# ── Internals ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _extract_candidate_token(question: str) -> str:
    """Pick the most-likely ambiguous noun in the question.

    Strategy: take quoted tokens first (`'lob'` → ``lob``); otherwise pick
    the first short identifier that isn't a stopword. We bias toward the
    final identifier of the verb phrase because English puts the noun there
    in "rename the lob column".
    """
    # Quoted tokens win.
    quoted = re.findall(r"[`'\"]([A-Za-z_][A-Za-z0-9_]+)[`'\"]", question)
    for q in quoted:
        if _MIN_TOKEN_LEN <= len(q) <= _MAX_TOKEN_LEN and q.lower() not in _STOPWORDS:
            return q
    # Fall back: scan identifiers.
    tokens = _TOKEN_RE.findall(question)
    for t in tokens:
        if t.lower() in _STOPWORDS:
            continue
        if _MIN_TOKEN_LEN <= len(t) <= _MAX_TOKEN_LEN:
            return t
    return ""


def _bucket_hits_for(term: str, brain_root: Path) -> set[str]:
    """Return the distinct buckets where ``term`` appears as an entity name."""
    target = term.lower()
    hits: set[str] = set()
    try:
        # entity_type subdirs sit directly under .brain/
        for entity_dir in brain_root.iterdir():
            if not entity_dir.is_dir() or entity_dir.name.startswith("."):
                continue
            bucket = ENTITY_TYPE_TO_BUCKET.get(entity_dir.name)
            if not bucket:
                continue
            if _term_present_in_dir(entity_dir, target):
                hits.add(bucket)
    except OSError as e:
        log.debug("[clarification] bucket scan failed", error=str(e))
    # Extra heuristic: JSON keys live inside OpenAPI/Proto/GraphQL payloads
    # but the brain may not have promoted them to first-class entities. Look
    # for them inside ``api_contract`` blobs as a fallback.
    if BUCKET_JSON_KEY not in hits and BUCKET_API_FIELD in hits:
        if _term_appears_as_json_key(brain_root, target):
            hits.add(BUCKET_JSON_KEY)
    return hits


def _term_present_in_dir(entity_dir: Path, target: str) -> bool:
    try:
        for jf in entity_dir.glob("*.json"):
            try:
                blob = json.loads(jf.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            for key in ("qualified_name", "name"):
                val = blob.get(key, "")
                if isinstance(val, str) and target in val.lower():
                    return True
    except OSError:
        return False
    return False


def _term_appears_as_json_key(brain_root: Path, target: str) -> bool:
    api_dir = brain_root / "api_contract"
    if not api_dir.is_dir():
        return False
    needle = f'"{target}"'
    try:
        for jf in api_dir.glob("*.json"):
            try:
                if needle in jf.read_text().lower():
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _build_interpretations(term: str, buckets: Iterable[str]) -> list[dict]:
    ordered = [b for b in (
        BUCKET_JSON_KEY, BUCKET_API_FIELD, BUCKET_DB_COLUMN, BUCKET_FRONT_PROP,
    ) if b in buckets]
    options: list[dict] = []
    for b in ordered:
        options.append({
            "id": b,
            "description": f"{BUCKET_LABELS[b]} matching `{term}`",
        })
    if len(options) >= 2:
        options.append({
            "id": "both",
            "description": (
                f"Both — atomic operation across `{term}` everywhere it appears"
            ),
        })
    return options


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
