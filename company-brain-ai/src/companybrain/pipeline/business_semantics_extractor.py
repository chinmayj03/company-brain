"""
BusinessSemanticsExtractor — Task #40: Business semantics for API parameters.

Takes the deterministic APIContract (Task #39) and enriches each parameter
with LLM-inferred business semantics:

  • Parameter purpose in plain English ("Which market to filter by")
  • Multiselect detection ("comma-separated list of payer types" → multiselect=True)
  • Valid values / enum semantics ("One of: COMMERCIAL, MEDICARE, MEDICAID")
  • Business rules ("Required when reportType=DETAILED")
  • Annotations from human corrections (highest signal, overrides LLM)

One LLM call covers all parameters in one endpoint. The prompt is structured
so the LLM output is machine-parseable JSON — no freeform prose.

This is the last pure-LLM extraction step in the pipeline. After this,
all remaining gaps go to Stage 4 (GapDetector) and human annotation.

Output stored in Node.metadata["parameter_semantics"]:
{
  "marketId": {
    "purpose": "Identifies the geographic market to filter results by",
    "is_multiselect": false,
    "valid_values": [],
    "business_rules": ["Required — no default"],
    "data_type_hint": "integer ID"
  },
  "payerType": {
    "purpose": "Insurance payer category for competitor analysis",
    "is_multiselect": true,
    "valid_values": ["COMMERCIAL", "MEDICARE", "MEDICAID", "MANAGED_MEDICAID"],
    "business_rules": ["Defaults to ALL if not specified"],
    "data_type_hint": "enum"
  }
}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

from companybrain.llm.base import ChatMessage, TaskRole
from companybrain.llm import get_provider
from companybrain.config import settings
from companybrain.pipeline.api_contract_extractor import APIContract, ParamDef

log = structlog.get_logger(__name__)


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ParameterSemantics:
    """Business semantics for one API parameter."""
    name: str
    purpose: str = ""                        # plain-English description
    is_multiselect: bool = False             # accepts comma-separated / list input
    valid_values: list[str] = field(default_factory=list)   # known valid enum values
    business_rules: list[str] = field(default_factory=list) # conditional rules
    data_type_hint: str = ""                 # "integer ID" | "enum" | "date (YYYY-MM-DD)" | ...
    gaps: list[str] = field(default_factory=list)           # still unknown after LLM


@dataclass
class EndpointSemantics:
    """Full semantics for all parameters of one endpoint."""
    endpoint: str
    http_method: str
    endpoint_purpose: str = ""              # one-sentence endpoint description
    parameters: dict[str, ParameterSemantics] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "http_method": self.http_method,
            "endpoint_purpose": self.endpoint_purpose,
            "parameters": {
                name: {
                    "purpose": p.purpose,
                    "is_multiselect": p.is_multiselect,
                    "valid_values": p.valid_values,
                    "business_rules": p.business_rules,
                    "data_type_hint": p.data_type_hint,
                    "gaps": p.gaps,
                }
                for name, p in self.parameters.items()
            },
        }

    def gaps_summary(self) -> list[dict]:
        """Return all unanswered questions for GapDetector."""
        gaps = []
        for name, sem in self.parameters.items():
            for gap in sem.gaps:
                gaps.append({"parameter": name, "question": gap})
        return gaps


# ── LLM prompt ────────────────────────────────────────────────────────────────

_SEMANTICS_SYSTEM = """You are extracting business semantics from an API endpoint definition.

You will be given:
  - The endpoint path and HTTP method
  - All parameters (query params, path params, request body fields)
  - The Java types, default values, and validation annotations
  - Repository/DB queries the endpoint uses (if available)
  - Any human annotations already provided

