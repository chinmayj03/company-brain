"""
GapDetector — LLM Pass 4 of the context builder pipeline.

Detects unexplained behaviours and annotation conflicts.
Single LLM call with a summary of entities + annotated commits.
"""
from __future__ import annotations

import json

import structlog
from tenacity import retry
from companybrain.pipeline._retry import SYNTHESIS_RETRY

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.config import settings
from companybrain.models.entities import (
    ExtractedEntity, CommitCluster, PipelineGap, BusinessContext
)

log = structlog.get_logger(__name__)

GAP_DETECTION_SYSTEM_PROMPT = """You are a senior software engineer auditing a codebase for knowledge gaps — things that
cannot be understood from the code and its history alone, or where existing documentation
contradicts actual code behavior.

━━━ YOUR TASK ━━━
Review the provided entities, their synthesised business contexts, commit history, and
human annotations. Identify gaps that a new engineer would hit when trying to safely modify
this code. Flag only GENUINE, HIGH-SIGNAL issues — not minor ambiguities.

━━━ OUTPUT FORMAT ━━━
Return ONLY valid JSON — start with { end with }. No markdown, no preamble.

{
  "gaps": [
    {
      "entity": "<exact entity_name from the list>",
      "gap_type": "<one of the 5 types below>",
      "severity": "critical|high|medium",
      "description": "<precise description of the gap or conflict>",
      "suggested_question": "<specific, answerable question for a human SME>"
    }
  ]
}

━━━ GAP TYPE REFERENCE ━━━

unexplained_behaviour — The code does something that cannot be explained by any available
  context: commit messages, PR descriptions, or human annotations.
  Trigger when: magic numbers, silent failure paths, undocumented state transitions,
                domain-specific filtering logic with no rationale.
  Example: "getPayerCompetitors silently returns empty list when providerTypes is null,
            but there is no documented contract for this behavior."

annotation_vs_code — A human annotation or PR description claims X, but the code
  clearly does Y. This is a stale-documentation hazard.
  Trigger when: annotation says "always returns sorted results" but there is no ORDER BY;
                PR says "removes the 10-row limit" but LIMIT 10 is still in the query.
  Cite the SPECIFIC contradiction — entity name, annotation claim, and what code shows.

missing_owner — Entity is HIGH change_risk with no determinable owner team.
  Trigger when: multiple teams have committed (unclear ownership), no PR has a team label,
                no @owner annotation, and change_risk is HIGH.
  Do NOT flag LOW or MEDIUM risk orphans — only flag HIGH risk without an owner.

untested_critical_path — Entity is HIGH change_risk AND has no TESTED_BY edges AND the
  business context indicates it handles money, auth, or external compliance.
  This is the highest-priority gap type.
  Example: "createCharge has no test coverage; it writes to the charges table and
            publishes a billing event, making it critical and unverifiable."

data_contract_ambiguity — The entity's inputs/outputs have implicit contracts that are
  not enforced in code and not documented anywhere: valid enum values, nullable fields
  treated as non-null, implicit ordering assumptions, max result size expectations.
  Trigger when: a field is used as a filter/key without any null check or validation;
                an enum-like string parameter has no exhaustive handling.
  Example: "lob parameter in getPayerCompetitors has no null guard and no documented
            list of valid values; invalid lob silently returns empty results."

━━━ SEVERITY CALIBRATION ━━━
critical — Would cause silent data corruption, wrong billing, security bypass, or
           complete service outage if misunderstood. Flag and escalate immediately.
high     — Would cause a P1/P2 incident if modified without the missing knowledge.
           A new engineer could easily make a breaking change.
medium   — Causes confusion or slower development; unlikely to cause immediate outage.
           Surface for annotation but not urgent.

━━━ RULES ━━━
• Only use entity names from the provided list — exact spelling.
• Flag each (entity, gap_type) pair at most ONCE — no duplicates.
• Maximum 10 gaps per call — prioritize by severity.
• If no significant gaps exist, return {"gaps": []}.
• Do NOT flag: normal code complexity, missing JavaDoc, style issues, missing logging.
  Those are not knowledge gaps.

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — untested_critical_path:
Entity: createCharge (Function)
Context: purpose="Persists a charge and publishes billing event", change_risk=HIGH,
         no testedBy edges found, writes to charges table.
Gap:
{
  "entity": "createCharge",
  "gap_type": "untested_critical_path",
  "severity": "critical",
  "description": "createCharge writes to the charges table and publishes a ChargeCreatedEvent with no detected test coverage. A regression here would corrupt billing records silently.",
  "suggested_question": "Does the billing team have integration tests for createCharge outside this repo? If not, what is the safe-change procedure?"
}

EXAMPLE 2 — data_contract_ambiguity:
Entity: getPayerCompetitors (Function)
Context: takes List<String> providerTypes, no null check visible, returns empty list on null.
Gap:
{
  "entity": "getPayerCompetitors",
  "gap_type": "data_contract_ambiguity",
  "severity": "high",
  "description": "providerTypes parameter has no null guard; passing null silently returns an empty list rather than all provider types, but callers cannot know this without reading the implementation.",
  "suggested_question": "What is the intended behavior when providerTypes is null or empty — all types, or an error? This should be in the API contract."
}

EXAMPLE 3 — annotation_vs_code:
Entity: findByLobAndStates (DatabaseQuery)
Annotation: "Returns results sorted by market share descending"
Code: Query has no ORDER BY clause.
Gap:
{
  "entity": "findByLobAndStates",
  "gap_type": "annotation_vs_code",
  "severity": "high",
  "description": "PR annotation claims results are sorted by market share descending, but the query has no ORDER BY clause. The sort either happens in a caller (undocumented) or the annotation is stale.",
  "suggested_question": "Is sorting done by a caller of findByLobAndStates, or is the annotation stale? If the latter, the annotation should be removed to prevent misleading consumers."
}

EXAMPLE 4 — unexplained_behaviour:
Entity: processPayment (Function)
Context: silently swallows FraudException and returns a success response when fraud score < 0.
Gap:
{
  "entity": "processPayment",
  "gap_type": "unexplained_behaviour",
  "severity": "critical",
  "description": "When fraudService.assess() throws FraudException with score < 0 (negative score), processPayment catches the exception and returns a 200 OK with an empty charge. No comment or ticket explains why negative scores are treated as success.",
  "suggested_question": "Is a negative fraud score a 'fraud system unavailable' sentinel that should allow the payment to proceed, or is this a bug? This requires immediate clarification from the Fraud team."
}

CRITICAL: Output ONLY the raw JSON object. Start with { end with }. Nothing else.
"""


