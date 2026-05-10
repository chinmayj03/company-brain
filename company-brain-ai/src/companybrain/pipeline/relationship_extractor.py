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
Return ONLY this JSON (up to 80 relationships — emit ALL high-confidence edges
you can find; do NOT artificially limit to a small number):
{"relationships": [{"from": "entityName", "from_type": "type", "edge_type": "EDGE", "to": "entityName", "to_type": "type", "confidence": 0.9, "evidence": "exact code token ≤40 chars"}]}

━━━ EDGE TYPE REFERENCE ━━━
Use EXACTLY one of the types below. Group headers are reading aids only —
emit only the all-caps type name. If a candidate edge does not fit any of
these, SKIP IT. Do NOT invent new types.

# STRUCTURE / INHERITANCE
- EXTENDS         child Class → parent Class                           ("extends Base")
- IMPLEMENTS      Class → Interface                                     ("implements Foo")
- OVERRIDES       method → parent method it overrides                   (@Override)
- CONTAINS        Class → its method, Package → its class               (member-of)
- ANNOTATES       Annotation/Decorator → annotated entity               (@Transactional, @Component)
- IMPORTS         Module → imported module (only first-party, not std-lib)

# BEHAVIOR / CALL FLOW
- CALLS           caller → callee, synchronous in-process invocation
- INVOKES         alias for CALLS where source is an event/handler dispatch
- AWAITS          async caller → awaited callee (await, .then, CompletableFuture)
- CALLS_ENDPOINT  client → ApiEndpoint/ExternalService (HTTP/gRPC/queue)
- DELEGATES_TO    wrapper/proxy → inner implementation it forwards to
- INSTANTIATES    factory/caller → Class it creates via `new X()` / builder
- USES            holder → collaborator (field, @Autowired, ctor param, type arg)

# DATA FLOW
- READS_COLUMN    Function/Query → DatabaseColumn (SELECT, ORM getter)
- WRITES_COLUMN   Function/Query → DatabaseColumn (INSERT/UPDATE/MERGE)
- READS_FIELD     Function → object field (non-DB)
- WRITES_FIELD    Function → object field it mutates
- RETURNS         Function → named return type / DTO
- ACCEPTS_PARAM   Function → named parameter type
- TRANSFORMS      mapper Function → produced type (source → target)
- SERIALIZES_TO   Class/DTO → schema/format it serializes to (JSON, Avro, ProtoBuf)

# PERSISTENCE / STORAGE
- PERSISTS_TO     Entity → table/collection it's stored in
- CACHED_BY       Entity → caching layer (Redis, in-mem, @Cacheable)
- INDEXED_BY      DatabaseColumn → index/constraint that covers it
- CONSTRAINED_BY  SchemaField/DatabaseColumn → constraint (NOT NULL, FK, CHECK)

# VALIDATION
- VALIDATES       Function → SchemaField/DatabaseColumn it validates
- ENFORCES        Function → business rule it enforces
- SANITIZES       Function → input it sanitizes/escapes

# ERROR / EXCEPTION FLOW
- THROWS          Function → Exception type it throws
- CATCHES         Function → Exception type it catches
- WRAPS_EXCEPTION Function → Exception it rethrows as a different type
- HANDLES_ERROR   ErrorHandler/Endpoint → Exception type it maps to response

# UI / FRONTEND
- RENDERS         Component → child Component
- RENDERS_FIELD   Component → SchemaField it displays
- BINDS_TO        Component → state/store/form it reads/writes
- ROUTED_BY       Component → Route that mounts it
- LISTENS_TO      Component → event/message it subscribes to

# AUTHZ / SECURITY
- AUTHORIZED_BY   Endpoint → permission/role required
- PROTECTED_BY    Endpoint → guard/middleware/filter chain
- AUDITED_BY      Function → audit-log emitter / category

# ASYNC / EVENTING
- PUBLISHES_TO    Function → topic/queue/exchange it publishes to
- SUBSCRIBES_TO   Handler → topic/queue/exchange it consumes
- SCHEDULED_BY    Function → schedule/cron/trigger that fires it

# OBSERVABILITY
- LOGS_TO         Function → logger / log category
- EMITS_METRIC    Function → metric/counter/gauge name
- TRACED_BY       Function → tracer/span/feature it's instrumented under

# TESTING
- TESTED_BY       production entity → test that exercises it (NEVER reverse)
- MOCKS           test → dependency it mocks/stubs
- FIXTURE_FOR     fixture/builder → entity it produces

# CONFIG / LIFECYCLE
- CONFIGURED_BY   Entity → config/property/env-var it reads
- INITIALIZED_BY  Bean/Service → lifecycle hook that initializes it
- RATE_LIMITED_BY Endpoint → policy/limiter that throttles it

