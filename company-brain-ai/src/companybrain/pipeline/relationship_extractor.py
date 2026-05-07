"""
RelationshipExtractor — LLM Pass 2 of the context builder pipeline.

Given the entity list from Pass 1, extracts typed edges between them.
Uses a single LLM call with entity list + current API snapshot as context.
"""
from __future__ import annotations

import json

import structlog
from tenacity import retry
from companybrain.pipeline._retry import EXTRACTION_RETRY

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.config import settings
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship, CommitCluster

log = structlog.get_logger(__name__)

RELATIONSHIP_SYSTEM_PROMPT = """You are a code graph analyst. Your job is to extract TYPED EDGES between code entities to build a precise call-dependency graph.

━━━ OUTPUT FORMAT ━━━
Return ONLY this JSON (max 20 relationships):
{"relationships": [{"from": "entityName", "from_type": "type", "edge_type": "EDGE", "to": "entityName", "to_type": "type", "confidence": 0.9, "evidence": "exact code token ≤40 chars"}]}

━━━ EDGE TYPE REFERENCE ━━━
Use EXACTLY one of these seven types. Nothing else.

┌────────────────┬──────────────────────────────────────────────────────────────────┐
│ CALLS          │ A function/method directly invokes another function/method.      │
│                │ from=caller, to=callee. Both must be Function or ApiEndpoint.    │
│                │ Evidence: the exact call expression, e.g. "repo.save(entity)"   │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ READS_COLUMN   │ A Function or DatabaseQuery reads a specific DB column.          │
│                │ to must be a DatabaseColumn (format: table.column).             │
│                │ Evidence: SQL fragment or ORM accessor, e.g. "SELECT amount"    │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ WRITES_COLUMN  │ A Function or DatabaseQuery inserts/updates a DB column.        │
│                │ to must be a DatabaseColumn. Use for INSERT/UPDATE/MERGE ops.   │
│                │ Evidence: SQL fragment or ORM setter, e.g. "SET status = ?"     │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ RENDERS_FIELD  │ A FrontendComponent displays or binds a SchemaField.             │
│                │ from=FrontendComponent, to=SchemaField. TypeScript/JSX only.    │
│                │ Evidence: JSX expression, e.g. "item.providerTypes.map(...)"    │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ CALLS_ENDPOINT │ A Function or FrontendComponent calls an external HTTP endpoint. │
│                │ to must be an ApiEndpoint or ExternalService.                   │
│                │ Evidence: HTTP call, e.g. "axios.get('/api/v1/competitors')"    │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ VALIDATES      │ A Function enforces a business rule or constraint on a field.    │
│                │ to must be a SchemaField or DatabaseColumn.                     │
│                │ Evidence: validation expression, e.g. "if (lob == null) throw"  │
├────────────────┼──────────────────────────────────────────────────────────────────┤
│ TESTED_BY      │ A production Function/ApiEndpoint is exercised by a test.        │
│                │ from=production entity, to=test function. NEVER reverse.        │
│                │ Evidence: test class/method name, e.g. "CompetitorServiceTest"  │
└────────────────┴──────────────────────────────────────────────────────────────────┘

━━━ DISAMBIGUATION RULES ━━━
• CALLS vs CALLS_ENDPOINT:
    CALLS — same codebase, synchronous method invocation (e.g. service.getCompetitors())
    CALLS_ENDPOINT — crosses a network boundary (HTTP/gRPC/queue). Use when you see
    RestTemplate, WebClient, axios, fetch, @FeignClient, or an explicit URL string.

• READS_COLUMN vs CALLS (for repository methods):
    If a repository method name implies a SELECT (findBy*, get*, list*, search*),
    emit CALLS from caller → repository method, then READS_COLUMN from repository
    method → each column it reads. Do NOT skip the intermediate CALLS edge.

• WRITES_COLUMN only when mutation is explicit:
    Only emit WRITES_COLUMN when you see INSERT/UPDATE/SET/MERGE or ORM .save()/.persist()
    on a specific column. Do NOT emit for SELECT queries that happen to filter by a column.

• TESTED_BY confidence calibration:
    1.0 — test method body explicitly calls the production method by name
    0.85 — test class name follows *ServiceTest → *Service or *ControllerTest → *Controller
    0.70 — test is in same package as production code and accesses same entity

• DO NOT EMIT edges for:
    - Class inheritance (extends / implements)
    - Module imports (import statements)
    - Field declarations (private Foo bar;)
    - Constructor injection (@Autowired / @Inject) — these are structural, not behavioral
    - Lombok-generated methods (equals, hashCode, toString, builder)

━━━ CONFIDENCE SCALE ━━━
1.0 — Explicit: exact token found in code snippet ("repo.findByLob(lob)" is right there)
0.9 — Near-explicit: clear from diff context even if full body not shown
0.7 — Inferred: naming convention or structural pattern strongly implies the edge
0.5 — Uncertain: possible but ambiguous — SKIP IT (only emit if ≥ 0.7)

━━━ ENTITY CONSTRAINT ━━━
Use ONLY entity names from the provided list. Exact spelling. If the name is not in the
list, do not emit the edge. Never invent entity names.

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — REST controller → service → repository call chain + column reads:
Entities: getCompetitors (ApiEndpoint), getPayerCompetitors (Function),
          findByLobAndStates (DatabaseQuery), competitors.lob (DatabaseColumn),
          competitors.provider_type (DatabaseColumn), competitors.state (DatabaseColumn)
Method body snippets available.
Expected:
{"relationships": [
  {"from": "getCompetitors",      "from_type": "ApiEndpoint",    "edge_type": "CALLS",        "to": "getPayerCompetitors",    "to_type": "Function",       "confidence": 1.0, "evidence": "competitorService.getPayerCompetitors(lob,types)"},
  {"from": "getPayerCompetitors", "from_type": "Function",       "edge_type": "CALLS",        "to": "findByLobAndStates",     "to_type": "DatabaseQuery",  "confidence": 1.0, "evidence": "repo.findByLobAndStates(lob,states)"},
  {"from": "findByLobAndStates",  "from_type": "DatabaseQuery",  "edge_type": "READS_COLUMN", "to": "competitors.lob",        "to_type": "DatabaseColumn", "confidence": 1.0, "evidence": "WHERE lob = ?"},
  {"from": "findByLobAndStates",  "from_type": "DatabaseQuery",  "edge_type": "READS_COLUMN", "to": "competitors.provider_type","to_type": "DatabaseColumn","confidence": 1.0, "evidence": "AND provider_type IN (?)"},
  {"from": "findByLobAndStates",  "from_type": "DatabaseQuery",  "edge_type": "READS_COLUMN", "to": "competitors.state",      "to_type": "DatabaseColumn", "confidence": 1.0, "evidence": "AND state IN (?)"}
]}

EXAMPLE 2 — jOOQ service writes columns + validation:
Entities: createCharge (Function), charges.amount_cents (DatabaseColumn),
          charges.currency (DatabaseColumn), charges.status (DatabaseColumn),
          validateAmount (Function), ChargeRequest.amountCents (SchemaField)
Expected:
{"relationships": [
  {"from": "createCharge",   "from_type": "Function", "edge_type": "CALLS",         "to": "validateAmount",          "to_type": "Function",       "confidence": 1.0, "evidence": "validateAmount(request)"},
  {"from": "validateAmount", "from_type": "Function", "edge_type": "VALIDATES",     "to": "ChargeRequest.amountCents","to_type": "SchemaField",    "confidence": 1.0, "evidence": "if (amountCents <= 0) throw"},
  {"from": "createCharge",   "from_type": "Function", "edge_type": "WRITES_COLUMN", "to": "charges.amount_cents",    "to_type": "DatabaseColumn", "confidence": 1.0, "evidence": "INSERT amount_cents VALUES (?)"},
  {"from": "createCharge",   "from_type": "Function", "edge_type": "WRITES_COLUMN", "to": "charges.currency",        "to_type": "DatabaseColumn", "confidence": 1.0, "evidence": "INSERT currency VALUES (?)"},
  {"from": "createCharge",   "from_type": "Function", "edge_type": "WRITES_COLUMN", "to": "charges.status",          "to_type": "DatabaseColumn", "confidence": 1.0, "evidence": "SET status = 'PENDING'"}
]}

EXAMPLE 3 — Frontend component with external API call + field rendering:
Entities: CompetitorTable (FrontendComponent), fetchCompetitors (Function),
          getCompetitors (ApiEndpoint), CompetitorDto.providerTypes (SchemaField),
          CompetitorDto.marketShare (SchemaField)
Expected:
{"relationships": [
  {"from": "CompetitorTable",  "from_type": "FrontendComponent", "edge_type": "CALLS_ENDPOINT", "to": "getCompetitors",              "to_type": "ApiEndpoint", "confidence": 1.0, "evidence": "axios.get('/api/v1/competitors')"},
  {"from": "CompetitorTable",  "from_type": "FrontendComponent", "edge_type": "RENDERS_FIELD",  "to": "CompetitorDto.providerTypes", "to_type": "SchemaField", "confidence": 0.9, "evidence": "item.providerTypes.map(...)"},
  {"from": "CompetitorTable",  "from_type": "FrontendComponent", "edge_type": "RENDERS_FIELD",  "to": "CompetitorDto.marketShare",   "to_type": "SchemaField", "confidence": 0.9, "evidence": "{competitor.marketShare}"}
]}

EXAMPLE 4 — Test coverage edges:
Entities: getPayerCompetitors (Function), getCompetitors_returnsFilteredList (Function),
          CompetitorServiceTest (Function)
Expected:
{"relationships": [
  {"from": "getPayerCompetitors", "from_type": "Function", "edge_type": "TESTED_BY", "to": "getCompetitors_returnsFilteredList", "to_type": "Function", "confidence": 1.0, "evidence": "service.getPayerCompetitors(lob,types)"},
  {"from": "getPayerCompetitors", "from_type": "Function", "edge_type": "TESTED_BY", "to": "CompetitorServiceTest",              "to_type": "Function", "confidence": 0.85, "evidence": "CompetitorServiceTest class"}
]}

CRITICAL: Output ONLY the raw JSON. Start with { end with }. Nothing before, nothing after.
"""


