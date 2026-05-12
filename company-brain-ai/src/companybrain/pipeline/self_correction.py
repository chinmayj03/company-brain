"""
ADR-0056 Mode C — self-correction loop.

Triggered when Mode B flags an entity as wrong AND the original extraction had
high confidence on a high-stakes claim (database query, external service call,
etc.). Re-invokes a focused extractor with augmented retry context and
replaces the disputed fields on the original entity.

If the re-extracted output ALSO disagrees with the verifier (or fails to
return), the entity is marked ``verified="conflicting"`` so it stays out of
public reads but remains in the DB for telemetry / human review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

from companybrain.agents.verifier_agent import VerifierAgent
from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.verifier_deterministic import _read_source

log = structlog.get_logger(__name__)


HIGH_STAKES_TYPES = {
    "DatabaseQuery",
    "ExternalService",
    "ApiEndpoint",
}

SELF_CORRECTION_CONFIDENCE_FLOOR = 0.8


_RETRY_SYSTEM_PROMPT = """\
You are re-extracting a single code entity because a verifier disputed the
prior extraction. Re-examine the supplied SOURCE excerpt and emit corrected
fields. Do NOT invent text that is not present in the source.

Return a single compact JSON object — no prose, no markdown:
{"query_text": "<verbatim from source or empty>",
 "code_snippet": "<1-2 line verbatim excerpt from source or empty>",
 "confidence": 0.0-1.0,
 "notes": "one-line explanation"}

If the source does not actually contain the claim, leave query_text empty,
emit a low confidence (<0.5), and explain in notes.
"""


@dataclass
class SelfCorrectionResult:
    """Outcome of one Mode-C re-extraction attempt."""

    accepted: bool             # True ↔ the rewrite replaced fields on the entity
    new_query_text: str = ""
    new_code_snippet: str = ""
    new_confidence: float = 0.0
    notes: str = ""
    # Whether the rewrite ALSO disagreed with the verifier. When True the
    # caller should mark the entity ``verified="conflicting"``.
    still_conflicting: bool = False


def is_high_stakes(entity: ExtractedEntity) -> bool:
    """ADR-0056: re-extraction fires only for entities whose disputed claim
    materially affects downstream answers — primarily database queries and
    external service calls."""
    if entity.entity_type in HIGH_STAKES_TYPES:
        return True
    if entity.query_text and entity.query_text.strip():
        return True
    return False


def should_self_correct(
    entity: ExtractedEntity,
    verifier_said_no: bool,
) -> bool:
    """ADR-0056 trigger criteria, all three must hold:
       - original confidence ≥ 0.8
       - verifier explicitly disagreed (NO, not PARTIAL/uncertain)
       - entity is high-stakes
    """
    return (
        verifier_said_no
        and entity.confidence >= SELF_CORRECTION_CONFIDENCE_FLOOR
        and is_high_stakes(entity)
    )


class SelfCorrector:
    """Re-extraction driver. Holds a provider handle and a VerifierAgent so
    that a single Mode-C cycle (retry → re-verify) reuses one round of model
    state without re-paying provider initialisation."""

    def __init__(self, verifier: Optional[VerifierAgent] = None) -> None:
        # Lazy provider — see VerifierAgent for the same rationale.
        self._provider = None
        self._verifier = verifier or VerifierAgent()

    async def recorrect(
        self,
        entity: ExtractedEntity,
        verifier_reason: str,
        source_roots: list[Path],
    ) -> SelfCorrectionResult:
        source = _read_source(entity.file, source_roots) or ""
        if not source:
            return SelfCorrectionResult(
                accepted=False,
                notes=f"source file unreadable: {entity.file}",
                still_conflicting=True,
            )

        prior_claim = entity.query_text or entity.code_snippet or ""
        user_msg = (
            f"<retry_context reason=\"prior_extraction_disputed_by_verifier\">\n"
            f"  Entity: {entity.entity_type} {entity.name}\n"
            f"  File: {entity.file}\n"
            f"  Original claim: {prior_claim}\n"
            f"  Verifier said NO because: {verifier_reason}\n"
            f"</retry_context>\n"
            f"<source>\n{source}\n</source>"
        )

        if self._provider is None:
            self._provider = get_provider()
        try:
            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=_RETRY_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user_msg),
                ],
                role=TaskRole.FAST,
                max_tokens=400,
            )
        except Exception as exc:
            log.warning("[self_correction] retry LLM failed", error=str(exc))
            return SelfCorrectionResult(
                accepted=False,
                notes=f"retry llm failed: {exc}",
                still_conflicting=True,
            )

        parsed = _parse_retry_payload(raw)
        if parsed is None:
            return SelfCorrectionResult(
                accepted=False,
                notes="retry payload not parseable",
                still_conflicting=True,
            )

        # Re-verify the new claim. If the verifier ALSO disputes it, mark
        # conflicting rather than silently accepting a second hallucination.
        new_claim = parsed["query_text"] or parsed["code_snippet"]
        if new_claim:
            verdict = await self._verifier.verify(
                claim=new_claim, source_excerpt=source, file_path=entity.file,
            )
            if verdict.result == "NO":
                return SelfCorrectionResult(
                    accepted=False,
                    new_query_text=parsed["query_text"],
                    new_code_snippet=parsed["code_snippet"],
                    new_confidence=parsed["confidence"],
                    notes=f"retry still disputed: {verdict.reason}",
                    still_conflicting=True,
                )

        return SelfCorrectionResult(
            accepted=True,
            new_query_text=parsed["query_text"],
            new_code_snippet=parsed["code_snippet"],
            new_confidence=parsed["confidence"],
            notes=parsed["notes"],
            still_conflicting=False,
        )


def _parse_retry_payload(raw: str) -> Optional[dict]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "query_text":   str(data.get("query_text", "") or ""),
        "code_snippet": str(data.get("code_snippet", "") or ""),
        "confidence":   max(0.0, min(1.0, confidence)),
        "notes":        str(data.get("notes", "") or ""),
    }