Extract for each parameter:
  "purpose": One sentence describing what this parameter controls in business terms.
             Use plain language — no Java jargon, no type names.
  "is_multiselect": true if this parameter can accept multiple values
                    (comma-separated string, List type, array, or named '...Ids', '...Types', '...List').
                    false otherwise.
  "valid_values": Array of known valid enum values (strings).
                  Leave empty [] if the parameter is a free-form ID or date.
                  Include values you can infer from the Java type name, DB queries, or parameter name.
  "business_rules": Array of conditional rules or constraints, e.g.:
                    ["Required when reportType=DETAILED", "Defaults to ALL when omitted"]
                    Leave empty [] if none apparent.
  "data_type_hint": One of: "integer ID" | "string ID" | "UUID" | "enum" | "date (YYYY-MM-DD)" |
                    "boolean flag" | "free text" | "numeric value" | "comma-separated list" | "JSON object"
  "gaps": Array of questions you cannot answer from the code alone, e.g.:
          ["What is the business meaning of a 'payer'?", "What does marketId=0 signify?"]

Also produce:
  "endpoint_purpose": One sentence describing what this endpoint does.

Output ONLY valid JSON:
{
  "endpoint_purpose": "...",
  "parameters": {
    "paramName": {"purpose": "...", "is_multiselect": false, "valid_values": [], "business_rules": [], "data_type_hint": "...", "gaps": []},
    ...
  }
}"""


# ── Main class ─────────────────────────────────────────────────────────────────

class BusinessSemanticsExtractor:
    """
    Enrich an APIContract with LLM-inferred business semantics.

    One LLM call covers the entire endpoint (all parameters at once).
    Human annotations (if any) are injected as highest-priority context.

    Usage::

        extractor = BusinessSemanticsExtractor()
        semantics = await extractor.extract(
            contract=api_contract,
            db_queries=db_query_list,          # from extract_db_queries()
            human_annotations=existing_anns,   # from NodeContext table
        )
        node.metadata["parameter_semantics"] = semantics.to_dict()
        # Surface gaps to GapDetector
        for gap in semantics.gaps_summary():
            gap_detector.add(gap)
    """

    def __init__(self):
        self._provider = get_provider()

    async def extract(
        self,
        contract: APIContract,
        db_queries: Optional[list[dict]] = None,
        human_annotations: Optional[list[dict]] = None,
    ) -> EndpointSemantics:
        """
        Extract business semantics for all parameters in the endpoint.
        """
        # Build prompt context
        all_params = contract.path_params + contract.query_params + contract.headers
        if not all_params and not contract.request_body_fields:
            # No parameters at all — just extract endpoint purpose
            return EndpointSemantics(
                endpoint=contract.path,
                http_method=contract.http_method,
                endpoint_purpose=f"{contract.http_method} {contract.path}",
                parameters={},
            )

        user_msg = self._build_prompt(contract, db_queries or [], human_annotations or [])

        messages = [
            ChatMessage(role="system", content=_SEMANTICS_SYSTEM),
            ChatMessage(role="user",   content=user_msg),
        ]

        log.debug(
            "BusinessSemanticsExtractor: LLM call",
            endpoint=contract.path,
            params=len(all_params) + len(contract.request_body_fields),
        )

        try:
            response = await self._provider.chat(
                messages=messages,
                role=TaskRole.BALANCED,
                max_tokens=settings.max_tokens_entity_extraction,
                temperature=0.0,
            )
            return self._parse_response(response.content, contract)
        except Exception as e:
            log.error("BusinessSemanticsExtractor: LLM call failed",
                      endpoint=contract.path, error=str(e))
            return self._fallback(contract)

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        contract: APIContract,
        db_queries: list[dict],
        human_annotations: list[dict],
    ) -> str:
        lines: list[str] = []

        lines.append(f"Endpoint: {contract.http_method} {contract.path}")
        lines.append(f"Handler: {contract.handler_class}.{contract.handler_method}")
        if contract.security:
            lines.append(f"Security: {', '.join(contract.security)}")

        # Parameters
        lines.append("\nParameters:")
        for p in contract.path_params:
            lines.append(f"  [PATH] {p.name}: {p.java_type}  (always required)")
        for p in contract.query_params:
            req = "required" if p.required else f"optional, default={p.default_value or 'none'}"
            lines.append(f"  [QUERY] {p.name}: {p.java_type}  ({req})")
        for p in contract.headers:
            lines.append(f"  [HEADER] {p.name}: {p.java_type}")
        if contract.request_body:
            lines.append(f"  [BODY] {contract.request_body}:")
            for f in contract.request_body_fields:
                req = "required" if f.required else "optional"
                lines.append(f"    {f.name}: {f.java_type}  ({req})")

        # Response type
        lines.append(f"\nResponse type: {contract.response_type}")
        if contract.response_dto:
            lines.append(f"Response DTO: {contract.response_dto}")

        # DB queries (strong signal for valid values and table names)
        if db_queries:
            lines.append("\nDatabase queries executed by this endpoint:")
            for q in db_queries[:6]:
                lines.append(f"  [{q.get('type', '?')}] {q.get('query', '')[:200]}")
                if q.get('tables'):
                    lines.append(f"    tables: {', '.join(q['tables'])}")

        # Human annotations (override any LLM inference)
        if human_annotations:
            lines.append("\nHuman annotations (treat as ground truth):")
            for ann in human_annotations[:5]:
                lines.append(f"  [{ann.get('annotation_type', 'note')}] {ann.get('text', '')[:200]}")

        return "\n".join(lines)

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse_response(self, llm_text: str, contract: APIContract) -> EndpointSemantics:
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

        endpoint_purpose = data.get("endpoint_purpose", "")
        raw_params = data.get("parameters", {})

        semantics = EndpointSemantics(
            endpoint=contract.path,
            http_method=contract.http_method,
            endpoint_purpose=endpoint_purpose,
        )

        all_params = (
            contract.path_params + contract.query_params +
            contract.headers +
            [_dto_field_to_param(f) for f in contract.request_body_fields]
        )

        for p in all_params:
            raw = raw_params.get(p.name, {})
            semantics.parameters[p.name] = ParameterSemantics(
                name=p.name,
                purpose=raw.get("purpose", ""),
                is_multiselect=bool(raw.get("is_multiselect", False)),
                valid_values=raw.get("valid_values", []),
                business_rules=raw.get("business_rules", []),
                data_type_hint=raw.get("data_type_hint", ""),
                gaps=raw.get("gaps", []),
            )

        log.info(
            "BusinessSemanticsExtractor: extracted",
            endpoint=contract.path,
            params=len(semantics.parameters),
            multiselect=[n for n, s in semantics.parameters.items() if s.is_multiselect],
        )
        return semantics

    def _fallback(self, contract: APIContract) -> EndpointSemantics:
        """Return minimal semantics when LLM call fails."""
        sem = EndpointSemantics(
            endpoint=contract.path,
            http_method=contract.http_method,
            endpoint_purpose=f"{contract.http_method} {contract.path}",
        )
        all_params = contract.path_params + contract.query_params + contract.headers
        for p in all_params:
            sem.parameters[p.name] = ParameterSemantics(
                name=p.name,
                purpose=f"{p.name} parameter",
                data_type_hint=_infer_type_hint(p.java_type),
                gaps=[f"What is the business meaning of '{p.name}'?"],
            )
        return sem


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dto_field_to_param(f) -> "ParamDef":
    """Convert a DtoField to a ParamDef for uniform processing."""
    from companybrain.pipeline.api_contract_extractor import ParamDef
    return ParamDef(
        kind="body",
        name=f.name,
        java_name=f.name,
        java_type=f.java_type,
        required=f.required,
    )


def _infer_type_hint(java_type: str) -> str:
    t = java_type.lower().replace(" ", "")
    if t in ("long", "integer", "int"):
        return "integer ID"
    if t in ("string",):
        return "free text"
    if t in ("boolean",):
        return "boolean flag"
    if t.startswith("list") or t.endswith("[]"):
        return "comma-separated list"
    if "date" in t:
        return "date (YYYY-MM-DD)"
    if "uuid" in t:
        return "UUID"
    return "string"
