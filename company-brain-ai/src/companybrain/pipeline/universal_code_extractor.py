"""
UniversalCodeExtractor — Language-agnostic structured knowledge extraction.

DESIGN PRINCIPLE:
  The LLM is the parser. We do NOT write language-specific regex for understanding
  code structure. The LLM already knows Java, Python, TypeScript, Go, Rust, Kotlin,
  C#, Ruby, etc. — we just give it the source and ask for a universal schema.

  The ONLY language-specific code in this module is:
    - file extension detection (which language is this file?)
    - that's it.

WHAT WE EXTRACT (language-agnostic schema):
  For any source file, in any language, we extract:

    module_name       — class / module / file name
    module_type       — controller | service | repository | model | utility | config | test
    language          — java | python | typescript | go | kotlin | ...
    description       — one sentence: what does this module do?

    endpoints []      — HTTP routes exposed by this module
      ↳ http_method, path, handler, parameters[], response_type

    dependencies []   — what other modules/services this depends on
      ↳ name, dep_type (service|repo|client|cache|queue|db), injection_style

    functions []      — public functions/methods with their intent
      ↳ name, intent_label, description, data_reads[], data_writes[], side_effects[]

    parameters []     — API parameters with business semantics
      ↳ name, kind (path|query|body|header), type, required, valid_values,
         is_multiselect, purpose, business_rules[]

    data_contracts [] — DTOs / request-response types
      ↳ name, fields[]

    db_queries []     — database queries (SQL, ORM, query builder)
      ↳ method, query_text, operation, tables[], is_native

    gaps []           — questions the code cannot answer
      ↳ {"question": "What does payerType='ALL' mean?", "parameter": "payerType"}

This schema is stored in Node.metadata and feeds:
  - Stage 3 ContextSynthesizer (business context)
  - Stage 4 GapDetector (surfacing unanswerable questions)
  - The knowledge graph dependency edges
  - The VS Code extension hover card

USAGE:
    extractor = UniversalCodeExtractor()

    # Extract from one file
    result = await extractor.extract_file(
        file_path="/path/to/CompetitivenessService.java",
        endpoint_context="/api/v1/mcheck/niq/competitiveness/summary/competitors/payer",
    )

    # Extract from multiple files (the assembled chain)
    results = await extractor.extract_chain(
        files=[controller_file, service_file, repository_file],
        endpoint="/api/v1/...",
    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.llm.base import ChatMessage, TaskRole
from companybrain.llm import get_provider
from companybrain.config import settings

log = structlog.get_logger(__name__)


# ── Universal knowledge schema ─────────────────────────────────────────────────

@dataclass
class ParameterKnowledge:
    name: str
    kind: str = "query"          # path | query | body | header | matrix
    type: str = ""               # language type string (String, str, number, ...)
    required: bool = True
    default_value: Optional[str] = None
    purpose: str = ""            # business-language description
    is_multiselect: bool = False # accepts list / comma-separated values
    valid_values: list[str] = field(default_factory=list)
    business_rules: list[str] = field(default_factory=list)
    data_type_hint: str = ""     # "integer ID" | "enum" | "date" | "UUID" | ...


@dataclass
class EndpointKnowledge:
    http_method: str = "GET"
    path: str = ""
    handler: str = ""            # function/method name
    description: str = ""
    parameters: list[ParameterKnowledge] = field(default_factory=list)
    response_type: str = ""
    response_description: str = ""
    auth_required: bool = False
    auth_description: str = ""


@dataclass
class DependencyKnowledge:
    name: str                    # class/module/service name
    dep_type: str = "service"    # service | repository | client | cache | queue | database | utility
    how_used: str = ""           # one sentence: what does this dependency do here?
    injection_style: str = ""    # constructor | field | function_arg | import | ...


@dataclass
class FunctionKnowledge:
    name: str
    intent_label: str = "unknown"  # data_read | data_write | orchestration | side_effect | validation | ...
    description: str = ""
    data_reads: list[str] = field(default_factory=list)    # table/collection/API names
    data_writes: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)  # emails, events, queues, ...
    data_assumptions: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)    # param type names


@dataclass
class DBQueryKnowledge:
    method: str = ""
    query_text: str = ""
    query_type: str = "sql"      # sql | jpql | orm_derived | jooq | prisma | mongoose | ...
    operation: str = "SELECT"    # SELECT | INSERT | UPDATE | DELETE
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    is_native: bool = False


@dataclass
class FileKnowledge:
    """
    Universal knowledge extracted from one source file.
    Language-agnostic: works for Java, Python, TypeScript, Go, etc.
    """
    # Identity
    file_path: str
    language: str                 # java | python | typescript | go | kotlin | ...
    module_name: str              # class/module name
    module_type: str              # controller | service | repository | model | ...

    # What it does
    description: str = ""
    endpoints: list[EndpointKnowledge] = field(default_factory=list)
    dependencies: list[DependencyKnowledge] = field(default_factory=list)
    functions: list[FunctionKnowledge] = field(default_factory=list)
    db_queries: list[DBQueryKnowledge] = field(default_factory=list)
    parameters: list[ParameterKnowledge] = field(default_factory=list)  # endpoint params

    # Gap tracking
    gaps: list[dict] = field(default_factory=list)  # [{"question": "...", "context": "..."}]

    def to_metadata(self) -> dict:
        """Serialise for Node.metadata storage."""
        return {
            "language": self.language,
            "module_name": self.module_name,
            "module_type": self.module_type,
            "description": self.description,
            "endpoints": [_endpoint_to_dict(e) for e in self.endpoints],
            "dependencies": [_dep_to_dict(d) for d in self.dependencies],
            "functions": [_fn_to_dict(f) for f in self.functions],
            "db_queries": [_query_to_dict(q) for q in self.db_queries],
            "parameters": [_param_to_dict(p) for p in self.parameters],
            "gaps": self.gaps,
        }

    def to_llm_summary(self) -> str:
        """Compact summary for downstream LLM context (Stage 3)."""
        lines = [
            f"File: {Path(self.file_path).name}  [{self.language} {self.module_type}]",
            f"Module: {self.module_name}",
        ]
        if self.description:
            lines.append(f"Purpose: {self.description}")
        if self.endpoints:
            for ep in self.endpoints:
                params_str = ", ".join(
                    f"{p.name}{'?' if not p.required else ''}"
                    for p in ep.parameters
                )
                lines.append(f"  Endpoint: {ep.http_method} {ep.path}({params_str}) → {ep.response_type}")
        if self.dependencies:
            lines.append(f"Depends on: {', '.join(d.name for d in self.dependencies)}")
        if self.functions:
            for fn in self.functions:
                lines.append(f"  {fn.intent_label}: {fn.name} — {fn.description}")
        if self.db_queries:
            for q in self.db_queries:
                lines.append(f"  DB [{q.operation}]: {q.query_text[:120]}")
                if q.tables:
                    lines.append(f"    tables: {', '.join(q.tables)}")
        if self.gaps:
            lines.append("Open questions:")
            for g in self.gaps[:3]:
                lines.append(f"  ? {g.get('question', '')}")
        return "\n".join(lines)


# ── LLM extraction prompt ─────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You are extracting structured knowledge from source code.

The code may be in ANY language: Java, Python, TypeScript, Go, Kotlin, C#, Ruby, etc.
Do NOT assume any specific framework. Read what is actually there.

Extract the following JSON schema. Leave arrays empty [] if nothing applies.
Use plain English for all description fields — no jargon, no type names.

{
  "language": "java|python|typescript|go|kotlin|csharp|ruby|other",
  "module_name": "exact class or module name",
  "module_type": "controller|service|repository|model|utility|config|test|middleware|unknown",
  "description": "One sentence: what does this module do in the system?",

  "endpoints": [
    {
      "http_method": "GET|POST|PUT|DELETE|PATCH",
      "path": "/exact/path",
      "handler": "function or method name",
      "description": "what this endpoint does",
      "parameters": [
        {
          "name": "paramName",
          "kind": "path|query|body|header",
          "type": "language type",
          "required": true,
          "default_value": null,
          "purpose": "What this parameter controls in plain English",
          "is_multiselect": false,
          "valid_values": [],
          "business_rules": [],
          "data_type_hint": "integer ID|enum|date|UUID|boolean flag|free text|comma-separated list"
        }
      ],
      "response_type": "return type",
      "response_description": "what the response contains",
      "auth_required": false,
      "auth_description": ""
    }
  ],

  "dependencies": [
    {
      "name": "DependencyClassName",
      "dep_type": "service|repository|client|cache|queue|database|utility",
      "how_used": "One sentence: what does this dependency do here?",
      "injection_style": "constructor|field|function_arg|import|singleton"
    }
  ],

  "functions": [
    {
      "name": "methodName",
      "intent_label": "data_read|data_write|orchestration|side_effect|validation|cache_op|external_call|mixed",
      "description": "One sentence in plain English",
      "data_reads": ["TABLE_NAME or API name"],
      "data_writes": ["TABLE_NAME or API name"],
      "side_effects": ["e.g. Sends email", "Publishes event to queue"],
      "data_assumptions": ["e.g. payerType is always non-null"],
      "parameters": ["param types"]
    }
  ],

  "db_queries": [
    {
      "method": "enclosing function name",
      "query_text": "SQL, JPQL, ORM method name, or DSL summary",
      "query_type": "sql|jpql|orm_derived|jooq|prisma|mongoose|sqlalchemy|other",
      "operation": "SELECT|INSERT|UPDATE|DELETE",
      "tables": ["TABLE_NAMES"],
      "columns": ["TABLE.COLUMN"],
      "is_native": false
    }
  ],

  "gaps": [
    {
      "question": "What does payerType=ALL mean in the business context?",
      "context": "The parameter has no comment and defaults to ALL"
    }
  ]
}

Rules:
- Extract ONLY what is in the source. Do not invent values.
- For parameters: detect multiselect if the type is List/Array, or the name contains 'Ids'/'Types'/'List'/'Filter'.
- For valid_values: look for enum types, switch/if checks against string constants, or comments.
- For gaps: flag anything a new developer couldn't understand without external knowledge.
- Output ONLY the JSON object. No markdown, no explanation."""


