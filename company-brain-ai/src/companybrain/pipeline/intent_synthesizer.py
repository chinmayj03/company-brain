"""
IntentSynthesizer — Task #37: Method-level intent and data flow extraction.

Takes the structured output from ClassNodeExtractor and NavigatorAgent,
then uses ONE focused LLM call to classify each method's intent:

  DETERMINISTIC (no LLM):
    • Call sites: which services/repos does this method call, with what arguments
    • Data reads: which DB tables / repositories are queried
    • Data writes: which DB tables / repositories are mutated
    • Return type analysis: pagination, list, single entity, void, event

  LLM-CLASSIFIED (single call per class):
    • Intent label: data_read | data_write | orchestration | side_effect |
                   validation | event_emit | cache_op | external_call
    • Business description: "Retrieves competitor payer summary filtered by market ID"
    • Side effects: "Publishes CompetitorPricingEvent to SQS after each update"
    • Data assumptions: "Assumes payer_type is always one of: COMMERCIAL, MEDICARE, MEDICAID"
    • Gaps: things the code cannot tell us (what does payerType mean in business terms?)

Why one LLM call per class (not per method)?
  - A service class typically has 3-8 methods. Batching them in one call is far cheaper.
  - The LLM can see how methods relate to each other (one method calls another).
  - A single class fits in a 4k token window comfortably.

Output feeds into:
  1. Entity extractor context (replaces the raw source block)
  2. BusinessContext synthesis (Stage 3) -- adds method-level intent to history_summary
  3. Gap detection (Stage 4) -- "what are the valid values for payerType?"
  4. Knowledge graph Node.metadata["method_intents"]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.llm.base import ChatMessage, TaskRole
from companybrain.llm import get_provider
from companybrain.config import settings
from companybrain.pipeline.class_node_extractor import ClassKnowledgeNode

log = structlog.get_logger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class CallSite:
    """A method call made from within the current method."""
    target_class: str       # e.g. "CompetitivenessRepository"
    target_method: str      # e.g. "findByPayerType"
    call_type: str          # service_call | repo_read | repo_write | event_emit | external_http
    arguments: list[str] = field(default_factory=list)


@dataclass
class MethodIntent:
    """
    Classified intent for a single Java method.
    Combines deterministic structural data with LLM-inferred semantics.
    """
    method_name: str
    class_name: str

    # Deterministic
    call_sites: list[CallSite] = field(default_factory=list)
    reads_tables: list[str] = field(default_factory=list)
    writes_tables: list[str] = field(default_factory=list)
    return_is_list: bool = False
    return_is_page: bool = False
    return_is_void: bool = False
    return_is_optional: bool = False
    is_transactional: bool = False
    is_transactional_read_only: bool = False
    is_async: bool = False

    # LLM-classified
    intent_label: str = ""
    business_description: str = ""
    side_effects: list[str] = field(default_factory=list)
    data_assumptions: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method_name": self.method_name,
            "class_name": self.class_name,
            "call_sites": [
                {"target_class": c.target_class, "target_method": c.target_method,
                 "call_type": c.call_type, "arguments": c.arguments}
                for c in self.call_sites
            ],
            "reads_tables": self.reads_tables,
            "writes_tables": self.writes_tables,
            "return_is_list": self.return_is_list,
            "return_is_page": self.return_is_page,
            "return_is_void": self.return_is_void,
            "return_is_optional": self.return_is_optional,
            "is_transactional": self.is_transactional,
            "is_transactional_read_only": self.is_transactional_read_only,
            "is_async": self.is_async,
            "intent_label": self.intent_label,
            "business_description": self.business_description,
            "side_effects": self.side_effects,
            "data_assumptions": self.data_assumptions,
            "gaps": self.gaps,
        }


# ── Constants ─────────────────────────────────────────────────────────────────

_CALL_RE  = re.compile(r'\b(\w+)\s*\.\s*(\w+)\s*\(')
_ASYNC_RE = re.compile(r'@(?:Async|Scheduled)\b')

_WRITE_METHODS = frozenset({
    "save", "saveAll", "saveAndFlush", "delete", "deleteAll", "deleteById",
    "update", "updateAll", "insert", "insertAll", "persist", "merge",
    "remove", "flush", "execute", "batchUpdate",
})

_EVENT_METHODS = frozenset({
    "publish", "publishEvent", "send", "sendMessage", "emit", "dispatch",
    "enqueue", "produceMessage", "sendTo",
})

_HTTP_CLIENTS = frozenset({
    "RestTemplate", "WebClient", "FeignClient", "HttpClient",
    "OkHttpClient", "HttpURLConnection",
})

_INTENT_SYSTEM = """You are a senior software engineer extracting business intent from code methods.

