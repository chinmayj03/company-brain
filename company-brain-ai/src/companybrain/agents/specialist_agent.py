"""SpecialistAgent — single LLM call, no tools, no ReAct loop.

Replaces KnowledgeNavigatorAgent's 25-turn loop with a single
strategic planning call. Receives the full entry handler file +
a filtered repo manifest, returns a structured extraction plan.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.llm import get_provider, ChatMessage, TaskRole

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a code-aware extraction planner.

Given:
- An entry handler file (full content)
- A filtered manifest of candidate files in the repo

Return a JSON plan that lists which files + which methods within each
should be extracted by the downstream ContextAgent. Skip pure DTOs,
value objects, request/response shells — those are handled
structurally without an LLM call.

Output schema (compact, single-line JSON):
{
  "plan": [
    {"file": "...", "role": "controller|service|repository|model",
     "methods": ["m1", "m2"], "relevance": 0.0-1.0,
     "reason": "why this file matters for the endpoint"}
  ],
  "skip_dto": ["DtoName1", "DtoName2"]
}

Roles MUST be one of: controller, service, repository, model, util, test.
Relevance 1.0 = directly on the call chain; 0.5 = tangential helper.
methods[] must be EXACT method names from the file.
Skip files entirely if their relevance < 0.3.
Return only JSON, no prose before or after."""


@dataclass
class ExtractionPlan:
    plan: list[dict] = field(default_factory=list)   # [{file, role, methods, relevance, reason}]
    skip_dto: list[str] = field(default_factory=list) # DTO class names to fast-path


class SpecialistAgent:
    """Single-call extraction planner. No tools, no loop."""

    def __init__(self) -> None:
        self._provider = get_provider()

    async def plan(
        self,
        endpoint: str,
        http_method: str,
        entry_handler_path: str,
        candidate_files: list[tuple[str, str, int]],  # (path, role, size_kb)
    ) -> ExtractionPlan:
        """Return a structured plan for which files/methods to extract.

        One LLM call. Reads the entry handler from disk.
        candidate_files is the pre-filtered repo manifest (≤20 files).
        """
        try:
            entry_content = Path(entry_handler_path).read_text(errors="ignore")
        except OSError as exc:
            log.warning("SpecialistAgent: cannot read entry handler", path=entry_handler_path, error=str(exc))
            entry_content = ""

        manifest_md = self._build_manifest_table(candidate_files)
        user = self._build_user_prompt(endpoint, http_method, entry_handler_path, entry_content, manifest_md)

        log.info(
            "SpecialistAgent.plan calling LLM",
            endpoint=endpoint,
            http_method=http_method,
            candidate_count=len(candidate_files),
            entry_chars=len(entry_content),
        )

        try:
            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user),
                ],
                role=TaskRole.BALANCED,
                max_tokens=2_000,
            )
            plan = self._parse(raw)
        except Exception as exc:
            log.warning("SpecialistAgent: LLM call failed, returning empty plan", error=str(exc))
            plan = ExtractionPlan()

        log.info(
            "SpecialistAgent.plan complete",
            plan_files=len(plan.plan),
            skip_dtos=len(plan.skip_dto),
        )
        return plan

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_manifest_table(self, files: list[tuple[str, str, int]]) -> str:
        """Markdown table — LLM parses this natively, ~40% smaller than JSON."""
        rows = ["| file | role | size_kb |", "|---|---|---|"]
        for path, role, size_kb in files:
            rows.append(f"| {path} | {role} | {size_kb} |")
        return "\n".join(rows)

    def _build_user_prompt(
        self,
        endpoint: str,
        method: str,
        handler_path: str,
        handler_content: str,
        manifest_md: str,
    ) -> str:
        return (
            f"<endpoint>{method} {endpoint}</endpoint>\n\n"
            f'<entry_handler path="{handler_path}">\n'
            f"{handler_content}\n"
            f"</entry_handler>\n\n"
            f"<repo_manifest>\n"
            f"{manifest_md}\n"
            f"</repo_manifest>"
        )

    def _parse(self, raw: str) -> ExtractionPlan:
        # Strip markdown fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try extracting first JSON object from the string
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    log.warning("SpecialistAgent: could not parse plan JSON", raw=raw[:200])
                    return ExtractionPlan()
            else:
                log.warning("SpecialistAgent: no JSON object in response", raw=raw[:200])
                return ExtractionPlan()

        raw_plan = data.get("plan", [])
        # Defensive: ensure each entry is a dict
        plan = [p for p in raw_plan if isinstance(p, dict)]
        skip_dto = [s for s in data.get("skip_dto", []) if isinstance(s, str)]
        return ExtractionPlan(plan=plan, skip_dto=skip_dto)
