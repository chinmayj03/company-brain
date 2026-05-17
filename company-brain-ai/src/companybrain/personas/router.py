"""
Persona router — ADR-0079 M3 (rule-based, Phase 1).

Three-stage routing:
  Stage 1 — Persona inference:
    a) Explicit @persona token in query (highest priority)
    b) Caller-supplied persona parameter
    c) Keyword classifier on query text

  Stage 2 — Shape match:
    Keyword overlap between query and each shape's intent_examples.
    Top-1 match; confidence threshold 0.5.

  Stage 3 — Return RouterResult with matched shape + confidence.
    If confidence < MATCH_THRESHOLD → fell_through_to_generic = True.

Phase 2 will replace Stage 1c with an LLM classifier trained on M5 data.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from companybrain.personas.shape import QuestionShape

log = logging.getLogger(__name__)

# Minimum overlap score to claim a shape match.
MATCH_THRESHOLD = 0.5


@dataclass
class RouterResult:
    """Output from the persona router."""
    persona: str                              # inferred/selected persona
    shape: Optional[QuestionShape] = None     # matched shape (None = generic fallback)
    match_confidence: float = 0.0
    fell_through_to_generic: bool = False
    persona_source: str = "keyword"           # "explicit", "param", "keyword", "default"


# ── Stage 1: Keyword classifier rules ────────────────────────────────────────
# Maps keyword sets to persona labels. Checked in order; first match wins.

_PM_KEYWORDS: set[str] = {
    "ship", "estimate", "feature", "roadmap", "sprint", "quarter", "delivery",
    "progress", "status", "milestone", "blocking", "blocked", "backlog",
    "release", "launch", "commit", "promise", "customer", "stakeholder",
    "product", "scope", "timeline", "planning", "deliverable", "shipped",
    "shipped", "completing", "eta",
}

_DEV_KEYWORDS: set[str] = {
    "blast radius", "implementation", "implements", "pattern", "refactor",
    "change", "break", "breaks", "breaking", "dependency", "depends",
    "architecture", "design", "service", "module", "class", "function",
    "method", "domain", "entity", "meaning", "owns", "ownership",
    "codebase", "decided", "decision", "rationale", "why built",
    "who wrote", "who knows", "similar code", "existing pattern",
}

_VP_KEYWORDS: set[str] = {
    "drift", "debt", "hotspot", "area health", "bus factor", "bus-factor",
    "capacity", "load", "velocity", "estimate vs actual", "tech debt",
    "accumulating", "drifting", "diverging", "divergence", "degradation",
    "quality", "single point", "knowledge", "concentration", "risk",
    "trend", "recent changes", "what changed",
}

# Explicit @persona tokens in query text.
_EXPLICIT_TOKEN_MAP: dict[str, str] = {
    "@pm": "pm",
    "@dev": "dev",
    "@developer": "dev",
    "@vp": "vp_eng",
    "@vp_eng": "vp_eng",
    "@vpeng": "vp_eng",
    "@cs": "cs",
    "@cfo": "cfo",
    "@ceo": "ceo",
}


def infer_persona(question: str, persona_param: Optional[str] = None) -> tuple[str, str]:
    """
    Infer the persona for a query.

    Returns (persona_name, source) where source is one of:
    "explicit", "param", "keyword", "default"

    Precedence:
      1. Explicit @persona token in the query text
      2. persona_param supplied by the caller (e.g., from the frontend PersonaSelector)
      3. Keyword classifier on the query text
      4. Default "dev" if nothing matches
    """
    q_lower = question.lower()

    # Stage 1a: explicit @persona token
    for token, persona in _EXPLICIT_TOKEN_MAP.items():
        if token in q_lower:
            log.debug("[router] Explicit persona token found: %s → %s", token, persona)
            return persona, "explicit"

    # Stage 1b: caller-supplied param
    if persona_param:
        normalized = persona_param.lower().strip()
        # Normalize aliases
        if normalized in ("vp", "vpeng", "vp eng"):
            normalized = "vp_eng"
        if normalized in ("developer", "engineer", "dev"):
            normalized = "dev"
        if normalized in ("pm", "product", "product manager"):
            normalized = "pm"
        if normalized in ("cs", "customer success", "support"):
            normalized = "cs"
        if normalized in ("cfo", "finance", "financial"):
            normalized = "cfo"
        if normalized in ("ceo", "exec", "executive"):
            normalized = "ceo"
        valid = {"dev", "pm", "cs", "vp_eng", "cfo", "ceo"}
        if normalized in valid:
            log.debug("[router] Persona from param: %s", normalized)
            return normalized, "param"

    # Stage 1c: keyword classifier
    words = set(re.findall(r'\b\w+\b', q_lower))
    # Also check multi-word phrases
    for phrase in _DEV_KEYWORDS:
        if " " in phrase and phrase in q_lower:
            words.add(phrase)
    for phrase in _VP_KEYWORDS:
        if " " in phrase and phrase in q_lower:
            words.add(phrase)

    # Score each persona by keyword overlap
    pm_score = len(words & _PM_KEYWORDS)
    dev_score = len(words & {w for w in _DEV_KEYWORDS if " " not in w}) + \
                sum(1 for p in _DEV_KEYWORDS if " " in p and p in q_lower)
    vp_score = len(words & {w for w in _VP_KEYWORDS if " " not in w}) + \
               sum(1 for p in _VP_KEYWORDS if " " in p and p in q_lower)

    best_score = max(pm_score, dev_score, vp_score)
    if best_score > 0:
        if vp_score == best_score:
            return "vp_eng", "keyword"
        elif pm_score == best_score:
            return "pm", "keyword"
        else:
            return "dev", "keyword"

    # Default
    return "dev", "default"


def match_shape(
    question: str,
    persona: str,
    shapes: dict[str, QuestionShape],
) -> tuple[Optional[QuestionShape], float]:
    """
    Find the best-matching QuestionShape for a query within a persona.

    Returns (shape, confidence) where confidence is 0.0-1.0.
    Returns (None, 0.0) if no shape meets the MATCH_THRESHOLD.

    Matching algorithm: token overlap between the query and each shape's
    intent_examples. Score = matched_tokens / max(query_tokens, example_tokens).
    Take the maximum score across all examples for a shape.
    """
    q_lower = question.lower()
    # Remove @persona tokens from the query for shape matching
    for token in _EXPLICIT_TOKEN_MAP:
        q_lower = q_lower.replace(token, "")
    q_tokens = set(re.findall(r'\b\w+\b', q_lower)) - _STOPWORDS

    persona_shapes = [s for s in shapes.values() if s.persona == persona]
    if not persona_shapes:
        log.debug("[router] No shapes loaded for persona=%s", persona)
        return None, 0.0

    best_shape: Optional[QuestionShape] = None
    best_score: float = 0.0

    for shape in persona_shapes:
        shape_score = 0.0
        for example in shape.intent_examples:
            ex_tokens = set(re.findall(r'\b\w+\b', example.lower())) - _STOPWORDS
            if not ex_tokens:
                continue
            overlap = len(q_tokens & ex_tokens)
            denominator = max(len(q_tokens), len(ex_tokens), 1)
            score = overlap / denominator
            if score > shape_score:
                shape_score = score
        if shape_score > best_score:
            best_score = shape_score
            best_shape = shape

    if best_score >= MATCH_THRESHOLD:
        log.debug(
            "[router] Shape matched: %s (confidence=%.2f)",
            best_shape.id if best_shape else "none",
            best_score,
        )
        return best_shape, best_score

    log.debug(
        "[router] No shape met threshold (best=%.2f < %.2f) for persona=%s",
        best_score,
        MATCH_THRESHOLD,
        persona,
    )
    return None, best_score


def route(
    question: str,
    persona_param: Optional[str] = None,
    shapes: Optional[dict[str, QuestionShape]] = None,
) -> RouterResult:
    """
    Full routing: infer persona → match shape → return RouterResult.

    If shapes is None, loads templates from disk on first call.
    """
    if shapes is None:
        from companybrain.personas.templates import load_all_templates
        shapes = load_all_templates()

    persona, persona_source = infer_persona(question, persona_param)

    shape, confidence = match_shape(question, persona, shapes)

    return RouterResult(
        persona=persona,
        shape=shape,
        match_confidence=confidence,
        fell_through_to_generic=(shape is None),
        persona_source=persona_source,
    )


# ── Common English stopwords to ignore in token overlap ─────────────────────

_STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "into", "about", "against", "between", "through", "during", "before",
    "after", "above", "below", "up", "down", "out", "off", "over", "under",
    "again", "then", "so", "but", "or", "and", "nor", "not", "if", "as",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they",
    "them", "their", "its", "how", "where", "when", "why", "how",
}
