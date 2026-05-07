"""
EntityExtractor — LLM Pass 1 of the context builder pipeline.

PRIMARY mode: extract_from_focal_context()
  Receives a FocalContext (actual current source code, role-labelled).
  Processes one CodeUnit at a time so each LLM call stays well within the
  context window of even a small local model (llama3.1:8b = 8k tokens).

FALLBACK mode: extract_from_clusters() [legacy, used only when code tracing fails]
  Processes git commit diffs. Less accurate but works when the codebase
  is unavailable (e.g. remote-only repo without a local clone).

Key design decisions:
- One small, focused LLM call per code unit (controller, service, repo, model)
- git history is NOT used for entity extraction — only for business context (Stage 3)
- Confidence scoring: controller methods = 1.0, downstream layers = 0.9, inferred = 0.7
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryCallState

from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.config import settings
from companybrain.models.entities import CommitCluster, ExtractedEntity
from companybrain.collectors.code_tracer import FocalContext, CodeUnit
from companybrain.pipeline.chunker import Chunker
from companybrain.pipeline.context_manager_agent import ContextAssembly
from companybrain.pipeline.method_chunker import MethodChunker, MethodChunk
from companybrain.pipeline.ast_analyzer import ASTAnalyzer
from companybrain.agents.tools.code_tools import extract_jpa_queries

log = structlog.get_logger(__name__)


# ── Rate-limit-aware retry helpers ───────────────────────────────────────────
#
# Groq free tier hits 429s when method chunking fans out many sub-requests.
# Standard exponential backoff (max 8s) is not enough — rate limit windows
# are 60 seconds.  We detect 429/RateLimitError and use a much longer wait.

def _is_rate_limit(exc: BaseException) -> bool:
    """Return True for any exception that indicates an API rate limit."""
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "rate limit" in msg or "ratelimit" in msg


def _wait_for_rate_limit(retry_state: "RetryCallState") -> float:
    """
    Progressive back-off tuned for Groq's 60-second TPM window:
      attempt 1 → 20s, attempt 2 → 40s, attempt 3 → 60s, attempt 4+ → 90s
    Falls back to standard 5s wait for non-rate-limit errors.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc and _is_rate_limit(exc):
        attempt = retry_state.attempt_number
        wait = min(20 * attempt, 90)
        log.warning("Rate limit hit — backing off", wait_seconds=wait, attempt=attempt)
        return wait
    # Generic error: short exponential backoff
    return min(2 ** retry_state.attempt_number, 15)


