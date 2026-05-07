"""
ContextSynthesizer — LLM Pass 3 of the context builder pipeline.

For each extracted entity, synthesises a business context block using:
  - Commit history for that entity
  - PR descriptions and ticket summaries
  - User annotations (highest signal)
  - Related entities and their contexts

Uses Claude Sonnet/Opus — this is the highest-value pass and
quality matters more than cost here.

See PIPELINE-api-context-builder.md Section 5.4 for full design.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import structlog
from tenacity import retry
from companybrain.pipeline._retry import SYNTHESIS_RETRY

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.config import settings
from companybrain.models.entities import ExtractedEntity, BusinessContext, CommitCluster

log = structlog.get_logger(__name__)

CONTEXT_SYNTHESIS_PROMPT = """You are a senior software engineer building institutional memory for a production codebase.

━━━ YOUR ROLE ━━━
Given an entity (function, API endpoint, component, query, etc.) plus all available context —
code body, commit history, PR descriptions, ticket summaries, human annotations — you will
synthesise a precise business context record that captures what this entity DOES, WHY it
exists, HOW it evolved, and WHAT risks it carries.

This record will be stored permanently in a knowledge graph and used by engineers to
understand code they've never seen before. Precision and honesty matter more than completeness.

━━━ OUTPUT FORMAT ━━━
Return ONLY valid JSON — start with { end with }. No markdown, no preamble, nothing else.

{
  "purpose": "<1–2 sentences: what this entity does + why it exists in the system>",
  "history_summary": "<2–3 sentences: how it evolved, key decisions made, major refactors>",
  "invariants": ["<business rule that MUST always be true for this entity to be correct>"],
  "change_risk": "LOW|MEDIUM|HIGH",
  "change_risk_reason": "<specific reason — what breaks, which team is affected, why this risk level>",
  "owner_team": "<team name if determinable from PR/commit authors, else null>",
  "external_dependencies": ["<external service, queue, or system this entity relies on>"],
  "source_confidence": "high|medium|low",
  "gaps": ["<specific thing you could NOT infer — phrase as a question for a human>"]
}

━━━ FIELD GUIDANCE ━━━

purpose — The most important field. Answer: what does this entity do, and why does it exist?
  • Include the business domain (e.g. "billing", "competitor analysis", "claims processing").
  • Mention return value type if meaningful (e.g. "returns a paginated list", "returns void").
  • BAD: "This function processes data." / "Handles competitor information."
  • GOOD: "Returns all payer competitors for a given line of business and provider type,
           used by the competitor analysis dashboard to display market positioning."
  • GOOD: "Persists a new charge record in PENDING state and publishes a ChargeCreatedEvent
           to trigger downstream billing processing."

history_summary — Synthesize change patterns from commit history:
  • Note when major changes happened and why (refactors, bug fixes, new requirements).
  • Note if the entity is STABLE (few commits = either well-designed or forgotten).
  • If no commit history: "No commit history available; entity appears stable or recently added."
  • BAD: "This function was changed multiple times."
  • GOOD: "Added in Q3 2023 to support multi-state competitor querying. Refactored in
           Jan 2024 to use jOOQ after the JPA N+1 performance issue in JIRA-4421."

invariants — Business rules that MUST be true for correctness. Derive from:
  • Null checks (null means "always non-null in production")
  • Enum constraints (only specific values are valid)
  • @NotNull / @Size / @Valid annotations
  • If-then-throw patterns (invariant violation → exception)
  • Transaction boundaries (must be atomic with another operation)
  • Examples:
    - "Input lob must be a non-empty string; null lob will cause NPE in query layer"
    - "amountCents must be > 0; the code does not handle zero-amount charges"
    - "Each charge must belong to exactly one payer; orphaned charges are invalid"
  • Return [] if no invariants are extractable from the code.

change_risk — Pick from LOW / MEDIUM / HIGH using this rubric:
  HIGH:   Called by many other components (hub node) OR touches a payment/billing/auth
          critical path OR has no test coverage and is complex OR external API surface.
  MEDIUM: Moderate call depth, has some tests, changes affect one team.
  LOW:    Utility/helper with few callers, well-tested, isolated from critical path.

change_risk_reason — Be specific:
  BAD:  "This is risky because it is important."
  GOOD: "HIGH — called by 5+ services; changes to the return type would break the
         competitor dashboard, pricing API, and the nightly reporting job."
  GOOD: "LOW — pure data mapping function with no side effects and 4 unit tests covering
         all enum branches."

external_dependencies — Only EXTERNAL systems (not internal services):
  • Include: AWS SQS/SNS/S3, external APIs (Salesforce, Stripe, third-party data feeds),
             databases in different services, Redis, Kafka topics.
  • Exclude: calls to internal microservices within the same org (those are CALLS edges).
  • Return [] if none.