━━━ YOUR TASK ━━━
For EACH method listed, classify its intent and extract its business semantics.
You will receive: class context, call sites, table reads/writes, and method source.

━━━ OUTPUT FORMAT ━━━
Output ONLY valid JSON — no markdown, no explanation, no extra text:
{"methods": [
  {
    "method_name": "<exact method name>",
    "intent_label": "<label from table below>",
    "business_description": "<one precise sentence in plain English>",
    "side_effects": ["<observable effect beyond return value>"],
    "data_assumptions": ["<business fact the code assumes but doesn't enforce>"],
    "gaps": ["<question only a human can answer>"]
  }
]}

━━━ INTENT LABEL REFERENCE ━━━
Choose EXACTLY one. When two labels fit, choose the DOMINANT behavior.

  data_read    — Primary purpose is querying data. No mutations. Returns entity list,
                 page, single object, or Optional. Read-only transaction.
                 Examples: findByLob(), getCompetitorSummary(), searchPayers()

  data_write   — Primary purpose is persisting, updating, or deleting data.
                 May return saved entity or ID. @Transactional (not readOnly).
                 Examples: createCharge(), updateStatus(), deleteExpiredSessions()

  orchestration — Coordinates 2+ service/repository calls. Logic lies in sequencing
                 and routing, not in a single data operation.
                 Examples: processPayment(), handleCompetitorUpdate()

  validation   — Enforces business rules on input. Throws exception (or returns
                 error result) when constraints are violated.
                 Examples: validatePayerType(), assertMarketExists()

  side_effect  — Sends notification, publishes event, writes audit log, sends
                 email/SMS. The network call IS the purpose, not a side activity.
                 Examples: publishPricingEvent(), sendAlertEmail(), auditLog()

  cache_op     — Explicitly manages a cache (put/get/evict). @CacheEvict or
                 @Cacheable present, or direct CacheManager call.
                 Examples: evictCompetitorCache(), getCachedPricing()

  external_call — Makes an outbound HTTP/gRPC/queue call to a DIFFERENT SYSTEM
                 and uses the response in its own logic.
                 Examples: fetchPricingFromThirdParty(), callClaimsApi()

  mixed        — Clearly does TWO OR MORE of the above with equal weight.
                 Use sparingly — prefer the dominant label when one clearly wins.

━━━ FIELD GUIDANCE ━━━

business_description — ONE sentence, active voice, plain English, no class/method name
  repetition. Include WHAT the method does AND the domain context.
  BAD:  "This method gets the competitor data."
  GOOD: "Returns payer competitors filtered by line of business and provider type for
         the competitor analysis dashboard."

side_effects — Genuine observable side-effects only:
  Include: event publish, email/SMS, audit log, cache eviction, async task dispatch,
           external mutation call.
  Exclude: return value, debug logging, internal state changes.
  Empty array [] if none.

data_assumptions — Business constraints the code RELIES ON but does NOT enforce:
  - Enum values: "payerType is always one of: COMMERCIAL, MEDICARE, MEDICAID"
  - Cardinality: "every payer belongs to exactly one market"
  - Format: "competitorId is a UUID string, never null in production"
  Return [] if no hidden assumptions found.

gaps — Questions ONLY humans can answer (business rules, domain definitions):
  BAD:  "What does this method do?" (you just answered that)
  GOOD: "What defines 'active' competitor status — batch or event-driven?"
  Return [] if the code is self-explanatory.

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — data_read:
Context: CompetitorService.getPayerCompetitors(lob, providerTypes)
  call_sites: [payerRepository.findByLobAndStates], reads_tables: [competitors], return: List<PayerCompetitorDto>
