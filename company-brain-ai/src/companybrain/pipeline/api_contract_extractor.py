"""
APIContractExtractor — Task #39: Deterministic API contract extraction.

Extracts the complete API contract from Java Spring Boot source WITHOUT any LLM call.
Everything here is deterministic regex/AST over Java annotations.

What it captures:
  • HTTP method + full path (class-level @RequestMapping + method-level mapping)
  • @RequestParam — name, type, required, defaultValue
  • @PathVariable — name, type
  • @RequestBody — DTO class name + all its fields with types + @NotNull/@Valid
  • @RequestHeader — name, type, required
  • Response type — from ResponseEntity<T> or plain return type
  • @PreAuthorize / @Secured — security constraints
  • @Transactional — presence + readOnly flag

This feeds directly into the knowledge graph as a "APIContract" node with no hallucination risk.
LLM is only used LATER (Task #40: BusinessSemanticsExtractor) for things like:
  - Parameter purpose in business terms
  - Valid value ranges / enum semantics
  - Multiselect detection
  - Business rules
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
class ParamDef:
    """A single HTTP parameter extracted deterministically."""
    kind: str               # path | query | body | header | matrix
    name: str               # parameter name as seen in HTTP request
    java_name: str          # Java method parameter name (may differ)
    java_type: str          # Java type (String, Long, List<String>, CustomEnum, ...)
    required: bool = True
    default_value: Optional[str] = None
    # Validation constraints (from javax/jakarta.validation)
    not_null: bool = False
    not_blank: bool = False
    not_empty: bool = False
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    size_min: Optional[int] = None
    size_max: Optional[int] = None
    pattern: Optional[str] = None


@dataclass
class DtoField:
    """A field in a @RequestBody DTO."""
    name: str
    java_type: str
    required: bool = False          # @NotNull / @NotBlank present
    validation_annotations: list[str] = field(default_factory=list)


@dataclass
class APIContract:
    """
    Complete, deterministically-extracted API contract for one endpoint handler.
    """
    # Identity
    http_method: str                       # GET | POST | PUT | DELETE | PATCH
    path: str                              # full path e.g. /api/v1/mcheck/niq/...
    handler_class: str                     # Spring controller class name
    handler_method: str                    # Java method name

    # Parameters
    path_params: list[ParamDef] = field(default_factory=list)
    query_params: list[ParamDef] = field(default_factory=list)
    headers: list[ParamDef] = field(default_factory=list)
    request_body: Optional[str] = None    # DTO class name (if any)
    request_body_fields: list[DtoField] = field(default_factory=list)

    # Response
    response_type: str = ""               # raw Java return type string
    response_dto: Optional[str] = None    # extracted DTO class if ResponseEntity<T>

    # Cross-cutting
    security: list[str] = field(default_factory=list)   # @PreAuthorize values
    transactional: bool = False
    transactional_read_only: bool = False
    produces: list[str] = field(default_factory=list)   # e.g. ["application/json"]
    consumes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise for storage in node metadata."""
        return {
            "http_method":      self.http_method,
            "path":             self.path,
            "handler_class":    self.handler_class,
            "handler_method":   self.handler_method,
            "path_params":      [_param_to_dict(p) for p in self.path_params],
            "query_params":     [_param_to_dict(p) for p in self.query_params],
            "headers":          [_param_to_dict(p) for p in self.headers],
            "request_body":     self.request_body,
            "request_body_fields": [_field_to_dict(f) for f in self.request_body_fields],
            "response_type":    self.response_type,
            "response_dto":     self.response_dto,
            "security":         self.security,
            "transactional":    self.transactional,
            "transactional_read_only": self.transactional_read_only,
            "produces":         self.produces,
            "consumes":         self.consumes,
        }

    def summary(self) -> str:
        """Human-readable one-liner for logging and LLM context."""
        parts = [f"{self.http_method} {self.path}"]
        if self.path_params:
            parts.append(f"path={[p.name for p in self.path_params]}")
        if self.query_params:
            parts.append(f"query={[p.name for p in self.query_params]}")
        if self.request_body:
            parts.append(f"body={self.request_body}")
        parts.append(f"→ {self.response_type or 'void'}")
        return "  ".join(parts)