class RelationshipExtractor:
    """
    Runs LLM Pass 2 (relationship extraction) in a single LLM call.
    Uses the entity list from Pass 1 as context.
    """

    def __init__(self):
        self._provider = get_provider()
        log.info("RelationshipExtractor ready", llm_provider=self._provider.provider_name,
                 model=self._provider.model_for_role(TaskRole.BALANCED))

    @retry(**EXTRACTION_RETRY)
    async def extract(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        api_snapshot: dict,
    ) -> list[ExtractedRelationship]:
        """
        Extract relationships between entities using a single LLM call.
        Returns a list of ExtractedRelationship.
        """
        if not entities:
            return []

        # Apply distilled patterns first (active learning — tier 2, no LLM cost)
        from companybrain.pipeline.pattern_distiller import PatternDistiller
        distiller = PatternDistiller()
        pre_computed_rels = distiller.apply_patterns(
            getattr(api_snapshot, "workspace_id", "default"),
            entities,
        )

        user_content = self._build_user_message(entities, clusters, api_snapshot)

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=RELATIONSHIP_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_content),
            ],
            role=TaskRole.BALANCED,   # FAST (llama-3.1-8b) TPM too low; BALANCED has headroom
            max_tokens=settings.max_tokens_relationship,
        )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            # Model may have been cut off mid-JSON — try to salvage complete objects
            data = _salvage_truncated_json(raw)
            if not data:
                log.warning("Failed to parse relationship extraction JSON",
                            error=str(e), raw=raw[:300])
                return []

        # Build a lookup of entity names for validation
        entity_names = {e.name for e in entities}
        entity_name_to_type = {e.name: e.entity_type for e in entities}

        relationships = []
        for item in data.get("relationships", []):
            try:
                from_name = item["from"]
                to_name = item["to"]

                # Only include relationships between known entities
                if from_name not in entity_names or to_name not in entity_names:
                    log.debug("Skipping relationship with unknown entity",
                              from_entity=from_name, to_entity=to_name)
                    continue

                relationships.append(ExtractedRelationship(
                    from_entity=from_name,
                    from_type=item.get("from_type", entity_name_to_type.get(from_name, "")),
                    edge_type=item["edge_type"],
                    to_entity=to_name,
                    to_type=item.get("to_type", entity_name_to_type.get(to_name, "")),
                    confidence=float(item.get("confidence", 0.7)),
                    evidence=item.get("evidence", ""),
                ))
            except (KeyError, ValueError) as e:
                log.debug("Skipping malformed relationship", error=str(e), item=item)

        # Record for future pattern distillation
        await distiller.record_edges(
            getattr(api_snapshot, "workspace_id", "default"),
            relationships,
        )

        # Merge: LLM results override pattern-distilled results
        llm_edge_keys = {(r.from_entity, r.edge_type, r.to_entity) for r in relationships}
        for pre in pre_computed_rels:
            key = (pre["from_entity"], pre["edge_type"], pre["to_entity"])
            if key not in llm_edge_keys:
                relationships.append(ExtractedRelationship(
                    from_entity=pre["from_entity"],
                    from_type="",
                    edge_type=pre["edge_type"],
                    to_entity=pre["to_entity"],
                    to_type="",
                    confidence=pre["confidence"],
                    evidence=pre["evidence"],
                ))

        log.info("Relationship extraction complete", relationships=len(relationships))
        return relationships

    # Approximate chars-per-token ratio for Llama models (conservative)
    _CHARS_PER_TOKEN = 3.5
    # Leave headroom for system prompt (~2800 tokens) + output (~1024 tokens)
    # BALANCED model on Groq free tier has 30k TPM; cap input at 5000 tokens ≈ 17500 chars
    _MAX_INPUT_CHARS = 17_500

    def _build_user_message(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        api_snapshot: dict,
    ) -> str:
        """
        Build the user message for the LLM call.

        Respects a hard character budget (_MAX_INPUT_CHARS) so the total
        request never exceeds the provider's TPM window. Sections are added
        in priority order; lower-priority sections are trimmed or dropped when
        the budget is tight.
        """
        budget = self._MAX_INPUT_CHARS
        parts: list[str] = []

        def _add(text: str) -> bool:
            """Append text if budget allows; return False when exhausted."""
            nonlocal budget
            if budget <= 0:
                return False
            clipped = text[:budget]
            parts.append(clipped)
            budget -= len(clipped)
            return len(clipped) == len(text)

        # ── Priority 1: endpoint header (tiny, always fits) ──────────────────
        _add(f"## Target API Endpoint\n{api_snapshot.get('method', 'POST')} {api_snapshot.get('path', '')}")

        # ── Priority 2: entity list ───────────────────────────────────────────
        entity_lines = [f"\n## Extracted Entities ({len(entities)} total)"]
        for entity in entities:
            line = f"- [{entity.entity_type}] {entity.name} (file: {entity.file})"
            if entity.signature:
                line += f"\n  Signature: `{entity.signature[:120]}`"
            entity_lines.append(line)
        _add("\n".join(entity_lines))

        # ── Priority 3: method body snippets ─────────────────────────────────
        snippets = [
            e for e in entities
            if e.entity_type in ("Function", "ApiEndpoint") and e.code_snippet
        ]
        if snippets:
            _add("\n## Method Body Snippets (find CALLS relationships from these)")
            # Dynamically shrink snippet size to share the remaining budget
            per_snippet = max(150, min(380, budget // max(1, len(snippets[:6]))))
            for entity in snippets[:6]:
                fname = entity.file.split("/")[-1]
                block = f"\n### {entity.name}  [{fname}]\n```\n{entity.code_snippet[:per_snippet]}\n```"
                if not _add(block):
                    break

        # ── Priority 4: database queries ──────────────────────────────────────
        queries = [e for e in entities if e.entity_type == "DatabaseQuery" and e.query_text]
        if queries and budget > 200:
            _add("\n## Database Queries (find READS_COLUMN / WRITES_COLUMN edges)")
            for q in queries[:5]:
                if not _add(f"\n- {q.name}: `{(q.query_text or '')[:150]}`"):
                    break

        # ── Priority 5: commit context (lowest — drop when tight) ────────────
        if budget > 500:
            _add("\n## Recent Commit Context")
            commit_count = 0
            for cluster in clusters:
                for commit in cluster.commits:
                    if commit_count >= 3 or budget < 100:
                        break
                    line = f"\n- {commit.commit_hash[:7]}: {commit.message[:80]}"
                    if commit.diff and budget > 300:
                        line += f"\n  diff: {commit.diff[:200]}"
                    if not _add(line):
                        break
                    commit_count += 1
                if commit_count >= 3:
                    break

        return "\n".join(parts)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _salvage_truncated_json(text: str) -> dict | None:
    """
    When the model is cut off mid-JSON, extract every complete relationship
    object that appears before the truncation point.

    Strategy: find all complete {...} objects inside the relationships array
    and reassemble them into a valid payload.
    """
    import re
    objects = []
    # Find every {...} block that has both "from" and "edge_type" keys
    for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        chunk = m.group(0)
        if '"from"' in chunk and '"edge_type"' in chunk:
            try:
                obj = json.loads(chunk)
                objects.append(obj)
            except json.JSONDecodeError:
                continue
    if objects:
        log.info("Salvaged partial relationship JSON", recovered=len(objects))
        return {"relationships": objects}
    return None