# Decorator factories so each method can reference them cleanly
_EXTRACTION_RETRY = dict(
    stop=stop_after_attempt(4),
    wait=_wait_for_rate_limit,
    reraise=True,
)


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT_BASE = """You are a senior software engineer performing deep code analysis for a knowledge graph.
Your task: extract every named entity from source code that is structurally or semantically
relevant to the target API endpoint.

## Output format
Return ONLY valid JSON — no markdown fences, no explanation. Start with { end with }.

{
  "entities": [
    {
      "type": "<see types below>",
      "name": "<exact identifier as it appears in code>",
      "file": "<relative file path provided>",
      "repo": "<repo name provided>",
      "signature": "<full method/class/field signature including params and return type>",
      "confidence": <0.5–1.0>,
      "query_text": "<full SQL/JPQL/jOOQ DSL if type=DatabaseQuery, else omit>"
    }
  ]
}

## Entity types (use exactly these strings)
| Type              | When to use |
|-------------------|-------------|
| ApiEndpoint       | The HTTP handler method itself (controller action) |
| Function          | Any method/function that processes, transforms, or orchestrates data for this endpoint |
| Class             | Any class, interface, abstract class, or record (include services, repos, domain objects) |
| SchemaField       | Request/response DTO fields — format: "ClassName.fieldName" |
| DatabaseTable     | A DB table or entity class representing one (format: table_name) |
| DatabaseColumn    | A specific column accessed — format: "table_name.column_name" |
| DatabaseQuery     | Any DB access: @Query, JPQL, jOOQ, raw JDBC, JPA derived method, stored proc call |
| FrontendComponent | React/Angular/Vue component that renders this endpoint's data |
| ExternalService   | Downstream HTTP client, gRPC call, or message publish |
| ConfigKey         | Feature flag, env var, or @Value property that gates this endpoint's behaviour |
| SharedType        | Shared enum, constant class, or type used across layers |

## Extraction rules

### Always extract
1. The API handler method → ApiEndpoint
2. Every service method in the call chain → Function
3. Every repository/DAO class and interface → Class
4. Every database query (see DatabaseQuery below) → DatabaseQuery + DatabaseColumn
5. Every request/response field that carries business meaning → SchemaField
6. Every external service call → ExternalService

### DatabaseQuery — extract ALL of these
- @Query("...") annotations → use the JPQL/SQL verbatim as query_text
- JPA derived method names (findBy..., countBy..., existsBy...) → query_text = "JPA derived: <method>"
- jOOQ DSL chains (dsl.selectFrom(...).where(...)) → reconstruct approximate SQL as query_text
- JDBC prepareStatement("...") → use the SQL string as query_text
- Named queries (@NamedQuery) → include the JPQL
- Stored procedure calls → query_text = "CALL <proc_name>(...)"

### DatabaseColumn — extract when
- A specific column name appears in a WHERE clause, SELECT list, or UPDATE SET
- Format: "table_name.column_name" — infer table from the entity class name if not explicit

### SchemaField — extract when
- A DTO/request/response field carries business-meaningful data (not just id/uuid)
- Format: "ClassName.fieldName" — include the data type in signature

### Confidence scoring
- 1.0 = entity is explicitly defined in this file
- 0.8 = entity is clearly referenced and its type is unambiguous
- 0.6 = entity is inferred from naming patterns or partial evidence
- Skip anything below 0.5

### Always SKIP
- Test classes (*Test, *Tests, *Spec, *IT, *Mock)
- Logger field declarations
- Bare import statements
- Boilerplate getters/setters with no logic
- Static final string constants (not feature flags)
- Framework-generated proxy classes

## Few-shot examples

### EXAMPLE 1 — Spring Data JPA repository
```java
@Repository
public interface PayerRepository extends JpaRepository<Payer, Long> {
    @Query("SELECT p FROM Payer p WHERE p.lob = :lob AND p.state IN :states")
    List<Payer> findByLobAndStates(@Param("lob") String lob, @Param("states") List<String> states);

    @Query(value = "SELECT * FROM payers WHERE county = ?1 AND active = true", nativeQuery = true)
    List<Payer> findActiveByCounty(String county);

    List<Payer> findByLobAndCountyIn(String lob, List<String> counties);
    long countByLobAndState(String lob, String state);
}
```
Expected:
{"entities": [
  {"type": "Class", "name": "PayerRepository", "file": "...", "repo": "...", "signature": "interface PayerRepository extends JpaRepository<Payer, Long>", "confidence": 1.0},
  {"type": "DatabaseQuery", "name": "findByLobAndStates", "file": "...", "repo": "...", "signature": "List<Payer> findByLobAndStates(String lob, List<String> states)", "confidence": 1.0, "query_text": "SELECT p FROM Payer p WHERE p.lob = :lob AND p.state IN :states"},
  {"type": "DatabaseColumn", "name": "payers.lob", "file": "...", "repo": "...", "signature": "String lob", "confidence": 0.9},
  {"type": "DatabaseColumn", "name": "payers.state", "file": "...", "repo": "...", "signature": "String state", "confidence": 0.9},
  {"type": "DatabaseQuery", "name": "findActiveByCounty", "file": "...", "repo": "...", "signature": "List<Payer> findActiveByCounty(String county)", "confidence": 1.0, "query_text": "SELECT * FROM payers WHERE county = ?1 AND active = true"},
  {"type": "DatabaseQuery", "name": "findByLobAndCountyIn", "file": "...", "repo": "...", "signature": "List<Payer> findByLobAndCountyIn(String lob, List<String> counties)", "confidence": 1.0, "query_text": "JPA derived: findByLobAndCountyIn"},
  {"type": "DatabaseQuery", "name": "countByLobAndState", "file": "...", "repo": "...", "signature": "long countByLobAndState(String lob, String state)", "confidence": 1.0, "query_text": "JPA derived: countByLobAndState"}
]}

### EXAMPLE 2 — jOOQ service method
```java
public List<CompetitorDto> getPayerCompetitors(String lob, List<String> providerTypes, String basePayerName) {
    return dsl.select(
                COMPETITORS.PROVIDER_NAME,
                COMPETITORS.PROVIDER_TYPE,
                COMPETITORS.MARKET_SHARE,
                COMPETITORS.BASE_PAYER_NAME)
            .from(COMPETITORS)
            .where(COMPETITORS.LOB.eq(lob))
            .and(COMPETITORS.PROVIDER_TYPE.in(providerTypes))
            .and(COMPETITORS.BASE_PAYER_NAME.eq(basePayerName))
            .fetchInto(CompetitorDto.class);
}
```
Expected:
{"entities": [
  {"type": "Function", "name": "getPayerCompetitors", "file": "...", "repo": "...", "signature": "List<CompetitorDto> getPayerCompetitors(String lob, List<String> providerTypes, String basePayerName)", "confidence": 1.0},
  {"type": "DatabaseQuery", "name": "getPayerCompetitors_query", "file": "...", "repo": "...", "signature": "jOOQ: select(PROVIDER_NAME, PROVIDER_TYPE, MARKET_SHARE, BASE_PAYER_NAME).from(COMPETITORS).where(...)", "confidence": 0.95, "query_text": "SELECT provider_name, provider_type, market_share, base_payer_name FROM competitors WHERE lob = ? AND provider_type IN (?) AND base_payer_name = ?"},
  {"type": "DatabaseColumn", "name": "competitors.lob", "file": "...", "repo": "...", "signature": "String lob (filter)", "confidence": 1.0},
  {"type": "DatabaseColumn", "name": "competitors.provider_type", "file": "...", "repo": "...", "signature": "String provider_type (filter+select)", "confidence": 1.0},
  {"type": "DatabaseColumn", "name": "competitors.market_share", "file": "...", "repo": "...", "signature": "BigDecimal market_share (select)", "confidence": 0.9},
  {"type": "DatabaseColumn", "name": "competitors.base_payer_name", "file": "...", "repo": "...", "signature": "String base_payer_name (filter+select)", "confidence": 1.0}
]}

### EXAMPLE 3 — REST controller with DTO
```java
@GetMapping("/competitors/payer")
public ResponseEntity<CompetitivenessPayerSummaryDTO> getPayerCompetitors(
        @RequestBody NiqAPIRequest request,
        @RequestParam(defaultValue = "PLAN") String viewBy) {
    return ResponseEntity.ok(competitivenessService.getPayerCompetitors(request, ViewBy.valueOf(viewBy)));
}
```
Expected:
{"entities": [
  {"type": "ApiEndpoint", "name": "getPayerCompetitors", "file": "...", "repo": "...", "signature": "GET /competitors/payer → ResponseEntity<CompetitivenessPayerSummaryDTO>", "confidence": 1.0},
  {"type": "SchemaField", "name": "NiqAPIRequest.basePayerName", "file": "...", "repo": "...", "signature": "String basePayerName (request body field)", "confidence": 0.8},
  {"type": "SchemaField", "name": "CompetitivenessPayerSummaryDTO.viewBy", "file": "...", "repo": "...", "signature": "String viewBy (PLAN|PRODUCT enum filter)", "confidence": 0.85},
  {"type": "SharedType", "name": "ViewBy", "file": "...", "repo": "...", "signature": "enum ViewBy { PLAN, PRODUCT }", "confidence": 0.9}
]}

### EXAMPLE 4 — Async service with external call
```java
@Async
public CompletableFuture<TamResult> getTam(String lob, String state) {
    TamRequest req = TamRequest.builder().lob(lob).state(state).build();
    TamResult result = tamApiClient.fetchTam(req);   // downstream HTTP call
    return CompletableFuture.completedFuture(result);
}
```
Expected:
{"entities": [
  {"type": "Function", "name": "getTam", "file": "...", "repo": "...", "signature": "CompletableFuture<TamResult> getTam(String lob, String state)", "confidence": 1.0},
  {"type": "ExternalService", "name": "tamApiClient.fetchTam", "file": "...", "repo": "...", "signature": "TamResult fetchTam(TamRequest req)", "confidence": 0.9}
]}
"""