# ── Regex patterns ────────────────────────────────────────────────────────────

# Mapping annotations (class or method level)
_MAPPING_RE = re.compile(
    r'@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
    r'\s*\(([^)]*)\)',
    re.MULTILINE | re.DOTALL,
)

# Extract value= or path= from annotation attributes
_ANN_VALUE_RE  = re.compile(r'(?:value|path)\s*=\s*(?:\{[^}]*\}|\{?"([^"]+)")', re.DOTALL)
_ANN_METHOD_RE = re.compile(r'method\s*=\s*RequestMethod\.(\w+)')
_ANN_PRODUCES  = re.compile(r'produces\s*=\s*(?:\{([^}]+)\}|"([^"]+)")')
_ANN_CONSUMES  = re.compile(r'consumes\s*=\s*(?:\{([^}]+)\}|"([^"]+)")')

# Method-level parameter annotations
_PARAM_ANN_RE = re.compile(
    r'@(RequestParam|PathVariable|RequestHeader|RequestBody|RequestPart)'
    r'(?:\s*\(([^)]*)\))?',
    re.MULTILINE,
)

# Validation annotations
_VALID_ANN_RE = re.compile(
    r'@(NotNull|NotBlank|NotEmpty|Min|Max|Size|Pattern|Valid|Validated)'
    r'(?:\s*\(([^)]*)\))?',
)

# @PreAuthorize / @Secured
_SECURITY_RE = re.compile(
    r'@(PreAuthorize|Secured|RolesAllowed)\s*\(\s*["\{]([^"}\)]+)["\}]\s*\)',
    re.MULTILINE,
)

# @Transactional
_TRANSACTIONAL_RE = re.compile(
    r'@Transactional(?:\s*\(([^)]*)\))?',
    re.MULTILINE,
)

# Extract class-level @RequestMapping path
_CLASS_RE = re.compile(r'(?:class|interface)\s+(\w+)')

# Java method signature: return type + name + parameters
_METHOD_SIG_RE = re.compile(
    r'(?:public|protected)\s+'
    r'(?:(?:static|final|abstract|synchronized)\s+)*'
    r'([\w<>?,\s\[\]]+?)\s+'           # return type (group 1)
    r'(\w+)\s*'                         # method name (group 2)
    r'\(([^)]*(?:\([^)]*\)[^)]*)*)\)',  # parameters (group 3, handles nested generics)
    re.MULTILINE,
)

# Single parameter: annotations + type + name
_SINGLE_PARAM_RE = re.compile(
    r'((?:@\w+(?:\([^)]*\))?\s+)*)'   # annotations (group 1)
    r'([\w<>?,\s\[\]]+?)\s+'           # type (group 2)
    r'(\w+)\s*$',                       # name (group 3)
    re.DOTALL,
)

# DTO field: annotations + type + name
_DTO_FIELD_RE = re.compile(
    r'((?:@\w+(?:\([^)]*\))?\s+)*?)'  # annotations (group 1)
    r'(?:private|protected|public)\s+'
    r'(?:final\s+)?'
    r'([\w<>?,\s\[\]]+?)\s+'           # type (group 2)
    r'(\w+)\s*;',                       # name (group 3)
    re.MULTILINE | re.DOTALL,
)


# ── Main class ─────────────────────────────────────────────────────────────────