Output:
{"methods": [{"method_name": "getPayerCompetitors", "intent_label": "data_read",
  "business_description": "Returns all payer competitors matching the given line of business and provider type filters for market analysis.",
  "side_effects": [],
  "data_assumptions": ["providerTypes empty list is treated as 'all types'", "lob is always a 2-letter code"],
  "gaps": ["Is there a maximum result size — could this return millions of rows?"]}]}

EXAMPLE 2 — mixed (write + side_effect):
Context: ChargeService.createCharge(ChargeRequest)
  call_sites: [chargeRepository.save, eventPublisher.publish], writes_tables: [charges], return: ChargeDto
Output:
{"methods": [{"method_name": "createCharge", "intent_label": "mixed",
  "business_description": "Persists a new charge record with PENDING status, then publishes a ChargeCreatedEvent so downstream billing consumers can begin processing.",
  "side_effects": ["Publishes ChargeCreatedEvent to the billing event bus"],
  "data_assumptions": ["amountCents must be positive; zero-amount charges are not handled"],
  "gaps": ["Is the event publish atomic with the DB write, or can we end up with an orphaned charge record?"]}]}

EXAMPLE 3 — orchestration:
Context: PaymentService.processPayment(PaymentRequest)
  call_sites: [fraudService.assess, chargeService.createCharge, notificationService.sendReceipt], transactional: true
Output:
{"methods": [{"method_name": "processPayment", "intent_label": "orchestration",
  "business_description": "Orchestrates payment processing by running fraud assessment, creating the charge, and dispatching a receipt notification in sequence.",
  "side_effects": ["Sends receipt email via notificationService"],
  "data_assumptions": ["Fraud assessment is synchronous; non-passing score throws FraudException"],
  "gaps": ["What happens to the charge if receipt dispatch fails — is it rolled back or left in PENDING?"]}]}