class GapDetector:
    """
    Runs LLM Pass 4 (gap detection) in a single LLM call.
    Identifies unexplained behaviours and annotation conflicts.
    """

    def __init__(self):
        self._provider = get_provider()
        log.info("GapDetector ready", llm_provider=self._provider.provider_name,
                 model=self._provider.model_for_role(TaskRole.REASONING))

    @retry(**SYNTHESIS_RETRY)
    async def detect(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        annotations: list[dict],
        business_contexts: dict[str, BusinessContext],
    ) -> list[PipelineGap]:
        """
        Detect gaps and conflicts across all entities.
        Returns a list of PipelineGap.
        """
        if not entities:
            return []

        user_content = self._build_user_message(entities, clusters, annotations, business_contexts)

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=GAP_DETECTION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_content),
            ],
            role=TaskRole.FAST,        # JSON classification task — Haiku is sufficient.
                                   # Was SYNTHESIS (Opus on Anthropic) at $15/$75 per MTok,
                                   # accounting for ~$0.21–0.35 per endpoint run alone.
                                   # Groq note: if Groq rate-limits FAST, switch to BALANCED.
            max_tokens=settings.max_tokens_gap_detection,
        )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("Failed to parse gap detection JSON", error=str(e), raw=raw[:200])
            return []

        entity_name_to_id = {e.name: e.external_id for e in entities}

        _VALID_GAP_TYPES = frozenset({
            "unexplained_behaviour", "annotation_vs_code", "missing_owner",
            "untested_critical_path", "data_contract_ambiguity",
        })
        _VALID_SEVERITIES = frozenset({"critical", "high", "medium"})
        _RESOLUTION_NEEDED_TYPES = frozenset({
            "annotation_vs_code", "missing_owner", "untested_critical_path",
        })

        gaps = []
        for item in data.get("gaps", []):
            try:
                entity_name = item["entity"]
                external_id = entity_name_to_id.get(entity_name, entity_name)

                gap_type = item.get("gap_type", "unexplained_behaviour")
                if gap_type not in _VALID_GAP_TYPES:
                    gap_type = "unexplained_behaviour"

                severity = item.get("severity", "medium")
                if severity not in _VALID_SEVERITIES:
                    severity = "medium"

                gaps.append(PipelineGap(
                    entity_external_id=external_id,
                    gap_type=gap_type,
                    description=item.get("description", ""),
                    suggested_question=item.get("suggested_question"),
                    severity=severity,
                    resolution_needed=gap_type in _RESOLUTION_NEEDED_TYPES,
                ))
            except (KeyError, ValueError) as e:
                log.debug("Skipping malformed gap", error=str(e), item=item)

        log.info("Gap detection complete", gaps=len(gaps))
        return gaps

    def _build_user_message(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        annotations: list[dict],
        business_contexts: dict[str, BusinessContext],
    ) -> str:
        """Build the user message for the LLM call."""
        parts = [f"## Entities ({len(entities)} total)"]

        for entity in entities:
            ctx = business_contexts.get(entity.external_id)
            parts.append(f"\n### [{entity.entity_type}] {entity.name}")
            parts.append(f"File: {entity.file} | Repo: {entity.repo}")
            if entity.signature:
                parts.append(f"Signature: `{entity.signature}`")
            if ctx:
                parts.append(f"Purpose: {ctx.purpose}")
                parts.append(f"Change Risk: {ctx.change_risk} — {ctx.change_risk_reason}")
                if ctx.owner_team:
                    parts.append(f"Owner: {ctx.owner_team}")
                if ctx.gaps:
                    parts.append(f"Known gaps: {', '.join(ctx.gaps)}")

        if annotations:
            parts.append(f"\n## Human Annotations ({len(annotations)})")
            for ann in annotations[:20]:
                parts.append(
                    f"- [{ann.get('annotation_type', 'note')}] {ann.get('entity_name', '?')}: "
                    f"{ann.get('text', '')[:300]}"
                )

        # Include a brief summary of annotated commits
        annotated_commits = [
            c for cluster in clusters
            for c in cluster.commits
            if c.pr_body and len(c.pr_body) > 100
        ]
        if annotated_commits:
            parts.append(f"\n## Annotated Commits ({len(annotated_commits)} with rich PR descriptions)")
            for commit in annotated_commits[:5]:
                parts.append(f"\n### {commit.commit_hash[:7]} — {commit.message[:100]}")
                if commit.pr_title:
                    parts.append(f"PR: {commit.pr_title}")
                if commit.pr_body:
                    parts.append(f"PR Body: {commit.pr_body[:400]}")

        return "\n".join(parts)