source_confidence — Set automatically by caller, but you can override:
  high   — Human annotation is present and explicit for this entity.
  medium — Rich PR description or ticket summary (>100 chars) references this entity.
  low    — Inferred from code and short commit messages only.

gaps — Surface honest unknowns as questions for human annotation:
  • What business rules exist that the code doesn't capture?
  • What do domain terms (LOB, payer, market) mean in this context?
  • What is the SLA or performance expectation for this query?
  • What happens on failure — is there a fallback?
  • If you have high confidence in everything, return [].

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — Service method with commit history:
Entity: Function getPayerCompetitors | File: CompetitorService.java
Signature: List<PayerCompetitorDto> getPayerCompetitors(String lob, List<String> providerTypes)
Commit history: 3 commits — initial impl, jOOQ refactor, added state filter
PR: "JIRA-4421 — Fix N+1 query in competitor service (switch to jOOQ batch fetch)"

Expected output:
{
  "purpose": "Returns all payer competitors for a given line of business and provider type combination, used by the competitor analysis dashboard to display market positioning data.",
  "history_summary": "Initially implemented with JPA in Q3 2023. Refactored to jOOQ in Jan 2024 after JIRA-4421 identified N+1 query causing dashboard timeouts. A state filter was added in Feb 2024 to support multi-state competitor analysis.",
  "invariants": ["lob must be non-null and non-empty; null lob causes NPE in jOOQ filter layer", "providerTypes empty list is interpreted as 'all provider types' — this is implicit contract"],
  "change_risk": "HIGH",
  "change_risk_reason": "HIGH — return type and filter semantics are consumed by 3+ downstream screens; signature change requires coordinated frontend+backend deploy.",
  "owner_team": "Competitor Analysis Team",
  "external_dependencies": [],
  "source_confidence": "medium",
  "gaps": ["What is the valid set of lob values — is there an authoritative enum or DB lookup table?", "Is there a result size cap, or can this return unbounded rows for large markets?"]
}

EXAMPLE 2 — API endpoint, minimal history:
Entity: ApiEndpoint POST /api/v1/charges | File: ChargeController.java
Signature: ResponseEntity<ChargeDto> createCharge(@RequestBody ChargeRequest request)
No commit history. No PR description.

Expected output:
{
  "purpose": "Accepts a charge creation request and delegates to ChargeService to persist the charge record and trigger downstream billing processing via event.",
  "history_summary": "No commit history available; endpoint appears stable or was recently introduced. Inferred from code structure only.",
  "invariants": ["Request body must include amountCents > 0 and a valid ISO-4217 currency code", "Endpoint returns 201 Created on success; 400 Bad Request if validation fails"],
  "change_risk": "HIGH",
  "change_risk_reason": "HIGH — public API endpoint on a payment critical path; any breaking change requires versioning and consumer notification.",
  "owner_team": null,
  "external_dependencies": [],
  "source_confidence": "low",
  "gaps": ["Which downstream consumers call this endpoint — internal services only or external partners?", "Is idempotency required (duplicate charge prevention)?"]
}