_USER_TEMPLATE = """## Target Endpoint
{method} {endpoint}

{workspace_context}## Code Unit — {role}
File: {file_path}
Repo: {repo_name}
Language: {language}

```{language}
{content}
```

## Task
Extract all entities from this {role} that are directly involved in handling {method} {endpoint}.
Follow the system prompt rules exactly. Return only the JSON object."""

# Fallback prompt for git-diff extraction (used when live source code is unavailable).
# Stricter than the source-code prompt — diffs are noisy, only yield business-logic entities.
_DIFF_SYSTEM_PROMPT = """You are a senior engineer extracting business-logic entities from a git diff.
The diff relates to a specific API endpoint. Your goal is to identify what LOGIC changed —
not what fields were added, not what constants were renamed.

Return ONLY valid JSON — no markdown, no explanation. Start with { end with }.

Schema:
{"entities": [{"type": "<Function|Class|ApiEndpoint|DatabaseQuery|ExternalService>", "name": "<identifier>", "file": "<filename>", "repo": "<repo>", "signature": "<full method/class signature>", "confidence": <0.5-1.0>, "query_text": "<SQL if DatabaseQuery>"}]}

## Decision tree — for each changed block ask:
1. Does this add/modify a method or function body? → Function or ApiEndpoint
2. Does this add/modify a class with methods? → Class
3. Does this add/modify a SQL query, @Query, jOOQ call, or JDBC statement? → DatabaseQuery
4. Does this add/modify an HTTP client call? → ExternalService
5. Is this only a field addition, constant rename, import change, or logger line? → SKIP (return nothing for this block)

## Hard rules
- SKIP entirely: static final constants, added fields without method bodies, import changes, logger declarations, test classes
- A valid entity MUST contain executable logic — a method body, a query string, or a class with methods
- If a diff block only adds/removes a record field or enum value with no logic, return no entity for it
- Confidence: 1.0=method body present, 0.8=method signature visible, 0.6=inferred from annotation/naming
- type must be exactly one of: Function, Class, ApiEndpoint, DatabaseQuery, ExternalService
- If nothing in the diff qualifies, return {"entities": []}"""


# ── Main class ────────────────────────────────────────────────────────────────