class APIContractExtractor:
    """
    Deterministically extract the full API contract for an endpoint handler.

    Usage::

        extractor = APIContractExtractor()

        # From a controller file + method name
        contract = extractor.extract(
            controller_file="/path/to/CompetitivenessController.java",
            handler_method="getCompetitorsPayer",   # or "" to auto-detect from endpoint
            endpoint="/api/v1/mcheck/niq/competitiveness/summary/competitors/payer",
            repo_path="/path/to/repo",
        )
        print(contract.summary())
        # GET /api/v1/mcheck/niq/...  query=[marketId, payerType]  → ResponseEntity<...>

        # Store in knowledge graph
        node_metadata["api_contract"] = contract.to_dict()
    """

    def extract(
        self,
        controller_file: str,
        handler_method: str,
        endpoint: str,
        repo_path: str,
        http_method: str = "GET",
    ) -> Optional[APIContract]:
        """
        Extract the API contract from a Spring Boot controller file.

        Returns None if the handler cannot be found.
        """
        try:
            content = Path(controller_file).read_text(errors="ignore")
        except Exception as e:
            log.warning("APIContractExtractor: cannot read file", file=controller_file, error=str(e))
            return None

        handler_class = _extract_class_name(content)

        # ── 1. Find the handler method ─────────────────────────────────────────
        method_info = self._find_handler_method(content, handler_method, endpoint, http_method)
        if not method_info:
            log.warning(
                "APIContractExtractor: handler method not found",
                file=controller_file, method=handler_method, endpoint=endpoint,
            )
            return None

        actual_method, method_body, method_start, http_verb, full_path = method_info

        # ── 2. Extract parameters from method signature ────────────────────────
        params_str = method_body.split("{")[0] if "{" in method_body else method_body
        params_str = re.sub(r'.*\(', "", params_str, count=1)  # strip everything before (
        if ")" in params_str:
            params_str = params_str[:params_str.rfind(")")]

        path_params, query_params, headers_list, request_body = self._parse_parameters(params_str)

        # ── 3. Extract return type ─────────────────────────────────────────────
        response_type, response_dto = self._parse_return_type(method_body)

        # ── 4. Security annotations on the method ─────────────────────────────
        pre_method = content[max(0, method_start - 300): method_start + 50]
        security = _extract_security(pre_method)

        # Also check class-level security
        class_head = content[:2000]
        security = list(set(security + _extract_security(class_head)))

        # ── 5. @Transactional ─────────────────────────────────────────────────
        transactional, read_only = _extract_transactional(pre_method)

        # ── 6. produces / consumes from mapping ───────────────────────────────
        mapping_snippet = content[max(0, method_start - 400): method_start + 10]
        produces, consumes = _extract_media_types(mapping_snippet)

        # ── 7. Expand @RequestBody DTO ────────────────────────────────────────
        body_fields: list[DtoField] = []
        if request_body:
            body_fields = self._extract_dto_fields(request_body, repo_path)

        contract = APIContract(
            http_method=http_verb,
            path=full_path,
            handler_class=handler_class,
            handler_method=actual_method,
            path_params=path_params,
            query_params=query_params,
            headers=headers_list,
            request_body=request_body,
            request_body_fields=body_fields,
            response_type=response_type,
            response_dto=response_dto,
            security=security,
            transactional=transactional,
            transactional_read_only=read_only,
            produces=produces,
            consumes=consumes,
        )

        log.info(
            "APIContractExtractor: extracted contract",
            method=http_verb,
            path=full_path,
            query_params=[p.name for p in query_params],
            path_params=[p.name for p in path_params],
            body=request_body,
            response=response_type,
        )
        return contract

    # ── Handler method finder ─────────────────────────────────────────────────

    def _find_handler_method(
        self,
        content: str,
        handler_method: str,
        endpoint: str,
        http_method: str,
    ) -> Optional[tuple[str, str, int, str, str]]:
        """
        Returns (method_name, method_text, start_pos, http_verb, full_path) or None.

        Tries:
          1. If handler_method is given, find it directly.
          2. Otherwise scan all @RequestMapping annotations and match path.
        """
        # Class-level path prefix
        class_path = _extract_class_mapping_path(content)

        if handler_method:
            # Direct lookup: find the method + its preceding annotations
            m = re.search(
                rf'(?:public|protected)\s+[\w<>?,\s\[\]]+\s+{re.escape(handler_method)}\s*\(',
                content,
            )
            if m:
                method_start = _find_annotation_start(content, m.start())
                method_text = _extract_method_from(content, m.start())
                mapping_snippet = content[method_start: m.start() + 5]
                http_verb, rel_path = _parse_mapping_annotation(mapping_snippet, http_method)
                full_path = _join_paths(class_path, rel_path)
                return handler_method, method_text, method_start, http_verb, full_path

        # Path-based lookup: find method whose mapping annotation matches endpoint
        parts = [p for p in endpoint.split("/") if p and not re.match(r'^v\d+$', p) and p != "api"]
        candidates = {("/" + "/".join(parts[i:])).lower().rstrip("/") for i in range(len(parts))}
        candidates.add(endpoint.lower().rstrip("/"))

        first_method_pos = _first_method_pos(content)
        for match in _MAPPING_RE.finditer(content):
            if match.start() < first_method_pos:
                continue  # class-level annotation, skip
            ann_args = match.group(2)
            _, rel_path = _parse_mapping_attrs(match.group(1), ann_args, http_method)
            full = _join_paths(class_path, rel_path).lower().rstrip("/")
            if full in candidates or _tail_matches(full, candidates):
                # Found the right mapping — now find the method name after it
                after = content[match.end(): match.end() + 500]
                sig_m = re.search(
                    r'(?:public|protected)\s+[\w<>?,\s\[\]]+\s+(\w+)\s*\(',
                    after,
                )
                if sig_m:
                    method_name = sig_m.group(1)
                    method_start_abs = match.end() + sig_m.start()
                    method_text = _extract_method_from(content, method_start_abs)
                    ann_start = _find_annotation_start(content, match.start())
                    http_verb, _ = _parse_mapping_attrs(match.group(1), ann_args, http_method)
                    return method_name, method_text, ann_start, http_verb, _join_paths(class_path, rel_path)

        return None

    # ── Parameter parsing ─────────────────────────────────────────────────────

    def _parse_parameters(
        self,
        params_str: str,
    ) -> tuple[list[ParamDef], list[ParamDef], list[ParamDef], Optional[str]]:
        """
        Parse a Java method parameter list string into typed ParamDefs.

        params_str example:
          @RequestParam String marketId,
          @RequestParam(required=false, defaultValue="ALL") String payerType,
          @PathVariable Long id,
          @RequestBody PayerFilterRequest request
        """
        path_params: list[ParamDef] = []
        query_params: list[ParamDef] = []
        headers_list: list[ParamDef] = []
        request_body: Optional[str] = None

        # Split on commas that are NOT inside angle brackets (generic types)
        raw_params = _split_params(params_str)

        for raw in raw_params:
            raw = raw.strip()
            if not raw:
                continue

            # Extract annotations on this parameter
            ann_matches = list(_PARAM_ANN_RE.finditer(raw))
            if not ann_matches:
                continue   # no Spring param annotation — skip (might be HttpServletRequest etc)

            ann = ann_matches[0]
            ann_name = ann.group(1)
            ann_args = ann.group(2) or ""

            # Strip the annotation from raw to get type + name
            remainder = raw[ann.end():].strip()

            # Collect validation annotations
            valid_anns = [m.group(0) for m in _VALID_ANN_RE.finditer(raw)]

            # Parse type and Java name
            type_name_m = re.match(r'([\w<>?,\s\[\]]+?)\s+(\w+)\s*$', remainder.strip())
            if not type_name_m:
                continue
            java_type = type_name_m.group(1).strip()
            java_name = type_name_m.group(2).strip()

            # Parse annotation attributes
            http_name = _attr(ann_args, "value") or _attr(ann_args, "name") or java_name
            required = _attr_bool(ann_args, "required", default=True)
            default_val = _attr(ann_args, "defaultValue")

            # Validation
            not_null  = any("NotNull"  in a or "NotBlank" in a or "NotEmpty" in a for a in valid_anns)
            not_blank = any("NotBlank" in a for a in valid_anns)
            not_empty = any("NotEmpty" in a for a in valid_anns)

            param = ParamDef(
                kind="",
                name=http_name,
                java_name=java_name,
                java_type=java_type,
                required=required,
                default_value=default_val,
                not_null=not_null,
                not_blank=not_blank,
                not_empty=not_empty,
            )

            if ann_name == "PathVariable":
                param.kind = "path"
                param.required = True   # path params are always required
                path_params.append(param)

            elif ann_name == "RequestParam":
                param.kind = "query"
                query_params.append(param)

            elif ann_name == "RequestHeader":
                param.kind = "header"
                headers_list.append(param)

            elif ann_name in ("RequestBody", "RequestPart"):
                # The type IS the DTO class
                request_body = java_type.strip()

        return path_params, query_params, headers_list, request_body

    # ── Return type parsing ────────────────────────────────────────────────────

    def _parse_return_type(self, method_text: str) -> tuple[str, Optional[str]]:
        """
        Extract return type from method signature.

        Examples:
          ResponseEntity<List<PayerDto>>  → ("ResponseEntity<List<PayerDto>>", "PayerDto")
          Page<CompetitorSummary>          → ("Page<CompetitorSummary>", "CompetitorSummary")
          void                             → ("void", None)
        """
        # Match the method signature up to the first (
        sig_m = re.match(
            r'(?:public|protected)\s+(?:(?:static|final)\s+)*([\w<>?,\s\[\]]+?)\s+\w+\s*\(',
            method_text.strip(),
        )
        if not sig_m:
            return "", None

        return_type = sig_m.group(1).strip()

        # Extract inner DTO from ResponseEntity<...> or Page<...>
        dto = None
        inner_m = re.search(r'(?:ResponseEntity|Page|List|Optional)\s*<\s*([\w<>?,\s]+?)\s*>', return_type)
        if inner_m:
            inner = inner_m.group(1).strip()
            # Strip List< from List<SomeDto>
            inner_list = re.sub(r'^List\s*<\s*|\s*>$', '', inner).strip()
            # Take the outermost class name if it looks like a DTO
            candidate = inner_list.split(",")[0].strip().split("<")[0].strip()
            if candidate and not candidate[0].islower() and candidate not in (
                "String", "Long", "Integer", "Boolean", "Double", "Object", "Void"
            ):
                dto = candidate

        return return_type, dto

    # ── DTO field expansion ────────────────────────────────────────────────────

    def _extract_dto_fields(self, dto_class: str, repo_path: str) -> list[DtoField]:
        """
        Find the DTO class file and extract its fields with validation annotations.
        Returns [] if the file is not found.
        """
        from companybrain.agents.tools.code_tools import find_file_by_name
        files = find_file_by_name(dto_class, repo_path)
        if not files:
            log.debug("APIContractExtractor: DTO file not found", dto=dto_class)
            return []

        try:
            content = Path(files[0]).read_text(errors="ignore")
        except Exception:
            return []

        fields: list[DtoField] = []
        for m in _DTO_FIELD_RE.finditer(content):
            annotations_str = m.group(1)
            java_type = m.group(2).strip()
            name = m.group(3).strip()

            # Skip static/final constants (usually uppercase)
            if name.isupper():
                continue

            valid_anns = re.findall(r'@\w+(?:\([^)]*\))?', annotations_str)
            required = any(
                ("NotNull" in a or "NotBlank" in a or "NotEmpty" in a) for a in valid_anns
            )

            fields.append(DtoField(
                name=name,
                java_type=java_type,
                required=required,
                validation_annotations=valid_anns,
            ))

        log.debug("APIContractExtractor: DTO fields", dto=dto_class, fields=len(fields))
        return fields