CRITICAL: Output ONLY the raw JSON object. Start with { end with }. Nothing else.
"""


class ContextSynthesizer:
    """
    Synthesises business context for each extracted entity using LLM Pass 3.
    Runs in parallel with concurrency limits to respect API rate limits.
    """

    def __init__(self, max_concurrency: int = 1):
        # Default concurrency=1 for local Ollama — it's single-threaded.
        # Parallel calls queue up and all hit the timeout.  Sequential is faster overall.
        self._provider = get_provider()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        log.info("ContextSynthesizer ready", llm_provider=self._provider.provider_name,
                 model=self._provider.model_for_role(TaskRole.SYNTHESIS))

    async def synthesise_all(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        annotations: list[dict],
    ) -> dict[str, BusinessContext]:
        """
        Synthesise business context for all entities.
        Returns a dict keyed by entity external_id.
        """
        # Only synthesise entities that carry real business logic.
        # Schema fields, DB columns, and model/DTO types are fully described
        # by their code — git history adds nothing useful and wastes LLM time.
        # Skip pure data-shape types — their meaning is fully in their declaration,
        # git history adds nothing. DatabaseQuery is intentionally NOT skipped
        # because its SQL/JPQL body needs to be explained (query profiler use-case).
        _SKIP_TYPES = frozenset({
            "SchemaField", "DatabaseColumn", "DTO", "Request", "Response",
            "Model", "Entity", "Payload", "ValueObject",
        })
        entities = [e for e in entities if e.entity_type not in _SKIP_TYPES]
        log.info("Starting context synthesis",
                 entity_count=len(entities),
                 note="schema/field/model entities skipped")

        # Build a lookup: entity name → relevant clusters + annotations
        entity_context_map = self._build_entity_context_map(entities, clusters, annotations)

        tasks = [
            self._synthesise_entity(entity, entity_context_map.get(entity.name, {}))
            for entity in entities
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        context_map: dict[str, BusinessContext] = {}
        for entity, result in zip(entities, results):
            if isinstance(result, Exception):
                log.error("Context synthesis failed", entity=entity.name, error=str(result))
                continue
            context_map[entity.external_id] = result

        log.info("Context synthesis complete", synthesised=len(context_map))
        return context_map

    @retry(**SYNTHESIS_RETRY)
    async def _synthesise_entity(
        self,
        entity: ExtractedEntity,
        context: dict,
    ) -> BusinessContext:
        """Synthesise business context for a single entity."""
        async with self._semaphore:
            user_content = self._build_user_message(entity, context)

            # Local Ollama: always use FAST role to keep latency manageable.
            # SYNTHESIS/BALANCED are only worth the extra time on cloud APIs (Claude/GPT).
            role = TaskRole.FAST

            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=CONTEXT_SYNTHESIS_PROMPT),
                    ChatMessage(role="user", content=user_content),
                ],
                role=role,
                max_tokens=settings.max_tokens_context_synthesis,
            )

            data = json.loads(raw)

            confidence = "low"
            if context.get("has_human_annotation"):
                confidence = "high"
            elif context.get("has_rich_pr"):
                confidence = "medium"

            return BusinessContext(
                entity_external_id=entity.external_id,
                purpose=data.get("purpose", ""),
                history_summary=data.get("history_summary", ""),
                invariants=data.get("invariants", []),
                change_risk=data.get("change_risk", "MEDIUM"),
                change_risk_reason=data.get("change_risk_reason", ""),
                owner_team=data.get("owner_team"),
                external_dependencies=data.get("external_dependencies", []),
                source_confidence=confidence,
                gaps=data.get("gaps", []),
            )

    def _build_user_message(self, entity: ExtractedEntity, context: dict) -> str:
        """Build the LLM input for a single entity."""
        parts = [
            f"## Entity\nType: {entity.entity_type}\nName: {entity.name}\nFile: {entity.file}",
        ]
        if entity.signature:
            parts.append(f"Signature: `{entity.signature}`")

        # Code snippet — essential for stable functions that rarely appear in git diffs.
        # Gives the LLM the actual method body to reason about purpose, invariants, risk.
        if entity.code_snippet:
            parts.append(f"\n## Method Body\n```java\n{entity.code_snippet[:500]}\n```")

        # For database queries include the SQL/JPQL directly
        if entity.entity_type == "DatabaseQuery" and entity.query_text:
            parts.append(f"\n## Query\n```sql\n{entity.query_text}\n```")

        if context.get("human_annotations"):
            parts.append("\n## Human Annotations (HIGHEST SIGNAL — prioritise these)")
            for ann in context["human_annotations"]:
                parts.append(f"- [{ann['annotation_type']}] {ann['text']}")

        if context.get("commits"):
            # Cap tightly for local models — keep total prompt under ~1500 tokens.
            # 5 commits × ~200 chars each ≈ 1000 chars of history context.
            parts.append(f"\n## Commit History (showing 5 of {len(context['commits'])} commits)")
            for commit in context["commits"][:5]:
                line = f"- {commit['hash'][:7]}: {commit['message'][:100]}"
                if commit.get("pr_title"):
                    line += f" | PR: {commit['pr_title'][:80]}"
                parts.append(line)
        elif not entity.code_snippet:
            parts.append("\n## Note\nNo commit history or code body available — infer from name/signature only.")

        return "\n".join(parts)

    def _build_entity_context_map(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        annotations: list[dict],
    ) -> dict[str, dict]:
        """
        For each entity, collect the commits and annotations that reference it.
        Used to build focused LLM inputs per entity.
        """
        entity_names_lower = {e.name.lower(): e.name for e in entities}
        context_map: dict[str, dict] = {e.name: {"commits": [], "human_annotations": [], "has_rich_pr": False} for e in entities}

        # Match annotations to entities by name or applies_to_fields
        for ann in annotations:
            entity_name = ann.get("entity_name", "")
            if entity_name in context_map:
                context_map[entity_name]["human_annotations"].append(ann)
                context_map[entity_name]["has_human_annotation"] = True

        # Match commits to entities by searching diffs for entity names
        for cluster in clusters:
            for commit in cluster.commits:
                if not commit.diff:
                    continue
                diff_lower = commit.diff.lower()

                commit_data = {
                    "hash": commit.commit_hash,
                    "message": commit.message,
                    "pr_title": commit.pr_title,
                    "pr_body": commit.pr_body,
                    "ticket_summaries": commit.ticket_summaries or [],
                    "diff_snippet": commit.diff[:500],
                }

                for name_lower, name in entity_names_lower.items():
                    if name_lower in diff_lower:
                        context_map[name]["commits"].append(commit_data)
                        if commit.pr_body and len(commit.pr_body) > 100:
                            context_map[name]["has_rich_pr"] = True

        return context_map
