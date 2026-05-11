"""
E2 — AnnotationPass: ANNOTATES edges from framework annotations/decorators.

Language-agnostic: the system prompt names behavioral categories (lifecycle,
transactionality, security, scheduling, etc.) and the LLM identifies the
matching annotation syntax for any language/framework it knows.

BRAIN_SKIP_ANNOTATION_PASS=true disables this pass.
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel

from companybrain.llm import TaskRole
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.pipeline.passes.base import ExtractionPass

# Token budget from ADR-0042 §2.5
_MAX_TOKENS = 800


class _AnnotationEdge(BaseModel):
    annotation_name: str   # CamelCase, e.g. "Transactional", "PreAuthorize"
    entity_name: str       # must be in the entity list
    confidence: float = 1.0
    evidence: str = ""


class _AnnotationResponse(BaseModel):
    annotations: list[_AnnotationEdge]


_SYSTEM_PROMPT = """\
You are a code analyst specialising in framework annotations, decorators, and pragmas.

For each entity in the input, identify ALL framework annotations / decorators that
describe its LIFECYCLE, TRANSACTIONALITY, SECURITY, SCHEDULING, CACHING, RETRY,
OBSERVABILITY, VALIDATION, or ROUTING behaviour.

Languages and frameworks include but are not limited to:
  Java/Spring:  @Transactional, @Cacheable, @Async, @Scheduled, @PreAuthorize,
                @RolesAllowed, @Retry, @CircuitBreaker, @RateLimiter, @Timed,
                @EventListener, @KafkaListener, @SqsListener, @Validated
  Python/Flask: @app.route, @login_required, @cached_property, @retry, @celery.task,
                @validator (Pydantic), @cached
  Python/FastAPI: @app.get, @app.post, @app.put, @app.delete, @router.get,
                  @Depends, @BackgroundTasks
  Python/Django: @login_required, @permission_required, @cached_page, @csrf_exempt
  TypeScript/NestJS: @Controller, @UseGuards, @UseInterceptors, @Inject,
                     @Injectable, @Module, @Get, @Post, @Put, @Delete, @Cron,
                     @MessagePattern, @EventPattern
  TypeScript/NextJS: 'use client', 'use server', route handler exports
  Go: struct tags (json:, db:, validate:), //go:generate
  C#/ASP.NET: [Authorize], [ApiController], [HttpGet], [Cache]
  Rust: #[tokio::main], #[async_std::main], #[derive(...)]

EMIT ONE EDGE PER ANNOTATION. Rules:
  - Normalise annotation_name to CamelCase (drop the @ / # / [] prefix).
    Examples: @Transactional → "Transactional", @PreAuthorize → "PreAuthorize",
              @app.get → "AppGet", @celery.task → "CeleryTask",
              'use client' → "UseClient", [Authorize] → "Authorize"
  - Only emit annotations that describe behaviour, NOT structural annotations
    like @Override, @Deprecated, @SuppressWarnings, @Data, @Builder, @Getter.
  - Only emit edges where entity_name is EXACTLY as given in the entity list.
  - confidence: 1.0 if annotation is literally present; 0.8 if inferred from
    superclass or interface.
  - evidence: the exact annotation token from the code snippet.

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — Java Spring service:
Entity: getPayerCompetitors [Function], snippet: @Transactional @Cacheable("competitors")
Expected: {"annotations": [
  {"annotation_name": "Transactional", "entity_name": "getPayerCompetitors", "confidence": 1.0, "evidence": "@Transactional"},
  {"annotation_name": "Cacheable",     "entity_name": "getPayerCompetitors", "confidence": 1.0, "evidence": "@Cacheable(\\"competitors\\")"}
]}

EXAMPLE 2 — Python FastAPI route:
Entity: get_competitors [Function], snippet: @router.get("/competitors") @login_required
Expected: {"annotations": [
  {"annotation_name": "RouterGet",      "entity_name": "get_competitors", "confidence": 1.0, "evidence": "@router.get"},
  {"annotation_name": "LoginRequired",  "entity_name": "get_competitors", "confidence": 1.0, "evidence": "@login_required"}
]}

EXAMPLE 3 — TypeScript NestJS controller:
Entity: CompetitorController [Class], snippet: @Controller('/competitors') @UseGuards(AuthGuard)
Expected: {"annotations": [
  {"annotation_name": "Controller", "entity_name": "CompetitorController", "confidence": 1.0, "evidence": "@Controller"},
  {"annotation_name": "UseGuards",  "entity_name": "CompetitorController", "confidence": 1.0, "evidence": "@UseGuards(AuthGuard)"}
]}

Return ONLY valid JSON matching exactly: {"annotations": [...]}. No prose before or after.
"""


class AnnotationPass(ExtractionPass):
    name = "annotation_pass"
    role = TaskRole.FAST
    max_tokens = _MAX_TOKENS
    system_prompt = _SYSTEM_PROMPT

    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        # Only include entities that have code snippets (annotations live in declarations)
        relevant = [
            e for e in entities
            if e.entity_type in ("Function", "ApiEndpoint", "Class", "FrontendComponent")
            and e.code_snippet
        ]
        if not relevant:
            return ""

        lines = [f"Entities ({len(relevant)} total — find annotations for each):"]
        for e in relevant[:40]:  # cap at 40 for token budget
            snippet = (e.code_snippet or "")[:300]
            lines.append(f"\n[{e.entity_type}] {e.name}\nSnippet: {snippet}")

        return "\n".join(lines)

    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        data = json.loads(raw)
        resp = _AnnotationResponse(**data)

        entity_names = {e.name for e in entities}
        rels: list[ExtractedRelationship] = []
        for ann in resp.annotations:
            if ann.entity_name not in entity_names:
                continue
            rels.append(ExtractedRelationship(
                from_entity=ann.annotation_name,
                from_type="Annotation",
                edge_type="ANNOTATES",
                to_entity=ann.entity_name,
                to_type=next(
                    (e.entity_type for e in entities if e.name == ann.entity_name),
                    "Function",
                ),
                confidence=ann.confidence,
                evidence=ann.evidence,
            ))
        return rels