# ── Module helpers ────────────────────────────────────────────────────────────

def _extract_class_name(content: str) -> str:
    m = _CLASS_RE.search(content)
    return m.group(1) if m else ""


def _extract_class_mapping_path(content: str) -> str:
    """Get the class-level @RequestMapping value (base path)."""
    first_method = _first_method_pos(content)
    for m in _MAPPING_RE.finditer(content):
        if m.start() < first_method:
            ann_args = m.group(2)
            val = _attr(ann_args, "value") or _attr(ann_args, "path") or ""
            if val:
                return val.rstrip("/")
    return ""


def _parse_mapping_annotation(snippet: str, default_http: str) -> tuple[str, str]:
    """Parse a single @Mapping annotation snippet → (HTTP verb, path)."""
    m = _MAPPING_RE.search(snippet)
    if not m:
        return default_http.upper(), ""
    return _parse_mapping_attrs(m.group(1), m.group(2), default_http)


def _parse_mapping_attrs(ann_name: str, ann_args: str, default_http: str) -> tuple[str, str]:
    """Extract HTTP verb and path from annotation name + attribute string."""
    verb_map = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "DeleteMapping": "DELETE",
        "PatchMapping": "PATCH",
        "RequestMapping": default_http.upper(),
    }
    verb = verb_map.get(ann_name, default_http.upper())

    # Override from method= in @RequestMapping
    method_m = _ANN_METHOD_RE.search(ann_args)
    if method_m:
        verb = method_m.group(1).upper()

    # Extract path
    path = _attr(ann_args, "value") or _attr(ann_args, "path") or ""
    # Handle array: {"path"} → "path"
    if path.startswith("{"):
        path = path.strip("{}").strip().strip('"').strip("'").split(",")[0].strip()

    return verb, path