━━━ DISAMBIGUATION RULES ━━━
• CALLS vs CALLS_ENDPOINT:
    CALLS — same codebase, synchronous method invocation (e.g. service.getCompetitors())
    CALLS_ENDPOINT — crosses a network boundary (HTTP/gRPC/queue). Use when you see
    RestTemplate, WebClient, axios, fetch, @FeignClient, or an explicit URL string.

• InterfaceMethod entities are repository/DAO interface methods (e.g. Spring Data JPA
    findBy*, @Query methods). Treat them as call chain nodes:
    - Service calls them → emit CALLS from Function/ApiEndpoint to InterfaceMethod
    - They access DB columns → emit READS_COLUMN from InterfaceMethod to DatabaseColumn
    Do NOT skip the CALLS edge from service to InterfaceMethod.

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

• Structural edges already extracted deterministically (do NOT re-emit):
    - EXTENDS / IMPLEMENTS / CONTAINS / INSTANTIATES / IMPORTS are pre-extracted
      from the AST before this pass runs. You may safely OMIT these — they will
      be present in the final graph regardless.
    - Focus your output on BEHAVIORAL edges that need a body to find:
      CALLS, USES, THROWS, CATCHES, READS_COLUMN, WRITES_COLUMN, VALIDATES,
      RENDERS_FIELD, CALLS_ENDPOINT, AWAITS, DELEGATES_TO, LISTENS_TO,
      PUBLISHES_TO, SUBSCRIBES_TO, SCHEDULED_BY, AUTHORIZED_BY.
  Emitting EXTENDS/IMPLEMENTS/CONTAINS still works (they'll dedup) but wastes
  your output budget on edges we already have at confidence=1.0.

• USES is still useful — emit when you see @Autowired / @Inject / constructor
  injection / field collaborator that REFERENCES an entity by name (we don't
  pre-extract these structurally because they require body inspection).

• THROWS is still useful — emit when you see `throw new XException(...)` so
  exception-flow tracing works.

• DO NOT EMIT edges for:
    - Module imports (import statements)
    - Lombok-generated methods (equals, hashCode, toString, builder)
    - Primitive / JDK / language-builtin field types (String, int, List, Optional)
      — only emit USES when the target is a NAMED entity in the provided list.

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

EXAMPLE 5 — Structural edges (collaborators / inheritance / exceptions):
Entities: CompetitorController (Class), CompetitorService (Class), BaseController (Class),
          AuditLogger (Class), CompetitorRepository (Class), InvalidLobException (Class),
          getCompetitors (ApiEndpoint)
Snippet:
  public class CompetitorController extends BaseController {
      private final CompetitorService competitorService;
      private final AuditLogger logger;
      public CompetitorController(CompetitorService s, AuditLogger l) { ... }
      public ResponseEntity<List<CompetitorDto>> getCompetitors(...) {
          if (lob == null) throw new InvalidLobException("lob required");
          ...
      }
  }
Expected:
{"relationships": [
  {"from": "CompetitorController", "from_type": "Class",       "edge_type": "EXTENDS", "to": "BaseController",        "to_type": "Class", "confidence": 1.0, "evidence": "extends BaseController"},
  {"from": "CompetitorController", "from_type": "Class",       "edge_type": "USES",    "to": "CompetitorService",     "to_type": "Class", "confidence": 1.0, "evidence": "private final CompetitorService"},
  {"from": "CompetitorController", "from_type": "Class",       "edge_type": "USES",    "to": "AuditLogger",           "to_type": "Class", "confidence": 1.0, "evidence": "private final AuditLogger"},
  {"from": "getCompetitors",       "from_type": "ApiEndpoint", "edge_type": "THROWS",  "to": "InvalidLobException",   "to_type": "Class", "confidence": 1.0, "evidence": "throw new InvalidLobException"}
]}

CRITICAL: Output ONLY the raw JSON. Start with { end with }. Nothing before, nothing after.
"""


def _chunk_entities_for_extraction(
    entities: list[ExtractedEntity],
    batch_size: int = 25,
    max_chars_per_batch: int = 60_000,
) -> list[list[ExtractedEntity]]:
    """
    ADR-0042 E8: Co-locality chunking — group entities for multi-pass extraction.

    Strategy:
      1. Group by file (entities from the same file share call-site context).
      2. Within a file group, preserve original order (class before methods).
      3. Pack groups into batches of at most `batch_size` entities whose combined
         snippet+signature length stays under `max_chars_per_batch`.

    This ensures each LLM call sees a coherent slice of the call graph rather
    than arbitrary interleaving across unrelated files.
    """
    if not entities:
        return []

    # Group by file path, preserving insertion order
    from collections import defaultdict
    by_file: dict[str, list[ExtractedEntity]] = defaultdict(list)
    for e in entities:
        by_file[e.file or ""].append(e)

    batches: list[list[ExtractedEntity]] = []
    current_batch: list[ExtractedEntity] = []
    current_chars = 0

    def _entity_size(e: ExtractedEntity) -> int:
        return len(e.signature or "") + len(e.code_snippet or "") + len(e.name)

    for file_group in by_file.values():
        for entity in file_group:
            size = _entity_size(entity)
            if (len(current_batch) >= batch_size
                    or current_chars + size > max_chars_per_batch):
                if current_batch:
                    batches.append(current_batch)
                current_batch = [entity]
                current_chars = size
            else:
                current_batch.append(entity)
                current_chars += size

    if current_batch:
        batches.append(current_batch)

    return batches


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
        Extract relationships between entities.

        ADR-0042 E8: When entities > 25, chunks by co-locality and runs the
        existing single-call logic per batch, then merges and deduplicates.
        """
        if not entities:
            return []

        # E8: multi-pass for large entity sets
        if len(entities) > 25:
            batches = _chunk_entities_for_extraction(entities)
            log.info("RelationshipExtractor multi-pass", batches=len(batches),
                     entities=len(entities))
            all_rels: list[ExtractedRelationship] = []
            seen: set[tuple] = set()
            for batch in batches:
                batch_rels = await self._extract_single_batch(batch, clusters, api_snapshot)
                for r in batch_rels:
                    key = (r.from_entity, r.edge_type, r.to_entity)
                    if key not in seen:
                        seen.add(key)
                        all_rels.append(r)
            log.info("RelationshipExtractor multi-pass complete",
                     total_edges=len(all_rels))
            return all_rels

        return await self._extract_single_batch(entities, clusters, api_snapshot)

    async def _extract_single_batch(
        self,
        entities: list[ExtractedEntity],
        clusters: list[CommitCluster],
        api_snapshot: dict,
    ) -> list[ExtractedRelationship]:
        """Run the existing single-LLM-call relationship extraction on one batch."""
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

        # Merge: add pattern-distilled edges not already in LLM results.
        # Pre-computed edges have lower precedence; confidence-weighted dedup
        # (below) will keep the best version of any duplicate triple.
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

        # Confidence-weighted dedup (ADR-0043 WS1.S4): replace first-wins with
        # keep-highest-confidence to avoid silently dropping richer edges.
        from companybrain.pipeline._dedup import dedup_relationships_by_confidence
        before_dedup = len(relationships)
        relationships = dedup_relationships_by_confidence(relationships)
        dropped = before_dedup - len(relationships)
        log.info("Relationship extraction complete",
                 relationships=len(relationships), dedup_dropped=dropped)
        return relationships

    # Approximate chars-per-token ratio for Llama models (conservative)
    _CHARS_PER_TOKEN = 3.5
    # Leave headroom for system prompt (~2800 tokens) + output (~1024 tokens).
    # Anthropic Claude Haiku/Sonnet allow much larger inputs (200k context, 200k TPM)
    # than the old Groq Llama free-tier 30k TPM, so we can give the relationship
    # extractor a much bigger window. Old cap of 17.5k starved the model of call
    # sites and is the root cause of '8 edges out of 110 entities'.
    _MAX_INPUT_CHARS = 60_000

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
        # Old cap of 6 entities × 380 chars = 2280 chars total of body code is the
        # root cause of '8 edges per 110 entities'. The relationship LLM cannot
        # find call sites it cannot see. Now we expose up to 30 entities and
        # let each get up to 1500 chars (matches ADR-0040 Tier 1.B snippet size),
        # subject to the overall 60k-char budget.
        # Include Class entities as well so the LLM can find EXTENDS / IMPLEMENTS
        # / USES (constructor injection, @Autowired) edges. Without Class bodies,
        # the new structural-edge types in the prompt have nothing to anchor on.
        snippets = [
            e for e in entities
            if e.entity_type in ("Function", "ApiEndpoint", "Class") and e.code_snippet
        ]
        if snippets:
            _add("\n## Method Body Snippets (find CALLS relationships from these)")
            top_snippets = snippets[:30]
            # Reserve ~6k chars for low-priority sections (queries + commits).
            snippet_budget = max(0, budget - 6_000)
            per_snippet = max(400, min(1500, snippet_budget // max(1, len(top_snippets))))
            for entity in top_snippets:
                fname = entity.file.split("/")[-1]
                block = f"\n### {entity.name}  [{fname}]\n```\n{entity.code_snippet[:per_snippet]}\n```"
                if not _add(block):
                    break

        # ── Priority 4: database queries ──────────────────────────────────────
        # Bumped from 5 → 20 queries; bumped per-query from 150 → 400 chars so
        # JOIN clauses / WHERE columns aren't truncated mid-token. Without this,
        # READS_COLUMN / WRITES_COLUMN edges were almost never extracted.
        queries = [e for e in entities if e.entity_type == "DatabaseQuery" and e.query_text]
        if queries and budget > 200:
            _add("\n## Database Queries (find READS_COLUMN / WRITES_COLUMN edges)")
            for q in queries[:20]:
                if not _add(f"\n- {q.name}: `{(q.query_text or '')[:400]}`"):
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
