"""
ClassNodeExtractor — Task #36: Class-level knowledge node extraction.

Extracts rich, structured knowledge from a single Java class file:

  STRUCTURAL (deterministic, zero LLM):
    • @Autowired / constructor-injected fields → dependency edges
    • All method signatures with annotations
    • Reverse call edges: which classes call THIS class (from the assembled chain)
    • Spring stereotype: @Service / @Repository / @RestController / @Component
    • Database query metadata (from @Query, jOOQ chains, JdbcTemplate)

  RELATIONAL:
    • Builds a ClassKnowledgeNode that feeds into the graph DB as a Node
    • Emits dependency edges (this_class → dep_class) for the dependency graph

This extractor is called by PipelineService after NavigatorAgent assembles the chain.
It does NOT replace EntityExtractor — it AUGMENTS it with structured metadata that
the LLM extraction alone tends to miss (exact field names, exact @Query SQL, etc.).

Graph node produced:
  Node.nodeType = "JavaClass"
  Node.name = "CompetitivenessService"
  Node.metadata = {
    "stereotype": "@Service",
    "package": "com.example.competitiveness.service",
    "file": "src/main/java/.../CompetitivenessService.java",
    "fields": [{"name": "competitivenessRepository", "type": "CompetitivenessRepository", "injected": true}],
    "methods": [{"name": "getPayerCompetitors", "annotations": ["@Transactional"], "returns": "List<PayerDto>"}],
    "db_queries": [{"method": "findByPayer", "type": "jpql", "query": "SELECT ...", "tables": ["COMPETITOR"]}],
    "callers": ["CompetitivenessController"],    # reverse edges (who injects this)
    "callees": ["CompetitivenessRepository"],    # forward edges (what this injects)
  }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class InjectedField:
    """A dependency declared in the class via @Autowired / constructor injection."""
    name: str           # Java field name (e.g. "competitivenessRepository")
    type: str           # Java type (e.g. "CompetitivenessRepository")
    injected: bool      # True = Spring-injected, False = other field


@dataclass
class MethodSignature:
    """A method signature extracted without parsing the body."""
    name: str
    return_type: str
    annotations: list[str]
    parameter_types: list[str]
    is_public: bool
    is_transactional: bool
    transaction_read_only: bool


@dataclass
class DBQueryInfo:
    """A database query extracted deterministically."""
    method: str
    query: str
    query_type: str      # jpql | native_sql | derived | jooq | jdbc | mybatis
    tables: list[str]
    columns: list[str]
    operation: str       # SELECT | INSERT | UPDATE | DELETE
    is_native: bool


@dataclass
class ClassKnowledgeNode:
    """
    Full structural knowledge extracted from one Java class.
    """
    # Identity
    class_name: str
    package: str
    file_path: str
    repo_name: str

    # Spring metadata
    stereotype: str          # @Service | @Repository | @RestController | @Component | ""
    is_interface: bool

    # Structure
    fields: list[InjectedField] = field(default_factory=list)
    methods: list[MethodSignature] = field(default_factory=list)
    db_queries: list[DBQueryInfo] = field(default_factory=list)

    # Graph edges
    callers: list[str] = field(default_factory=list)    # classes that inject this
    callees: list[str] = field(default_factory=list)    # classes this injects

    # Implements / extends
    implements: list[str] = field(default_factory=list)
    extends: Optional[str] = None

    def to_metadata(self) -> dict:
        """Serialise to node metadata dict for graph storage."""
        return {
            "stereotype": self.stereotype,
            "package": self.package,
            "file": self.file_path,
            "is_interface": self.is_interface,
            "fields": [
                {
                    "name": f.name,
                    "type": f.type,
                    "injected": f.injected,
                }
                for f in self.fields
            ],
            "methods": [
                {
                    "name": m.name,
                    "return_type": m.return_type,
                    "annotations": m.annotations,
                    "parameter_types": m.parameter_types,
                    "is_public": m.is_public,
                    "is_transactional": m.is_transactional,
                    "transaction_read_only": m.transaction_read_only,
                }
                for m in self.methods
            ],
            "db_queries": [
                {
                    "method": q.method,
                    "query": q.query,
                    "type": q.query_type,
                    "tables": q.tables,
                    "columns": q.columns,
                    "operation": q.operation,
                    "is_native": q.is_native,
                }
                for q in self.db_queries
            ],
            "callers": self.callers,
            "callees": self.callees,
            "implements": self.implements,
            "extends": self.extends,
        }

    def to_llm_summary(self) -> str:
        """
        Compact, structured summary for the LLM context window.
        Used as supplemental context in ContextSynthesizer (Stage 3).
        """
        lines = [
            f"Class: {self.class_name}  [{self.stereotype or 'unknown'}]",
            f"Package: {self.package}",
        ]
        if self.implements:
            lines.append(f"Implements: {', '.join(self.implements)}")
        if self.extends:
            lines.append(f"Extends: {self.extends}")

        if self.fields:
            injected = [f for f in self.fields if f.injected]
            if injected:
                lines.append(f"Injected deps: {', '.join(f.type for f in injected)}")

        if self.methods:
            lines.append("Methods:")
            for m in self.methods[:10]:  # cap at 10
                anns = " ".join(m.annotations[:3]) + " " if m.annotations else ""
                lines.append(f"  {anns}{m.return_type} {m.name}({', '.join(m.parameter_types[:4])})")

        if self.db_queries:
            lines.append("DB queries:")
            for q in self.db_queries[:5]:
                lines.append(f"  [{q.query_type}] {q.method}: {q.query[:120]}")
                if q.tables:
                    lines.append(f"    tables: {', '.join(q.tables)}")

        if self.callers:
            lines.append(f"Called by: {', '.join(self.callers)}")
        if self.callees:
            lines.append(f"Calls: {', '.join(self.callees)}")

        return "\n".join(lines)


# ── Regex patterns ────────────────────────────────────────────────────────────

_PACKAGE_RE   = re.compile(r'^package\s+([\w.]+)\s*;', re.MULTILINE)
_CLASS_RE     = re.compile(r'(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)')
_INTERFACE_RE = re.compile(r'\bpublic\s+interface\b')
_EXTENDS_RE   = re.compile(r'\bextends\s+([\w<>]+)')
_IMPLEMENTS_RE = re.compile(r'\bimplements\s+([\w<>,\s]+?)(?:\{|extends)')

_STEREOTYPE_RE = re.compile(
    r'@(Service|Repository|RestController|Controller|Component|'
    r'EventHandler|MessageListener|SqsListener|KafkaListener)\b'
)

_FIELD_RE = re.compile(
    r'(?P<annotations>(?:@\w+(?:\([^)]*\))?\s+)*)'
    r'(?:private|protected|public)\s+'
    r'(?:final\s+|static\s+)*'
    r'(?P<type>[\w<>?,\s\[\]]+?)\s+'
    r'(?P<name>\w+)\s*;',
    re.MULTILINE,
)

_AUTOWIRED_RE = re.compile(r'@(?:Autowired|Inject|Resource)\b')

_METHOD_RE = re.compile(
    r'(?P<annotations>(?:@\w+(?:\([^)]*\))?\s+)*)'
    r'(?P<access>public|protected|private|default)\s+'
    r'(?:(?:static|final|abstract|synchronized|default)\s+)*'
    r'(?P<return>[\w<>?,\s\[\]]+?)\s+'
    r'(?P<name>\w+)\s*'
    r'\((?P<params>[^)]*(?:\([^)]*\)[^)]*)*)\)',
    re.MULTILINE,
)

_TRANSACTIONAL_RE = re.compile(r'@Transactional(?:\s*\(([^)]*)\))?')


# ── Main class ────────────────────────────────────────────────────────────────

class ClassNodeExtractor:
    """
    Extract rich structural knowledge from a Java source file.

    Designed to be called on each file in the NavigatorAgent assembled chain,
    producing a ClassKnowledgeNode per file.

    Usage::

        extractor = ClassNodeExtractor()

        # Extract from one file
        node = extractor.extract(
            file_path="/path/to/CompetitivenessService.java",
            repo_name="network-iq-backend-java",
            caller_classes=["CompetitivenessController"],   # who calls this
        )

        # Use the structured metadata
        graph_node.metadata["class_knowledge"] = node.to_metadata()

        # Inject the LLM summary into synthesis context
        synthesis_prompt += node.to_llm_summary()
    """

    def extract(
        self,
        file_path: str,
        repo_name: str,
        caller_classes: Optional[list[str]] = None,
    ) -> Optional[ClassKnowledgeNode]:
        """
        Parse a Java source file and return a ClassKnowledgeNode.
        Returns None if the file cannot be read.
        """
        try:
            content = Path(file_path).read_text(errors="ignore")
        except Exception as e:
            log.warning("ClassNodeExtractor: cannot read file", file=file_path, error=str(e))
            return None

        # ── Identity ───────────────────────────────────────────────────────────
        pkg_m = _PACKAGE_RE.search(content)
        package = pkg_m.group(1) if pkg_m else ""

        cls_m = _CLASS_RE.search(content)
        class_name = cls_m.group(1) if cls_m else Path(file_path).stem

        is_interface = bool(_INTERFACE_RE.search(content[:1000]))

        # ── Spring stereotype ──────────────────────────────────────────────────
        # Only check the class header (before the class body)
        class_header_end = content.find("{", content.find("class ") if "class " in content else 0)
        class_header = content[:class_header_end + 50] if class_header_end > 0 else content[:500]
        stereo_m = _STEREOTYPE_RE.search(class_header)
        stereotype = f"@{stereo_m.group(1)}" if stereo_m else ""

        # ── Inheritance ────────────────────────────────────────────────────────
        extends_m = _EXTENDS_RE.search(class_header)
        extends = extends_m.group(1).split("<")[0].strip() if extends_m else None

        implements_m = _IMPLEMENTS_RE.search(class_header)
        implements = []
        if implements_m:
            raw = implements_m.group(1)
            implements = [s.strip().split("<")[0].strip() for s in raw.split(",") if s.strip()]

        # ── Fields ─────────────────────────────────────────────────────────────
        fields = self._extract_fields(content)
        injected_types = {f.type for f in fields if f.injected}

        # ── Methods ────────────────────────────────────────────────────────────
        methods = self._extract_methods(content)

        # ── DB queries ─────────────────────────────────────────────────────────
        from companybrain.agents.tools.code_tools import extract_db_queries
        raw_queries = extract_db_queries(file_path)
        db_queries = [
            DBQueryInfo(
                method=q.get("method", ""),
                query=q.get("query", ""),
                query_type=q.get("type", "unknown"),
                tables=q.get("tables", []),
                columns=q.get("columns", []),
                operation=q.get("operation", "SELECT"),
                is_native=q.get("is_native", False),
            )
            for q in raw_queries
        ]

        # ── Edges ──────────────────────────────────────────────────────────────
        callees = sorted({f.type for f in fields if f.injected})

        node = ClassKnowledgeNode(
            class_name=class_name,
            package=package,
            file_path=str(Path(file_path).name),
            repo_name=repo_name,
            stereotype=stereotype,
            is_interface=is_interface,
            fields=fields,
            methods=methods,
            db_queries=db_queries,
            callers=caller_classes or [],
            callees=callees,
            implements=implements,
            extends=extends,
        )

        log.info(
            "ClassNodeExtractor: extracted",
            class_name=class_name,
            stereotype=stereotype,
            fields=len([f for f in fields if f.injected]),
            methods=len(methods),
            db_queries=len(db_queries),
            callers=len(node.callers),
            callees=len(node.callees),
        )
        return node

    def extract_chain(
        self,
        assembled_nodes: list[dict],
        repo_name: str,
    ) -> list[ClassKnowledgeNode]:
        """
        Extract ClassKnowledgeNodes for all files in an assembled chain.

        Builds the caller graph (who calls whom) across the chain so that
        reverse edges (callers) are populated for each node.

        assembled_nodes: list of {"file_path": ..., "class_name": ..., "depth": ...}
        """
        # First pass: build a simple depth → class_name map
        depth_to_class: dict[int, str] = {}
        for n in assembled_nodes:
            depth_to_class[n["depth"]] = n.get("class_name", Path(n["file_path"]).stem)

        # Second pass: for each node, its caller is the node at depth - 1
        result: list[ClassKnowledgeNode] = []
        for n in assembled_nodes:
            depth = n["depth"]
            caller = [depth_to_class[depth - 1]] if depth > 0 and (depth - 1) in depth_to_class else []
            node = self.extract(
                file_path=n["file_path"],
                repo_name=repo_name,
                caller_classes=caller,
            )
            if node:
                result.append(node)

        return result

    # ── Field extraction ──────────────────────────────────────────────────────

    def _extract_fields(self, content: str) -> list[InjectedField]:
        fields: list[InjectedField] = []
        seen: set[str] = set()

        for m in _FIELD_RE.finditer(content):
            annotations_str = m.group("annotations")
            java_type_raw = m.group("type").strip()
            name = m.group("name").strip()

            if name in seen:
                continue
            seen.add(name)

            # Strip generics for the type name
            java_type = re.sub(r'<.*>', '', java_type_raw).strip()

            # Skip primitives and common non-injectable types
            if java_type.lower() in (
                "string", "int", "long", "boolean", "double", "float",
                "byte", "char", "short", "void", "object",
            ):
                continue
            if java_type[0].islower():  # primitive / local variable
                continue

            # Determine if this is Spring-injected
            is_injected = bool(_AUTOWIRED_RE.search(annotations_str))

            # Also treat 'private final XxxService xxx;' as injected (Lombok pattern)
            if not is_injected:
                is_injected = bool(re.search(r'\bfinal\b', m.group(0))) and any(
                    java_type.endswith(s) for s in (
                        "Service", "Repository", "Repo", "Client",
                        "Gateway", "Adapter", "Handler", "Publisher",
                        "DAO", "Mapper", "Manager",
                    )
                )

            fields.append(InjectedField(
                name=name,
                type=java_type,
                injected=is_injected,
            ))

        return fields

    # ── Method extraction ─────────────────────────────────────────────────────

    def _extract_methods(self, content: str) -> list[MethodSignature]:
        methods: list[MethodSignature] = []
        seen: set[str] = set()

        for m in _METHOD_RE.finditer(content):
            name = m.group("name")
            if name in seen:
                continue
            # Skip common non-method keywords caught by the regex
            if name in ("if", "for", "while", "switch", "catch", "class", "interface"):
                continue
            seen.add(name)

            annotations_str = m.group("annotations") or ""
            access = m.group("access")
            return_type = m.group("return").strip()
            params_raw = m.group("params") or ""

            # Extract annotations
            annotations = re.findall(r'@\w+(?:\([^)]*\))?', annotations_str)

            # @Transactional detection
            trans_m = _TRANSACTIONAL_RE.search(annotations_str)
            is_transactional = trans_m is not None
            trans_read_only = False
            if trans_m and trans_m.group(1):
                trans_read_only = bool(re.search(r'readOnly\s*=\s*true', trans_m.group(1)))

            # Parameter types (strip names, keep types)
            param_types: list[str] = []
            for p in _split_params(params_raw):
                p = p.strip()
                # Remove annotations
                p = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', p).strip()
                # Last word is the name; everything before is type
                parts = p.rsplit(None, 1)
                if len(parts) == 2:
                    param_types.append(parts[0].strip())

            methods.append(MethodSignature(
                name=name,
                return_type=return_type,
                annotations=annotations,
                parameter_types=param_types,
                is_public=(access == "public"),
                is_transactional=is_transactional,
                transaction_read_only=trans_read_only,
            ))

        return methods


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_params(params_str: str) -> list[str]:
    """Split on commas, respecting angle brackets for generic types."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params_str:
        if ch in ("<", "(", "["):
            depth += 1
        elif ch in (">", ")", "]"):
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts
