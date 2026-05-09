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

━━━ USE YOUR DOMAIN KNOWLEDGE ━━━
You have extensive background knowledge about software systems and their business domains.
USE IT. Do not treat industry-standard terms as unknowns — interpret them confidently.

Healthcare / Managed-Care terms you will encounter:
  • payer        — a health insurance company or health plan (e.g. UnitedHealth, Aetna, BCBS)
  • LOB          — Line of Business: the insurance product segment, e.g. Commercial, Medicare
                   Advantage (MA), Medicaid, Exchange/ACA
  • provider     — a healthcare provider: physician, hospital, specialist, group practice
  • provider type — a physician specialty category (e.g. PCP, cardiologist, oncologist)
  • market       — a geographic or competitive region where payers compete for members
  • market share — percentage of covered lives or revenue a payer holds in a market
  • competitor   — another payer offering the same LOB in the same market
  • member       — an individual enrolled in a health plan
  • NIQ          — Network Intelligence Quotient or similar network analytics product
  • mcheck       — typically "market check" — a tool for evaluating market positioning
  • competitiveness — how a plan's benefits/network/premium compare to market peers
  • summary      — aggregated analytics view (e.g. competitor summary = rolled-up payer stats)
  • NPI          — National Provider Identifier, the unique US physician/facility ID
  • INN / OON    — In-Network / Out-of-Network (contract status of a provider)
  • prior auth    — prior authorization, a cost-control gate before a service is rendered
  • claim         — a billing record submitted by a provider to a payer for reimbursement

General enterprise software terms:
  • workspace    — a tenant/org isolation boundary
  • job          — an async pipeline run (ingestion, analysis, etc.)
  • upsert       — insert-or-update, idempotent write
  • BM25         — lexical keyword search algorithm used in retrieval
  • embedding    — dense vector representation of text for semantic search

When you see these terms in entity names, signatures, or SQL, interpret them using this
knowledge and write a CONCRETE, SPECIFIC purpose — not a generic placeholder.

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

gaps — Surface GENUINE unknowns only — questions that a human must answer:
  • What business rules exist that the code doesn't capture?
  • What is the SLA or performance expectation for this query?
  • What happens on failure — is there a fallback?
  • Which downstream consumers depend on the return contract?
  • DO NOT list domain terms (LOB, payer, market, provider type, etc.) as gaps —
    you know what these mean; use that knowledge to write the purpose instead.
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

EXAMPLE 3 — Repository interface method with @Query SQL:
Entity: InterfaceMethod findCompetitorsByPayerAndLob | File: CompetitorsRepository.java
Signature: List<CompetitorDto> findCompetitorsByPayerAndLob(String payerId, String lob)
Query: SELECT c.payer_name, c.market_share FROM competitors c WHERE c.payer_id = :payerId AND c.lob = :lob
No commit history.