CRITICAL: Output ONLY the raw JSON object. Start with { end with }. No markdown, no preamble."""


# ── Main class ─────────────────────────────────────────────────────────────────

class IntentSynthesizer:
    """
    Enrich a ClassKnowledgeNode with method-level intent using one LLM call per class.
    """

    def __init__(self):
        self._provider = get_provider()

    async def classify(
        self,
        class_node: ClassKnowledgeNode,
        method_sources: dict[str, str],
        db_queries: Optional[list] = None,
    ) -> list[MethodIntent]:
        """
        Classify the intent of all public methods in a class.

        Step 1: Deterministic -- call-site extraction, table read/write detection.
        Step 2: LLM -- intent label, business description, gaps.
        """
        base_intents = self._deterministic_pass(class_node, method_sources, db_queries or [])
        if not base_intents:
            return []
        return await self._llm_pass(class_node, method_sources, base_intents)

    # ── Deterministic pass ────────────────────────────────────────────────────

    def _deterministic_pass(
        self,
        class_node: ClassKnowledgeNode,
        method_sources: dict[str, str],
        db_queries: list,
    ) -> list[MethodIntent]:
        field_map: dict[str, str] = {f.name: f.type for f in class_node.fields}
        intents: list[MethodIntent] = []

        for method in class_node.methods:
            if not method.is_public:
                continue

            source = method_sources.get(method.name, "")
            intent = MethodIntent(
                method_name=method.name,
                class_name=class_node.class_name,
                is_transactional=method.is_transactional,
                is_transactional_read_only=method.transaction_read_only,
                is_async=bool(_ASYNC_RE.search(" ".join(method.annotations))),
            )

            rt = method.return_type.lower()
            intent.return_is_void     = "void" in rt
            intent.return_is_list     = rt.startswith("list") or rt.startswith("collection") or "[]" in rt
            intent.return_is_page     = rt.startswith("page")
            intent.return_is_optional = rt.startswith("optional") or "mono" in rt

            if source:
                intent.call_sites = self._extract_call_sites(source, field_map)
                for q in db_queries:
                    if q.method == method.name or q.method == "":
                        if q.operation in ("INSERT", "UPDATE", "DELETE", "MERGE"):
                            intent.writes_tables.extend(q.tables)
                        else:
                            intent.reads_tables.extend(q.tables)
                intent.reads_tables  = list(dict.fromkeys(intent.reads_tables))
                intent.writes_tables = list(dict.fromkeys(intent.writes_tables))

            intents.append(intent)

        return intents

    def _extract_call_sites(self, source: str, field_map: dict[str, str]) -> list[CallSite]:
        sites: list[CallSite] = []
        seen: set[tuple[str, str]] = set()

        for m in _CALL_RE.finditer(source):
            obj, method = m.group(1), m.group(2)
            if obj not in field_map:
                continue
            dep_type = field_map[obj]
            key = (dep_type, method)
            if key in seen:
                continue
            seen.add(key)

            if method in _WRITE_METHODS:
                call_type = "repo_write"
            elif method in _EVENT_METHODS:
                call_type = "event_emit"
            elif dep_type in _HTTP_CLIENTS or dep_type.endswith("Client"):
                call_type = "external_http"
            elif dep_type.endswith(("Repository", "Repo", "DAO", "Mapper")):
                call_type = "repo_read"
            else:
                call_type = "service_call"

            sites.append(CallSite(target_class=dep_type, target_method=method, call_type=call_type))

        return sites

    # ── LLM pass ──────────────────────────────────────────────────────────────

    async def _llm_pass(
        self,
        class_node: ClassKnowledgeNode,
        method_sources: dict[str, str],
        base_intents: list[MethodIntent],
    ) -> list[MethodIntent]:
        class_summary = class_node.to_llm_summary()
        method_blocks: list[str] = []

        for intent in base_intents:
            src = method_sources.get(intent.method_name, "")
            if len(src) > 800:
                src = src[:800] + "\n    // ..."
            method_blocks.append(
                f"--- Method: {intent.method_name} ---\n"
                f"  call_sites: {[f'{c.target_class}.{c.target_method}' for c in intent.call_sites]}\n"
                f"  reads_tables: {intent.reads_tables}  writes_tables: {intent.writes_tables}\n"
                f"  transactional: {intent.is_transactional} readOnly={intent.is_transactional_read_only}\n"
                f"Source:\n{src}\n"
            )

        user_msg = (
            f"Class:\n{class_summary}\n\n"
            f"Classify these {len(base_intents)} methods:\n\n"
            + "\n".join(method_blocks)
            + "\n\nOutput only the JSON object."
        )

        messages = [
            ChatMessage(role="system", content=_INTENT_SYSTEM),
            ChatMessage(role="user",   content=user_msg),
        ]

        log.debug("IntentSynthesizer: LLM call",
                  class_name=class_node.class_name, methods=len(base_intents))

        try:
            response = await self._provider.chat(
                messages=messages,
                role=TaskRole.BALANCED,
                max_tokens=settings.max_tokens_intent_synthesis,
                temperature=0.0,
            )
            return self._merge(response.content, base_intents, class_node.class_name)
        except Exception as e:
            log.error("IntentSynthesizer: LLM call failed", error=str(e))
            for intent in base_intents:
                intent.intent_label = "unknown"
                intent.business_description = f"{intent.method_name} (classification failed)"
            return base_intents

    def _merge(
        self,
        llm_text: str,
        base_intents: list[MethodIntent],
        class_name: str,
    ) -> list[MethodIntent]:
        import json
        text = llm_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            m = re.search(r'\{[\s\S]*\}', text)
            data = {}
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    pass

        llm_by_name: dict[str, dict] = {
            item.get("method_name", ""): item for item in data.get("methods", [])
        }

        for intent in base_intents:
            llm = llm_by_name.get(intent.method_name, {})
            intent.intent_label         = llm.get("intent_label", "unknown")
            intent.business_description = llm.get("business_description", f"{intent.method_name} in {class_name}")
            intent.side_effects         = llm.get("side_effects", [])
            intent.data_assumptions     = llm.get("data_assumptions", [])
            intent.gaps                 = llm.get("gaps", [])

        classified = len([i for i in base_intents if i.intent_label != "unknown"])
        log.info("IntentSynthesizer: done", class_name=class_name, classified=classified)
        return base_intents


# ── FunctionContext — orchestrator-facing type ─────────────────────────────────
#
# The orchestrator calls synthesise_all(entities, focal_context) and expects
# back a dict[external_id, FunctionContext].  This is a lightweight container
# that maps generic Entity objects to a business-context summary without
# requiring a second LLM call (the entity extractor already produced descriptions).

@dataclass
class FunctionContext:
    """
    Business-context summary for a single graph entity (function / service / endpoint).

    Built from entity-extractor output, not from raw source, so no extra LLM call.
    Stored in Java node metadata under "functionContext" for T2 context assembly.
    """
    entity_id: str          # external_id of the Entity
    name: str               # entity name
    entity_type: str        # Function | Service | ApiEndpoint | ...
    purpose: str            # one-sentence business description (from entity.description)
    intent_label: str       # data_read | data_write | orchestration | ...
    change_risk: str        # high | medium | low  (heuristic)
    reads_tables: list[str] = field(default_factory=list)
    writes_tables: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


def function_context_to_dict(ctx: FunctionContext) -> dict:
    """Serialize FunctionContext → plain dict for JSON transport to Java."""
    return {
        "entityId":    ctx.entity_id,
        "name":        ctx.name,
        "entityType":  ctx.entity_type,
        "purpose":     ctx.purpose,
        "intentLabel": ctx.intent_label,
        "changeRisk":  ctx.change_risk,
        "readsTables": ctx.reads_tables,
        "writesTables": ctx.writes_tables,
        "sideEffects": ctx.side_effects,
        "gaps":        ctx.gaps,
    }


# ── synthesise_all — orchestrator entry point ──────────────────────────────────

async def _synthesise_all(entities, focal_context) -> dict:
    """
    Convert entity-extractor output into FunctionContext objects for the orchestrator.

    Strategy (no extra LLM call):
      - Targets entities of type Function, CodeFunction, ApiEndpoint, Service.
      - Maps entity.description → FunctionContext.purpose.
      - Derives intent_label and change_risk from entity_type + name heuristics.
      - Returns dict[external_id, FunctionContext].
    """
    _WRITE_LABELS = frozenset({
        "save", "create", "update", "delete", "remove", "persist",
        "insert", "upsert", "post", "put", "patch",
    })
    _READ_LABELS = frozenset({
        "get", "find", "fetch", "load", "query", "search", "list",
        "retrieve", "lookup", "read",
    })
    _HIGH_RISK_TYPES = frozenset({"Service", "Repository", "ApiEndpoint"})
    _TARGET_TYPES = frozenset({"Function", "CodeFunction", "ApiEndpoint", "Service", "Component"})

    result: dict = {}

    for entity in entities:
        if entity.entity_type not in _TARGET_TYPES:
            continue

        name_lower = entity.name.lower()

        # Intent label heuristic from method name prefix
        if any(name_lower.startswith(w) for w in _WRITE_LABELS):
            intent_label = "data_write"
        elif any(name_lower.startswith(r) for r in _READ_LABELS):
            intent_label = "data_read"
        elif entity.entity_type == "ApiEndpoint":
            intent_label = "orchestration"
        else:
            intent_label = "mixed"

        # Change risk: high for public API surfaces and shared services
        if entity.entity_type in _HIGH_RISK_TYPES:
            change_risk = "high"
        elif any(w in name_lower for w in ("util", "helper", "mapper", "converter")):
            change_risk = "low"
        else:
            change_risk = "medium"

        # Purpose from description (entity extractor already set this)
        purpose = (
            getattr(entity, "description", None)
            or getattr(entity, "code_snippet", "")[:120]
            or f"{entity.entity_type} {entity.name}"
        )

        ctx = FunctionContext(
            entity_id=entity.external_id,
            name=entity.name,
            entity_type=entity.entity_type,
            purpose=purpose,
            intent_label=intent_label,
            change_risk=change_risk,
        )
        result[entity.external_id] = ctx

    log.info("synthesise_all: built FunctionContexts",
             total_entities=len(entities), targeted=len(result))
    return result


# Attach as a method on IntentSynthesizer so orchestrator can call it via instance
IntentSynthesizer.synthesise_all = staticmethod(_synthesise_all)