# ── Main extractor ─────────────────────────────────────────────────────────────

class UniversalCodeExtractor:
    """
    Language-agnostic knowledge extractor using LLM as the parser.

    One LLM call per file (or per small batch). The LLM understands the language,
    framework, and patterns — we don't hardcode any of that.

    Works with: Java (Spring, Quarkus, Micronaut), Python (FastAPI, Django, Flask),
    TypeScript (NestJS, Express, Next.js), Go (Gin, Echo), Kotlin, C#, Ruby, etc.
    """

    def __init__(self):
        self._provider = get_provider()

    async def extract_file(
        self,
        file_path: str,
        endpoint_context: str = "",
        extra_context: str = "",
    ) -> Optional[FileKnowledge]:
        """
        Extract structured knowledge from one source file.

        file_path:        Absolute path to the source file.
        endpoint_context: The API endpoint being analyzed (helps focus extraction).
        extra_context:    Any additional context (related file summaries, annotations).
        """
        try:
            content = Path(file_path).read_text(errors="ignore")
        except Exception as e:
            log.warning("UniversalCodeExtractor: cannot read file", file=file_path, error=str(e))
            return None

        language = _detect_language(file_path)

        # Cap content at 6000 chars — enough for most files
        if len(content) > 6000:
            content = content[:6000] + "\n// ... (file truncated)"

        context_hint = ""
        if endpoint_context:
            context_hint = f"\nContext: This file is part of the call chain for endpoint: {endpoint_context}"
        if extra_context:
            context_hint += f"\n{extra_context}"

        user_msg = (
            f"Language: {language}\n"
            f"File: {Path(file_path).name}{context_hint}\n\n"
            f"```{language}\n{content}\n```\n\n"
            f"Extract the structured knowledge schema."
        )

        messages = [
            ChatMessage(role="system", content=_EXTRACT_SYSTEM),
            ChatMessage(role="user",   content=user_msg),
        ]

        log.debug(
            "UniversalCodeExtractor: extracting",
            file=Path(file_path).name,
            language=language,
            content_chars=len(content),
        )

        try:
            response = await self._provider.chat(
                messages=messages,
                role=TaskRole.BALANCED,
                max_tokens=settings.max_tokens_entity_extraction,
                temperature=0.0,
            )
            return self._parse(response.content, file_path, language)
        except Exception as e:
            log.error("UniversalCodeExtractor: LLM call failed",
                      file=file_path, error=str(e))
            return None

    async def extract_chain(
        self,
        files: list[str],
        endpoint: str = "",
        max_concurrent: int = 3,
    ) -> list[FileKnowledge]:
        """
        Extract knowledge from all files in a call chain.
        Runs files concurrently (up to max_concurrent at once).
        """
        import asyncio

        results: list[FileKnowledge] = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_one(fp: str) -> Optional[FileKnowledge]:
            async with semaphore:
                return await self.extract_file(fp, endpoint_context=endpoint)

        tasks = [extract_one(fp) for fp in files]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        for r in raw:
            if isinstance(r, FileKnowledge):
                results.append(r)
            elif isinstance(r, Exception):
                log.warning("UniversalCodeExtractor: extraction error", error=str(r))

        log.info(
            "UniversalCodeExtractor: chain complete",
            files=len(files),
            extracted=len(results),
            endpoint=endpoint,
        )
        return results

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse(self, llm_text: str, file_path: str, language: str) -> FileKnowledge:
        """Parse LLM JSON output into a FileKnowledge object."""
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

        if not data:
            log.warning("UniversalCodeExtractor: parse failed", file=file_path,
                        preview=llm_text[:200])
            return FileKnowledge(
                file_path=file_path,
                language=language,
                module_name=Path(file_path).stem,
                module_type="unknown",
            )

        # Parse endpoints
        endpoints = []
        for ep in data.get("endpoints", []):
            params = [
                ParameterKnowledge(
                    name=p.get("name", ""),
                    kind=p.get("kind", "query"),
                    type=p.get("type", ""),
                    required=bool(p.get("required", True)),
                    default_value=p.get("default_value"),
                    purpose=p.get("purpose", ""),
                    is_multiselect=bool(p.get("is_multiselect", False)),
                    valid_values=p.get("valid_values", []),
                    business_rules=p.get("business_rules", []),
                    data_type_hint=p.get("data_type_hint", ""),
                )
                for p in ep.get("parameters", [])
            ]
            endpoints.append(EndpointKnowledge(
                http_method=ep.get("http_method", "GET").upper(),
                path=ep.get("path", ""),
                handler=ep.get("handler", ""),
                description=ep.get("description", ""),
                parameters=params,
                response_type=ep.get("response_type", ""),
                response_description=ep.get("response_description", ""),
                auth_required=bool(ep.get("auth_required", False)),
                auth_description=ep.get("auth_description", ""),
            ))

        # Parse dependencies
        dependencies = [
            DependencyKnowledge(
                name=d.get("name", ""),
                dep_type=d.get("dep_type", "service"),
                how_used=d.get("how_used", ""),
                injection_style=d.get("injection_style", ""),
            )
            for d in data.get("dependencies", [])
        ]

        # Parse functions
        functions = [
            FunctionKnowledge(
                name=f.get("name", ""),
                intent_label=f.get("intent_label", "unknown"),
                description=f.get("description", ""),
                data_reads=f.get("data_reads", []),
                data_writes=f.get("data_writes", []),
                side_effects=f.get("side_effects", []),
                data_assumptions=f.get("data_assumptions", []),
                parameters=f.get("parameters", []),
            )
            for f in data.get("functions", [])
        ]

        # Parse DB queries
        db_queries = [
            DBQueryKnowledge(
                method=q.get("method", ""),
                query_text=q.get("query_text", ""),
                query_type=q.get("query_type", "sql"),
                operation=q.get("operation", "SELECT").upper(),
                tables=q.get("tables", []),
                columns=q.get("columns", []),
                is_native=bool(q.get("is_native", False)),
            )
            for q in data.get("db_queries", [])
        ]

        result = FileKnowledge(
            file_path=file_path,
            language=data.get("language", language),
            module_name=data.get("module_name", Path(file_path).stem),
            module_type=data.get("module_type", "unknown"),
            description=data.get("description", ""),
            endpoints=endpoints,
            dependencies=dependencies,
            functions=functions,
            db_queries=db_queries,
            gaps=data.get("gaps", []),
        )

        # Flatten endpoint parameters to top-level for convenience
        for ep in result.endpoints:
            result.parameters.extend(ep.parameters)

        log.info(
            "UniversalCodeExtractor: parsed",
            file=Path(file_path).name,
            language=result.language,
            module_type=result.module_type,
            endpoints=len(result.endpoints),
            dependencies=len(result.dependencies),
            functions=len(result.functions),
            db_queries=len(result.db_queries),
            gaps=len(result.gaps),
        )
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension only."""
    ext = Path(file_path).suffix.lower()
    return {
        ".java":  "java",
        ".kt":    "kotlin",
        ".py":    "python",
        ".ts":    "typescript",
        ".tsx":   "typescript",
        ".js":    "javascript",
        ".jsx":   "javascript",
        ".go":    "go",
        ".cs":    "csharp",
        ".rb":    "ruby",
        ".rs":    "rust",
        ".php":   "php",
        ".scala": "scala",
    }.get(ext, "unknown")


def _endpoint_to_dict(e: EndpointKnowledge) -> dict:
    return {
        "http_method": e.http_method, "path": e.path, "handler": e.handler,
        "description": e.description, "response_type": e.response_type,
        "response_description": e.response_description,
        "auth_required": e.auth_required, "auth_description": e.auth_description,
        "parameters": [_param_to_dict(p) for p in e.parameters],
    }


def _dep_to_dict(d: DependencyKnowledge) -> dict:
    return {
        "name": d.name, "dep_type": d.dep_type,
        "how_used": d.how_used, "injection_style": d.injection_style,
    }


def _fn_to_dict(f: FunctionKnowledge) -> dict:
    return {
        "name": f.name, "intent_label": f.intent_label,
        "description": f.description, "data_reads": f.data_reads,
        "data_writes": f.data_writes, "side_effects": f.side_effects,
        "data_assumptions": f.data_assumptions, "parameters": f.parameters,
    }


def _query_to_dict(q: DBQueryKnowledge) -> dict:
    return {
        "method": q.method, "query_text": q.query_text, "query_type": q.query_type,
        "operation": q.operation, "tables": q.tables, "columns": q.columns,
        "is_native": q.is_native,
    }


def _param_to_dict(p: ParameterKnowledge) -> dict:
    return {
        "name": p.name, "kind": p.kind, "type": p.type,
        "required": p.required, "default_value": p.default_value,
        "purpose": p.purpose, "is_multiselect": p.is_multiselect,
        "valid_values": p.valid_values, "business_rules": p.business_rules,
        "data_type_hint": p.data_type_hint,
    }