Expected output:
{
  "purpose": "Fetches all competing health insurance plans for a given payer and line of business (e.g. Commercial, Medicare Advantage), returning their market share data for use in competitive positioning analysis.",
  "history_summary": "No commit history available; method appears stable. Inferred from @Query annotation and signature.",
  "invariants": ["payerId must be non-null and match a valid payer in the competitors table", "lob must match a recognized line-of-business value; unrecognised values will return empty results"],
  "change_risk": "MEDIUM",
  "change_risk_reason": "MEDIUM — returns data consumed by the competitor analysis UI; changing column aliases or filter semantics requires coordinated frontend update.",
  "owner_team": null,
  "external_dependencies": [],
  "source_confidence": "low",
  "gaps": ["What is the valid enumeration of lob values — is it enforced by a DB constraint or application enum?"]
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

    # Entities batched per LLM call when batched mode is on. Each call's
    # output budget is roughly 600 tok/entity × N, so 8 entities × 600 ≈ 4800
    # tokens which stays inside Anthropic Haiku's safe response window.
    _BATCH_SIZE = 8

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

        # Batch entities into groups of _BATCH_SIZE per LLM call. The system
        # prompt is shared (cached) so input cost amortises; output cost stays
        # ~the same per entity but the per-call HTTP+latency overhead is cut
        # by ~Nx. For 100 entities this drops 100 LLM calls → ~13 calls.
        batches: list[list[ExtractedEntity]] = [
            entities[i : i + self._BATCH_SIZE]
            for i in range(0, len(entities), self._BATCH_SIZE)
        ]

        tasks = [
            self._synthesise_batch(batch, entity_context_map)
            for batch in batches
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        context_map: dict[str, BusinessContext] = {}
        for batch, result in zip(batches, batch_results):
            if isinstance(result, Exception):
                log.error("Context synthesis batch failed",
                          first_entity=batch[0].name if batch else "?",
                          batch_size=len(batch),
                          error=str(result))
                # Fall back to per-entity calls for this batch so a single bad
                # entity doesn't lose the rest of the group's contexts.
                for entity in batch:
                    try:
                        ctx = await self._synthesise_entity(
                            entity, entity_context_map.get(entity.name, {}),
                        )
                        if ctx is not None:
                            context_map[entity.external_id] = ctx
                    except Exception as exc:
                        log.warning("Per-entity fallback also failed",
                                    entity=entity.name, error=str(exc))
                continue
            for entity, ctx in zip(batch, result or []):
                if ctx is not None:
                    context_map[entity.external_id] = ctx

        log.info("Context synthesis complete",
                 synthesised=len(context_map),
                 batches=len(batches),
                 batch_size=self._BATCH_SIZE)
        return context_map

    @retry(**SYNTHESIS_RETRY)
    async def _synthesise_batch(
        self,
        batch: list[ExtractedEntity],
        entity_context_map: dict,
    ) -> list[BusinessContext | None]:
        """One LLM call producing N BusinessContexts in one go.

        Strategy: the system prompt asks for a JSON object keyed by entity
        external_id. We send ALL entities' user-content concatenated under
        clear `### entity N` headers; the LLM returns a single JSON dict.
        Per-entity confidence is derived from the original entity_context_map
        flags so we don't lose the high/medium/low signal.
        """
        if not batch:
            return []
        async with self._semaphore:
            sections = []
            for i, entity in enumerate(batch, 1):
                ctx = entity_context_map.get(entity.name, {})
                section = self._build_user_message(entity, ctx)
                sections.append(
                    f"### entity {i}: external_id = {entity.external_id}\n{section}"
                )
            user_content = (
                "Synthesise BusinessContext for EACH of the following entities. "
                "Return a JSON object keyed by external_id, where each value is "
                "the BusinessContext object you would produce for that entity:\n\n"
                "  { \"<external_id_1>\": { ... context fields ... }, "
                "    \"<external_id_2>\": { ... }, ... }\n\n"
                + "\n\n---\n\n".join(sections)
            )

            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=CONTEXT_SYNTHESIS_PROMPT),
                    ChatMessage(role="user", content=user_content),
                ],
                role=TaskRole.FAST,
                # Per-batch ceiling = per-entity cap × batch size (with headroom)
                max_tokens=min(8000, settings.max_tokens_context_synthesis * len(batch)),
            )

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Batch LLM returned non-JSON — falling back to per-entity",
                            batch_size=len(batch))
                raise

            results: list[BusinessContext | None] = []
            for entity in batch:
                ctx_data = (
                    data.get(entity.external_id)
                    or data.get(entity.name)
                    or data.get(f"entity {batch.index(entity) + 1}")
                )
                if not isinstance(ctx_data, dict):
                    results.append(None)
                    continue
                ctx_for_entity = entity_context_map.get(entity.name, {})
                confidence = "low"
                if ctx_for_entity.get("has_human_annotation"):
                    confidence = "high"
                elif ctx_for_entity.get("has_rich_pr"):
                    confidence = "medium"
                results.append(BusinessContext(
                    entity_external_id=entity.external_id,
                    purpose=ctx_data.get("purpose", ""),
                    history_summary=ctx_data.get("history_summary", ""),
                    invariants=ctx_data.get("invariants", []) or [],
                    change_risk=ctx_data.get("change_risk", "MEDIUM"),
                    change_risk_reason=ctx_data.get("change_risk_reason", ""),
                    source_confidence=confidence,
                    owner_team=ctx_data.get("owner_team"),
                    external_dependencies=ctx_data.get("external_dependencies", []) or [],
                    gaps=ctx_data.get("gaps", []) or [],
                    business_capability=ctx_data.get("business_capability"),
                    personas_affected=ctx_data.get("personas_affected", []) or [],
                    failure_modes=ctx_data.get("failure_modes", []) or [],
                    side_effects=ctx_data.get("side_effects", []) or [],
                    idempotency=ctx_data.get("idempotency"),
                    blast_radius=ctx_data.get("blast_radius", []) or [],
                    deprecation_status=ctx_data.get("deprecation_status"),
                    data_sensitivity=ctx_data.get("data_sensitivity"),
                    compliance_tags=ctx_data.get("compliance_tags", []) or [],
                    performance_notes=ctx_data.get("performance_notes"),
                    related_concepts=ctx_data.get("related_concepts", []) or [],
                ))
            return results

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

        # For database queries and interface methods include the SQL/JPQL directly.
        # InterfaceMethod may have @Query body in query_text (e.g. Spring Data JPA).
        if entity.query_text and entity.entity_type in ("DatabaseQuery", "InterfaceMethod"):
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
            parts.append(
                "\n## Instruction\n"
                "No commit history or code body is available for this entity. "
                "Infer its purpose from the entity name, file path, and signature — "
                "apply your healthcare/managed-care and software domain knowledge "
                "to write a SPECIFIC, concrete purpose sentence. "
                "Do not write generic placeholders like 'processes data' or 'handles requests'."
            )

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