def _join_paths(*parts: str) -> str:
    result = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not p.startswith("/"):
            p = "/" + p
        result = result.rstrip("/") + p
    return result or "/"


def _attr(ann_args: str, key: str) -> Optional[str]:
    """Extract a named attribute value from annotation args string."""
    # key = "value" → matches value = "something"
    m = re.search(rf'{re.escape(key)}\s*=\s*"([^"]*)"', ann_args)
    if m:
        return m.group(1)
    # Positional (unnamed) string arg — only for value/path/name
    if key in ("value", "path", "name"):
        m = re.search(r'^\s*"([^"]*)"', ann_args.strip())
        if m:
            return m.group(1)
    return None


def _attr_bool(ann_args: str, key: str, default: bool = True) -> bool:
    m = re.search(rf'{re.escape(key)}\s*=\s*(true|false)', ann_args, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "true"
    return default


def _extract_security(snippet: str) -> list[str]:
    return [f"@{m.group(1)}({m.group(2).strip()})" for m in _SECURITY_RE.finditer(snippet)]


def _extract_transactional(snippet: str) -> tuple[bool, bool]:
    m = _TRANSACTIONAL_RE.search(snippet)
    if not m:
        return False, False
    args = m.group(1) or ""
    read_only = bool(re.search(r'readOnly\s*=\s*true', args))
    return True, read_only


def _extract_media_types(snippet: str) -> tuple[list[str], list[str]]:
    produces, consumes = [], []
    pm = _ANN_PRODUCES.search(snippet)
    if pm:
        raw = pm.group(1) or pm.group(2) or ""
        produces = [v.strip().strip('"') for v in raw.split(",") if v.strip()]
    cm = _ANN_CONSUMES.search(snippet)
    if cm:
        raw = cm.group(1) or cm.group(2) or ""
        consumes = [v.strip().strip('"') for v in raw.split(",") if v.strip()]
    return produces, consumes


def _split_params(params_str: str) -> list[str]:
    """Split parameter list on commas, respecting angle brackets for generics."""
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


def _find_annotation_start(content: str, pos: int) -> int:
    lines = content[:pos].split("\n")
    start = pos
    for line in reversed(lines):
        stripped = line.strip()
        if re.match(r'@\w+', stripped) or stripped.startswith("//"):
            start -= len(line) + 1
        else:
            break
    return max(0, start)


def _first_method_pos(content: str) -> int:
    m = re.search(r'(?:public|protected|private)\s+[\w<>?,\s\[\]]+\s+\w+\s*\(', content)
    return m.start() if m else len(content)


def _tail_matches(full: str, candidates: set[str]) -> bool:
    parts = full.strip("/").split("/")
    for i in range(len(parts)):
        if ("/" + "/".join(parts[i:])) in candidates:
            return True
    return False


def _extract_method_from(content: str, pos: int) -> str:
    """Extract method signature + body starting at pos (brace-balanced)."""
    open_brace = content.find("{", pos)
    if open_brace == -1:
        return content[pos: pos + 300]
    depth = 1
    i = open_brace + 1
    while i < len(content) and depth > 0:
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    # Cap at 4000 chars
    result = content[pos:i]
    return result[:4000] + "\n// ..." if len(result) > 4000 else result


def _param_to_dict(p: ParamDef) -> dict:
    return {
        "kind": p.kind, "name": p.name, "java_name": p.java_name,
        "java_type": p.java_type, "required": p.required,
        "default_value": p.default_value,
        "not_null": p.not_null, "not_blank": p.not_blank,
        "min_value": p.min_value, "max_value": p.max_value,
        "size_min": p.size_min, "size_max": p.size_max, "pattern": p.pattern,
    }


def _field_to_dict(f: DtoField) -> dict:
    return {
        "name": f.name, "java_type": f.java_type, "required": f.required,
        "validation_annotations": f.validation_annotations,
    }