class EntityExtractor:
    """
    LLM Pass 1: extract named entities from the focal code context.

    Primary path: extract_from_focal_context(focal_context)
    Fallback path: extract_from_clusters(clusters, api_snapshot)  [diff-based, legacy]
    """

    def __init__(self):
        self._provider = get_provider()
        self._chunker = Chunker()
        self._method_chunker = MethodChunker()
        self._ast_analyzer = ASTAnalyzer()
        log.info(
            "EntityExtractor ready",
            llm_provider=self._provider.provider_name,
            model=self._provider.model_for_role(TaskRole.FAST),
        )

    # ── Primary: code-based extraction ───────────────────────────────────────

    async def extract_from_focal_context(
        self,
        focal_context: FocalContext,
        context_assemblies: dict[str, "ContextAssembly"] | None = None,
    ) -> list[ExtractedEntity]:
        """
        Extract entities by reading the actual current source code.

        Processes CodeUnits sequentially (not parallel) to avoid overwhelming
        a local model — each call is small and fast (one class at a time).

        Args:
            focal_context:       Code units to extract from.
            context_assemblies:  Optional map of file_path → ContextAssembly from the
                                 Context Manager Agent. When provided, each extraction call
                                 gets an L2-enriched prompt. When absent, falls back to
                                 plain extraction (no shared context).
        """
        if focal_context.is_empty():
            log.warning("FocalContext is empty — no code units to extract from")
            return []

        log.info(
            "Starting code-based entity extraction",
            units=len(focal_context.code_units),
            roles=[u.role for u in focal_context.code_units],
            l2_enriched=context_assemblies is not None,
        )

        all_entities: list[ExtractedEntity] = []

        # Process one unit at a time — keeps prompts tiny (one class = ~1-3k tokens)
        for unit in focal_context.code_units:
            assembly = (context_assemblies or {}).get(unit.file_path)
            try:
                entities = await self._extract_from_code_unit(unit, focal_context, assembly)
                log.info(
                    "Unit extraction complete",
                    unit=unit.brief(),
                    entities_found=len(entities),
                    cm_patch=bool(assembly and assembly.system_prompt_patch),
                )
                all_entities.extend(entities)
            except Exception as e:
                log.error("Code unit extraction failed", unit=unit.brief(), error=str(e))

        deduplicated = self._deduplicate(all_entities)
        log.info(
            "Code-based entity extraction complete",
            raw=len(all_entities),
            deduplicated=len(deduplicated),
        )
        return deduplicated

    async def _extract_from_code_unit(
        self,
        unit: CodeUnit,
        focal_context: FocalContext,
        assembly: "ContextAssembly | None" = None,
    ) -> list[ExtractedEntity]:
        """
        Extract entities from one CodeUnit.

        If the unit content is large (> METHOD_SPLIT_THRESHOLD), splits it into
        per-method MethodChunks and runs one small LLM call per method.  This
        dramatically improves recall for DatabaseQuery entities buried inside
        helper methods and keeps individual prompt sizes under 2k tokens even
        for large service classes.

        Small units are processed in a single LLM call (legacy path).
        """
        # ── Fast-path: pure interface / abstract contract — no LLM needed ────
        # Java/Kotlin interfaces contain only method signatures — there is no
        # implementation logic to extract. Emitting entities directly from the
        # signatures is zero-cost and produces higher-fidelity output than
        # asking the LLM to paraphrase what the signature already states.
        #
        # Detection: content contains "interface <Name>" (Java/Kotlin) or
        # every non-blank non-comment line ends with ";" (all abstract).
        if _is_pure_interface(unit):
            log.info(
                "Interface fast-path: skipping LLM, emitting from signatures",
                unit=unit.brief(),
            )
            return _entities_from_interface(unit)

        # Try AST-based chunking first (tree-sitter — exact line ranges, no regex).
        # Falls back to regex MethodChunker if the grammar is unavailable.
        symbol_table = self._ast_analyzer.analyze(unit)
        if symbol_table:
            method_chunks = symbol_table.to_method_chunks(unit)
            if method_chunks:
                log.info(
                    "AST-based method chunking active",
                    unit=unit.brief(),
                    method_chunks=len(method_chunks),
                    classes=len(symbol_table.classes),
                )
        else:
            method_chunks = self._method_chunker.split(unit)

        if method_chunks:
            # ── Controller scope filter ───────────────────────────────────────
            # A controller file often has 5–20 endpoint methods. Only the method
            # that handles the target endpoint is relevant — the others belong to
            # different request paths and would pollute the graph with unrelated
            # entities. Filter to just the entry-point method when we know it.
            #
            # `entry_method` is the Java handler method name (e.g. "getPayerCompetitors"),
            # NOT the HTTP verb.  FocalContext.entry_method is set by CodeTracer from the
            # LLM handler-finder result; it is "" when tracing falls back to regex.
            #
            # For service / repository units we DO NOT filter: all methods are
            # reachable from the entry point through the call chain.
            entry_method = focal_context.entry_method  # e.g. "getPayerCompetitors"
            is_controller = unit.role and "controller" in unit.role.lower()
            if is_controller and entry_method:
                # Primary match: exact or substring match on chunk.method_name
                filtered = [c for c in method_chunks
                            if c.method_name == entry_method
                            or entry_method.lower() in c.method_name.lower()]

                # Fallback: AST sometimes produces garbage method_names due to
                # byte/char offset mismatch — search the chunk's raw content instead.
                if not filtered:
                    filtered = [c for c in method_chunks
                                if entry_method in (c.content or "")]

                if filtered:
                    log.info(
                        "Controller scope filter applied",
                        unit=unit.brief(),
                        entry_method=entry_method,
                        before=len(method_chunks),
                        after=len(filtered),
                        skipped=[c.method_name for c in method_chunks
                                 if c not in filtered],
                    )
                    method_chunks = filtered
                else:
                    log.warning(
                        "Controller scope filter found no match — processing all chunks",
                        unit=unit.brief(),
                        entry_method=entry_method,
                        available=[c.method_name for c in method_chunks],
                    )

            # ── Skeleton-first pre-filter ─────────────────────────────────────
            # For large files (content > 600 chars AND > 1 method), build a
            # skeleton string from AST method signatures and ask a FAST LLM call
            # which methods are relevant to the target endpoint.  Only those
            # methods (plus always the entry method) are sent for full extraction.
            # This replaces sending all N method bodies when only K << N matter.
            content_len = len(unit.content or "")
            if content_len > 600 and len(method_chunks) > 1:
                method_chunks = await self._apply_skeleton_prefilter(
                    method_chunks, symbol_table, unit, focal_context, entry_method
                )

            log.info(
                "Method-level chunking active",
                unit=unit.brief(),
                method_chunks=len(method_chunks),
                original_chars=len(unit.content or ""),
            )
            all_entities: list[ExtractedEntity] = []
            for chunk in method_chunks:
                try:
                    chunk_entities = await self._extract_from_method_chunk(
                        chunk, focal_context, assembly
                    )
                    all_entities.extend(chunk_entities)
                except Exception as e:
                    log.warning(
                        "Method chunk extraction failed — skipping",
                        method=chunk.method_name,
                        error=str(e),
                    )
            # JPA deterministic pass still runs once on the full unit
            if unit.role in ("repository", "dao") and unit.file_path:
                jpa_entities = self._extract_jpa_query_entities(unit)
                existing = {e.name for e in all_entities}
                for qe in jpa_entities:
                    if qe.name not in existing:
                        all_entities.append(qe)
            return all_entities

        # ── Small unit: single LLM call (original path) ──────────────────────
        return await self._extract_from_code_unit_single(unit, focal_context, assembly)

    @retry(**_EXTRACTION_RETRY)
    async def _extract_from_method_chunk(
        self,
        chunk: MethodChunk,
        focal_context: FocalContext,
        assembly: "ContextAssembly | None" = None,
    ) -> list[ExtractedEntity]:
        """Run entity extraction on a single method-level chunk."""
        workspace_context = ""
        if assembly and assembly.l2_section:
            workspace_context = assembly.l2_section + "\n\n"

        user_content = _USER_TEMPLATE.format(
            method=focal_context.method,
            endpoint=focal_context.endpoint,
            workspace_context=workspace_context,
            role=chunk.role,
            file_path=chunk.file_path,
            repo_name=chunk.repo_name,
            language=chunk.language,
            content=chunk.content,
        )

        system_prompt = _SYSTEM_PROMPT_BASE
        if assembly and assembly.system_prompt_patch:
            system_prompt = _SYSTEM_PROMPT_BASE + f"\n\nAdditional context: {assembly.system_prompt_patch}"

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user",   content=user_content),
            ],
            role=TaskRole.FAST,
            max_tokens=settings.max_tokens_entity_extraction,
        )

        entities = self._parse_entities(raw)

        # Populate code_snippet from the chunk content (method body)
        for entity in entities:
            if entity.entity_type in ("Function", "ApiEndpoint"):
                entity.code_snippet = _extract_snippet(chunk.content, entity.name)

        return entities

    @retry(**_EXTRACTION_RETRY)
    async def _extract_from_code_unit_single(
        self,
        unit: CodeUnit,
        focal_context: FocalContext,
        assembly: "ContextAssembly | None" = None,
    ) -> list[ExtractedEntity]:
        """Single LLM call for a small code unit (original path)."""
        # Inject L2 workspace context section if provided by the CM Agent
        workspace_context = ""
        if assembly and assembly.l2_section:
            workspace_context = assembly.l2_section + "\n\n"

        # Inject AST signature block if available — helps LLM extract exact names
        ast_block = ""
        try:
            sym = self._ast_analyzer.analyze(unit)
            if sym:
                sig = sym.to_signature_block()
                if sig.strip():
                    ast_block = f"## AST Signatures (use these exact names)\n{sig}\n\n"
        except Exception:
            pass

        workspace_context = ast_block + workspace_context

        user_content = _USER_TEMPLATE.format(
            method=focal_context.method,
            endpoint=focal_context.endpoint,
            workspace_context=workspace_context,
            role=unit.role,
            file_path=unit.file_path,
            repo_name=unit.repo_name,
            language=unit.language,
            content=unit.content,
        )

        # Append CM Agent's system prompt patch (domain-specific guidance for this unit)
        system_prompt = _SYSTEM_PROMPT_BASE
        if assembly and assembly.system_prompt_patch:
            system_prompt = _SYSTEM_PROMPT_BASE + f"\n\nAdditional context: {assembly.system_prompt_patch}"

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user",   content=user_content),
            ],
            role=TaskRole.FAST,
            max_tokens=settings.max_tokens_entity_extraction,
        )

        entities = self._parse_entities(raw)

        # Populate code_snippet for Function/ApiEndpoint entities from the unit content.
        # This lets the RelationshipExtractor see call sites (e.g. service.method() calls)
        # without needing the full method body in the relationship prompt.
        for entity in entities:
            if entity.entity_type in ("Function", "ApiEndpoint"):
                entity.code_snippet = _extract_snippet(unit.content, entity.name)

        # For Repository/DAO units: also run deterministic JPA query extraction.
        # This catches @Query annotations, createNativeQuery, derived findBy... methods
        # that the LLM often misses or truncates.
        if unit.role in ("repository", "dao") and unit.file_path:
            jpa_entities = self._extract_jpa_query_entities(unit)
            existing = {e.name for e in entities}
            for qe in jpa_entities:
                if qe.name not in existing:
                    entities.append(qe)

        return entities

    async def _apply_skeleton_prefilter(
        self,
        method_chunks: list["MethodChunk"],
        symbol_table,
        unit: "CodeUnit",
        focal_context: "FocalContext",
        entry_method: str,
    ) -> list["MethodChunk"]:
        """
        Skeleton-first pre-filter: ask a cheap FAST LLM call which methods in
        a large file are relevant to the target endpoint, then narrow
        method_chunks to only those methods.

        Returns the original list unchanged if the LLM call fails or returns
        nothing useful.
        """
        # Build skeleton from AST symbol table or fall back to method chunk names.
        if symbol_table and symbol_table.classes:
            skeleton_lines = []
            for cls in symbol_table.classes:
                skeleton_lines.append(f"class {cls.name}:")
                for sig in (cls.method_signatures if hasattr(cls, "method_signatures") else []):
                    skeleton_lines.append(f"  {sig}")
            # If the symbol table didn't yield signatures, fall back to chunk names.
            if not any("  " in line for line in skeleton_lines):
                skeleton_lines = [f"  {c.method_name}" for c in method_chunks]
        else:
            # No AST — just list method names from the chunks.
            skeleton_lines = [f"  {c.method_name}" for c in method_chunks]

        skeleton_str = "\n".join(skeleton_lines)

        prompt = (
            f"Which of these methods are directly relevant to handling the "
            f"{focal_context.method} {focal_context.endpoint} endpoint?\n\n"
            f"{skeleton_str}\n\n"
            f'Return a JSON array of method names only, e.g. ["name1", "name2"]. '
            f"Return an empty array [] if none are clearly relevant."
        )

        try:
            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(
                        role="system",
                        content="You are a code analyst. Answer with a JSON array of method names only.",
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                role=TaskRole.FAST,
                max_tokens=300,
            )

            # raw may already be a list after chat_json strips fences.
            if isinstance(raw, str):
                relevant_names: list[str] = json.loads(raw)
            else:
                relevant_names = raw  # type: ignore[assignment]

            if not relevant_names or not isinstance(relevant_names, list):
                return method_chunks

            relevant_lower = {n.lower() for n in relevant_names}

            # Always include the entry method if known.
            if entry_method:
                relevant_lower.add(entry_method.lower())

            filtered = [
                c for c in method_chunks
                if c.method_name.lower() in relevant_lower
                or any(rel in c.method_name.lower() for rel in relevant_lower)
            ]

            if filtered:
                log.info(
                    "skeleton_prefilter_applied",
                    unit=unit.brief(),
                    skeleton_prefilter_applied=True,
                    methods_before=len(method_chunks),
                    methods_after=len(filtered),
                )
                return filtered

        except Exception as exc:
            log.debug(
                "Skeleton pre-filter failed — using all chunks",
                unit=unit.brief(),
                error=str(exc),
            )

        return method_chunks

    def _extract_jpa_query_entities(self, unit: "CodeUnit") -> list[ExtractedEntity]:
        """
        Deterministically extract DatabaseQuery entities from a repository code unit.
        Runs extract_jpa_queries (pure Python regex) on the file — no LLM call.
        """
        queries = extract_jpa_queries(unit.file_path)
        results = []
        for q in queries:
            method = q.get("method") or q.get("query", "")[:40]
            if not method:
                continue
            results.append(ExtractedEntity(
                entity_type="DatabaseQuery",
                name=method,
                file=unit.file_path,
                repo=unit.repo_name,
                signature=f"{q['type'].upper()}: {q['query'][:120]}",
                last_modified_commit="",
                confidence=0.95,
                query_text=q.get("query"),
            ))
        if results:
            log.info("JPA query extraction", file=unit.file_path, queries=len(results))
        return results

    # ── ADR-006 §29: Structural-hints enrichment (Pass 1 optimisation) ──────────

    async def extract_with_structural_hints(
        self,
        structural_nodes: list[dict],
        endpoint: str,
        method: str = "GET",
    ) -> list[ExtractedEntity]:
        """ADR-006 §29: Enrich pre-parsed structural entities with LLM semantics.

        When the structural index already knows an entity's name, type, file,
        and location, the LLM does NOT need to discover these from raw code.
        This method accepts a list of structural node records (from the nodes
        table) and asks the LLM only to add:

          - purpose      — plain-English description of what the entity does
          - dataReads    — data sources read (table names, external services)
          - dataWrites   — data targets written
          - riskFlags    — business-level risk signals (e.g. "touches PII")
          - changeRisk   — "high" | "medium" | "low" with one-sentence reasoning

        Token reduction vs. extract_from_focal_context():
          The raw-code path spends ~2–4K tokens per unit on entity discovery
          (sending the full method/class body so the LLM can read names and types).
          This path sends only the entity list (~100–200 tokens per entity for
          the LLM's reply), skipping discovery entirely.
          Target: 30–50% reduction in Pass 1 prompt tokens on a typical workspace.

        Args:
            structural_nodes: List of node dicts from the Postgres `nodes` table.
                Each dict must contain: id, name, node_type, qualified_name, file_path.
                Optional: risk_score, risk_factors, line_start, line_end.
            endpoint:         The API endpoint being analysed (context only).
            method:           HTTP verb (context only).

        Returns:
            list[ExtractedEntity] — same format as extract_from_focal_context().
            Entities that fail enrichment are returned with confidence=0.6 and
            empty semantic fields (name/type/location are still correct).
        """
        if not structural_nodes:
            log.warning("extract_with_structural_hints: no structural nodes provided")
            return []

        log.info(
            "Pass 1 (structural-hints): enriching %d pre-parsed entities",
            len(structural_nodes),
            endpoint=endpoint,
        )

        # Batch into groups of 15 — keeps each prompt under ~2K tokens.
        _BATCH = 15
        all_entities: list[ExtractedEntity] = []

        for i in range(0, len(structural_nodes), _BATCH):
            batch = structural_nodes[i : i + _BATCH]
            try:
                enriched = await self._enrich_batch(batch, endpoint, method)
                all_entities.extend(enriched)
            except Exception as exc:
                log.error(
                    "extract_with_structural_hints: batch %d failed, using stubs: %s",
                    i // _BATCH, exc,
                )
                # Return stub entities with structural data intact but no semantics.
                all_entities.extend(self._stub_entities(batch))

        log.info(
            "Pass 1 (structural-hints) complete: entities=%d", len(all_entities)
        )
        return all_entities

    @retry(**_EXTRACTION_RETRY)
    async def _enrich_batch(
        self,
        nodes: list[dict],
        endpoint: str,
        method: str,
    ) -> list[ExtractedEntity]:
        """Single LLM call to semantically enrich one batch of structural nodes."""

        # Build a compact entity list for the prompt — NO raw code.
        entity_lines = []
        for idx, n in enumerate(nodes):
            entity_lines.append(
                f"{idx + 1}. [{n.get('node_type', 'Function')}] {n.get('name', '?')} "
                f"({n.get('file_path', '?')})"
            )

        entity_block = "\n".join(entity_lines)

        user_prompt = (
            f"Endpoint: {method} {endpoint}\n\n"
            f"The following entities have been identified by static analysis:\n\n"
            f"{entity_block}\n\n"
            f"For each entity (numbered as above), return a JSON array with one object per entity.\n"
            f"Each object must have exactly these keys:\n"
            f"  idx        — the 1-based index from the list above (integer)\n"
            f"  purpose    — one sentence: what this entity does\n"
            f"  dataReads  — list of data sources read (table names, service names, or [])\n"
            f"  dataWrites — list of data targets written (or [])\n"
            f"  riskFlags  — list of risk keywords present (e.g. 'payment', 'pii', 'auth'), or []\n"
            f"  changeRisk — 'high' | 'medium' | 'low'\n\n"
            f"Return ONLY the JSON array, no prose."
        )

        system_prompt = (
            "You are a code analyst. You are given a list of entities identified by a "
            "static-analysis parser. Your job is to add business-level semantics only — "
            "do NOT re-identify names, types, or locations. Be concise and precise."
        )

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            role=TaskRole.FAST,
            max_tokens=512,   # Much smaller than full extraction — we only need 5 fields
        )

        # Parse the JSON array keyed by idx
        enrichment_map: dict[int, dict] = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and "idx" in item:
                    enrichment_map[int(item["idx"])] = item
        elif isinstance(raw, dict):
            # Some models wrap the array in {"entities": [...]}
            items = raw.get("entities") or raw.get("result") or []
            for item in items:
                if isinstance(item, dict) and "idx" in item:
                    enrichment_map[int(item["idx"])] = item

        entities: list[ExtractedEntity] = []
        for idx, node in enumerate(nodes, start=1):
            enrich = enrichment_map.get(idx, {})
            entities.append(ExtractedEntity(
                entity_type=_map_node_type(node.get("node_type", "Function")),
                name=node.get("name", ""),
                file=node.get("file_path", ""),
                repo=node.get("repo_name", ""),
                signature=node.get("qualified_name", ""),
                last_modified_commit="",
                confidence=0.85,   # structural=1.0 identity + LLM-enriched semantics
                # ADR-006 §29 enrichment fields
                structural_purpose=enrich.get("purpose") or "",
                structural_data_reads=enrich.get("dataReads") or [],
                structural_data_writes=enrich.get("dataWrites") or [],
                structural_risk_flags=enrich.get("riskFlags") or [],
                structural_change_risk=enrich.get("changeRisk", "medium"),
            ))
        return entities

    @staticmethod
    def _stub_entities(nodes: list[dict]) -> list[ExtractedEntity]:
        """Return stub entities from structural data alone — no LLM semantics."""
        return [
            ExtractedEntity(
                entity_type=_map_node_type(n.get("node_type", "Function")),
                name=n.get("name", ""),
                file=n.get("file_path", ""),
                repo=n.get("repo_name", ""),
                signature=n.get("qualified_name", ""),
                last_modified_commit="",
                confidence=0.60,   # structural identity only, no semantics
            )
            for n in nodes
        ]

    # ── Fallback: diff-based extraction (legacy) ─────────────────────────────

    async def extract_from_clusters(
        self,
        clusters: list[CommitCluster],
        api_snapshot: dict,
    ) -> list[ExtractedEntity]:
        """
        Legacy fallback: extract from git commit diffs when no focal context is available.
        Less accurate than code-based extraction. Processes chunks in parallel.
        """
        chunks = self._chunker.chunk_clusters(clusters, api_snapshot)
        log.info("Starting diff-based entity extraction (fallback)", chunks=len(chunks))

        tasks = [self._extract_from_diff_chunk(chunk, api_snapshot) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_entities: list[ExtractedEntity] = []
        for result in results:
            if isinstance(result, Exception):
                log.error("Diff chunk extraction failed", error=str(result))
                continue
            all_entities.extend(result)

        deduplicated = self._deduplicate(all_entities)
        log.info("Diff-based extraction complete", raw=len(all_entities), deduplicated=len(deduplicated))
        return deduplicated

    @retry(**_EXTRACTION_RETRY)
    async def _extract_from_diff_chunk(self, chunk: dict, api_snapshot: dict) -> list[ExtractedEntity]:
        parts = [
            f"Endpoint: {api_snapshot.get('method', 'GET')} {api_snapshot.get('path', '')}",
            f"Commits ({len(chunk.get('commits', []))}):",
        ]
        for c in chunk.get("commits", [])[:5]:  # cap at 5 commits per chunk
            parts.append(f"- {c['commit_hash'][:7]}: {c['message'][:80]}")
            if c.get("diff"):
                parts.append(f"```diff\n{c['diff'][:1000]}\n```")

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_DIFF_SYSTEM_PROMPT),
                ChatMessage(role="user", content="\n".join(parts)),
            ],
            role=TaskRole.FAST,
            max_tokens=settings.max_tokens_entity_extraction,
        )
        return self._parse_entities(raw)

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _parse_entities(self, raw: str) -> list[ExtractedEntity]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("Failed to parse entity JSON", error=str(e), raw=raw[:300])
            return []

        entities = []
        for item in data.get("entities", []):
            try:
                entities.append(ExtractedEntity(
                    entity_type=item["type"],
                    name=item["name"],
                    file=item.get("file", ""),
                    repo=item.get("repo", ""),
                    signature=item.get("signature", ""),
                    first_appeared_commit=None,
                    last_modified_commit="",
                    confidence=float(item.get("confidence", 0.7)),
                    query_text=item.get("query_text"),
                ))
            except (KeyError, ValueError) as e:
                log.debug("Skipping malformed entity", error=str(e), item=item)
        return entities

    @staticmethod
    def _deduplicate(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """
        Deduplicate by (entity_type, name, repo).
        Keep highest-confidence duplicate; merge first_appeared_commit where missing.
        """
        seen: dict[tuple, ExtractedEntity] = {}
        for entity in entities:
            key = (entity.entity_type, entity.name.lower(), entity.repo)
            if key not in seen or entity.confidence > seen[key].confidence:
                seen[key] = entity
            elif entity.first_appeared_commit and not seen[key].first_appeared_commit:
                seen[key].first_appeared_commit = entity.first_appeared_commit
        return list(seen.values())


# ── Module helpers ────────────────────────────────────────────────────────────

import re as _re


def _map_node_type(structural_kind: str) -> str:
    """Map structural node_type (from parser.py) → ExtractedEntity entity_type.

    ADR-006 §29: the structural layer uses 'Function', 'Method', 'Class', etc.
    The entity extraction layer uses 'Function', 'ApiEndpoint', 'DatabaseQuery', etc.
    """
    _MAP = {
        "Function":   "Function",
        "Method":     "Function",
        "Class":      "Function",     # treated as a unit; type refined downstream
        "Route":      "ApiEndpoint",
        "Endpoint":   "ApiEndpoint",
        "Handler":    "ApiEndpoint",
        "Controller": "ApiEndpoint",
        "Repository": "DatabaseQuery",
        "DAO":        "DatabaseQuery",
        "Mapper":     "DatabaseQuery",
        "Schema":     "Function",
        "Test":       "Function",
    }
    return _MAP.get(structural_kind, "Function")

def _extract_snippet(source: str, method_name: str, max_chars: int = 400) -> str:
    """
    Pull a compact snippet of a method body from source text.
    Used to populate ExtractedEntity.code_snippet so the relationship extractor
    can see actual call sites (e.g. `competitorsService.getPayerCompetitors()`).

    Returns the method signature + first max_chars chars of the body, or "" if not found.
    """
    if not source or not method_name:
        return ""
    pattern = _re.compile(
        rf'(?:public|private|protected|default)\s+[\w<>\[\]?,\s]+\s+{_re.escape(method_name)}\s*\(',
        _re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return ""
    # Include from method signature start up to max_chars
    snippet = source[m.start(): m.start() + max_chars]
    # Remove multi-line comment blocks to save space
    snippet = _re.sub(r'/\*.*?\*/', '', snippet, flags=_re.DOTALL)
    return snippet.strip()


# ── Interface fast-path helpers ───────────────────────────────────────────────

def _is_pure_interface(unit: "CodeUnit") -> bool:
    """
    Return True when the code unit is a pure interface / abstract contract
    with no implementation bodies — so the LLM would add zero value.

    Detects:
      - Java/Kotlin:  `public interface Foo {` or `interface Foo {`
      - TypeScript:   `interface Foo {` (no method bodies, just signatures)
      - Python ABCs:  every method is `@abstractmethod` with `pass` / `...`
    """
    content = unit.content or ""
    if not content.strip():
        return False

    lang = getattr(unit, "language", None) or ""
    file_path = str(getattr(unit, "file_path", "") or "")

    # ── Java / Kotlin ─────────────────────────────────────────────────────────
    if lang in ("java", "kotlin") or file_path.endswith((".java", ".kt")):
        # Strip single-line and block comments
        stripped = _re.sub(r'//.*', '', content)
        stripped = _re.sub(r'/\*.*?\*/', '', stripped, flags=_re.DOTALL)
        # Look for the interface keyword at the top level
        if _re.search(r'\binterface\s+\w+', stripped):
            return True
        # Also catch annotation-only types (@FunctionalInterface etc.)
        if _re.search(r'@(FunctionalInterface|interface)\b', stripped):
            return True

    # ── TypeScript / JavaScript ────────────────────────────────────────────────
    if lang in ("typescript", "javascript") or file_path.endswith((".ts", ".d.ts")):
        stripped = _re.sub(r'//.*', '', content)
        stripped = _re.sub(r'/\*.*?\*/', '', stripped, flags=_re.DOTALL)
        if _re.search(r'\binterface\s+\w+', stripped):
            return True

    # ── Python ABC ────────────────────────────────────────────────────────────
    if lang == "python" or file_path.endswith(".py"):
        if "(ABC)" in content or "(abc.ABC)" in content or "Protocol" in content:
            # Check all methods are abstract (no real body beyond pass / ...)
            method_bodies = _re.findall(
                r'def \w+.*?(?=\n    def |\nclass |\Z)', content, _re.DOTALL
            )
            if method_bodies and all(
                _re.search(r'(pass|\.\.\.)\s*$', b.strip()) for b in method_bodies
            ):
                return True

    return False


def _entities_from_interface(unit: "CodeUnit") -> "list[ExtractedEntity]":
    """
    Emit one ExtractedEntity per method signature in the interface —
    no LLM, purely from the source text.  This is fast and precise
    because interfaces carry full type information in their signatures.
    """
    from companybrain.models.entities import ExtractedEntity

    content  = unit.content or ""
    entities: list[ExtractedEntity] = []

    # Extract method signatures: lines ending with `;` that look like declarations
    sig_pattern = _re.compile(
        r'(?:(?:public|protected|private|default|static|default)\s+)*'
        r'[\w<>\[\]?,\s]+\s+'       # return type (may have generics)
        r'(\w+)\s*'                 # method name (capture group 1)
        r'\([^)]*\)'                # parameters
        r'(?:\s+throws\s+[\w,\s]+)?' # optional throws
        r'\s*;',                    # ends with semicolon = no body
        _re.MULTILINE,
    )

    file_str  = str(getattr(unit, "file_path", "") or "")
    repo_str  = str(getattr(unit, "repo_name", "") or "")

    for m in sig_pattern.finditer(content):
        method_name = m.group(1)
        signature   = m.group(0).strip().rstrip(";").strip()

        entities.append(ExtractedEntity(
            name=method_name,
            entity_type="InterfaceMethod",
            file=file_str,
            repo=repo_str,
            signature=signature,
            last_modified_commit="",
            confidence=1.0,           # signatures are deterministic — no uncertainty
            structural_purpose="interface_contract",
            code_snippet=signature,
        ))

    if not entities:
        # Fallback: emit the interface itself as a single entity
        interface_match = _re.search(r'\binterface\s+(\w+)', content)
        name = interface_match.group(1) if interface_match else (
            unit.name if hasattr(unit, "name") else "UnknownInterface"
        )
        entities.append(ExtractedEntity(
            name=name,
            entity_type="Interface",
            file=file_str,
            repo=repo_str,
            signature=f"interface {name}",
            last_modified_commit="",
            confidence=1.0,
            structural_purpose="interface_contract",
            code_snippet="",
        ))

    log.debug(
        "Interface fast-path entities emitted",
        unit=unit.brief() if hasattr(unit, "brief") else file_path,
        count=len(entities),
        names=[e.name for e in entities],
    )
    return entities
