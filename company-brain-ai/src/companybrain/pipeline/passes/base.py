"""
ExtractionPass — abstract base for ADR-0042 LLM pattern-recognizer passes.

Every pass:
  1. Checks BRAIN_SKIP_<NAME> env flag and returns [] early if set.
  2. Builds a per-call user message from the provided entities.
  3. Calls provider.chat_json() with a static, cached system prompt.
  4. Parses the response via a Pydantic model, retrying once on failure.
  5. Converts to list[ExtractedRelationship].
  6. Logs ENTER rows=N and OK edges=N (or FAILED error=…).
  7. Appends a stage_summary dict for the orchestrator's progress log.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import ClassVar

import structlog

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship

log = structlog.get_logger(__name__)


class ExtractionPass(ABC):
    """
    Abstract base for all ADR-0042 extraction passes.

    Subclasses must define:
      name       — used for logs + BRAIN_SKIP_<NAME> env flag
      role       — TaskRole to use (default FAST = Haiku)
      max_tokens — per-call output token cap
      system_prompt — static system prompt (cached via cache_control:ephemeral)

    And implement:
      _build_user_message(entities) → str
      _parse_response(raw_json, entities) → list[ExtractedRelationship]
    """

    name: ClassVar[str]
    role: ClassVar[TaskRole] = TaskRole.FAST
    max_tokens: ClassVar[int] = 800
    system_prompt: ClassVar[str] = ""

    def __init__(self) -> None:
        self._provider = get_provider()

    async def run(
        self,
        entities: list[ExtractedEntity],
    ) -> tuple[list[ExtractedRelationship], dict]:
        """
        Run this pass against the provided entity list.

        Returns (relationships, stage_summary_dict).
        The stage_summary dict follows the ADR-0042 logging contract.
        """
        skip_var = f"BRAIN_SKIP_{self.name.upper()}"
        if os.environ.get(skip_var, "").lower() in ("1", "true", "yes"):
            log.info(f"{self.name} SKIPPED via env", skip_flag=skip_var)
            return [], {
                "stage": self.name,
                "label": self.name,
                "edges_emitted": 0,
                "skipped_via_env": True,
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }

        log.info(f"{self.name} ENTER", rows=len(entities))
        t0 = time.perf_counter()
        input_tokens = output_tokens = 0

        try:
            user_msg = self._build_user_message(entities)
            if not user_msg.strip():
                log.info(f"{self.name} OK edges=0 (nothing to process)")
                return [], self._summary(0, t0, 0, 0, skipped_via_env=False)

            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=self.system_prompt),
                    ChatMessage(role="user",   content=user_msg),
                ],
                role=self.role,
                max_tokens=self.max_tokens,
            )

            # Try to parse; retry once with corrective prompt on failure
            try:
                rels = self._parse_response(raw, entities)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as parse_err:
                log.warning(f"{self.name} parse failed — retrying", error=str(parse_err))
                corrective = (
                    f"{user_msg}\n\n"
                    f"PREVIOUS RESPONSE (INVALID JSON): {raw[:200]}\n"
                    f"Return ONLY valid JSON matching the schema. No prose."
                )
                raw = await self._provider.chat_json(
                    messages=[
                        ChatMessage(role="system", content=self.system_prompt),
                        ChatMessage(role="user",   content=corrective),
                    ],
                    role=self.role,
                    max_tokens=self.max_tokens,
                )
                rels = self._parse_response(raw, entities)

            # Drop low-confidence edges (< 0.7)
            rels = [r for r in rels if r.confidence >= 0.7]

            log.info(f"{self.name} OK", edges=len(rels))
            return rels, self._summary(len(rels), t0, input_tokens, output_tokens)

        except Exception as exc:
            log.error(f"{self.name} FAILED", error=str(exc))
            return [], self._summary(0, t0, input_tokens, output_tokens, error=str(exc))

    def _summary(
        self,
        edges: int,
        t0: float,
        input_tokens: int,
        output_tokens: int,
        *,
        skipped_via_env: bool = False,
        error: str | None = None,
    ) -> dict:
        return {
            "stage":          self.name,
            "label":          self.name,
            "edges_emitted":  edges,
            "skipped_via_env": skipped_via_env,
            "duration_ms":    round((time.perf_counter() - t0) * 1000),
            "input_tokens":   input_tokens,
            "output_tokens":  output_tokens,
            **({"error": error} if error else {}),
        }

    @abstractmethod
    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        """Build the user-turn message for this pass."""
        ...

    @abstractmethod
    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        """Parse LLM JSON response into relationships."""
        ...
