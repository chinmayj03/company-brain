"""
SmartMethodExtractor — method-level code navigation for Spring Boot Java.

PROBLEM WITH THE OLD APPROACH
──────────────────────────────
CodeTracer was reading entire Java files (trimmed to 4,000 chars each).
For a 300-line controller that's ~10,000 chars raw → trimmed at 4,000 chars.
The LLM sees: package decl, 30 imports, 5 unrelated methods, boilerplate —
and somewhere buried in there, the 12 lines it actually needs.

Signal/noise ratio: ~10%.  Result: InternalServerError from context overflow.

THE NEW APPROACH
────────────────
Extract only the METHOD CALL CHAIN relevant to the target endpoint:

  1. handler_method     — @GetMapping handler body (12 lines)
  2. class_skeleton     — just the class header + @Autowired fields (5 lines)
  3. service_method     — the specific method the handler calls (8 lines)
  4. repository_method  — the @Query method the service calls (4 lines)

Total: ~30 lines, ~650 chars, ~160 tokens.  Signal/noise: ~100%.

EXTRACTION ALGORITHM
────────────────────
1. Find handler: scan for @GetMapping (or similar) with matching path fragment
2. Extract method body: brace-balanced walk forward from opening {
3. Parse calls: find `field.method(` patterns in extracted body
4. Follow into service/repo: find the called method by name, extract it
5. Class skeleton: strip all method bodies, keep class + field declarations

INTERACTIVE CONTEXT ENRICHMENT (future)
────────────────────────────────────────
After extraction, the pipeline can surface what was found to the UI.
Users can add missing files, annotate methods, or mark business-critical paths.
These annotations are injected into L2 before Stage 1 extraction begins.
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
class MethodExtract:
    """
    A focused extract of a single method from a Java source file.
    Contains only what the LLM needs: annotations + signature + body.
    """
    file_path: str
    class_name: str
    method_name: str
    annotations: list[str]     # e.g. ["@GetMapping(\"/summary/competitors/payer\")", "@Transactional"]
    signature: str             # return type + name + params, e.g. "public ResponseEntity<?> getCompetitors(...)"
    body: str                  # method body (without outer braces)
    role: str                  # controller | service | repository | model
    repo_name: str = ""
    calls_out: list[str] = field(default_factory=list)   # method names called in body

    @property
    def focused_code(self) -> str:
        """
        Compact representation for LLM injection.
        Much smaller than a whole file: just annotations + signature + body.
        """
        lines = self.annotations + [self.signature + " {", self.body.rstrip(), "}"]
        return "\n".join(lines)

    @property
    def char_count(self) -> int:
        return len(self.focused_code)

    @property
    def token_estimate(self) -> int:
        return self.char_count // 4


@dataclass
class ClassSkeleton:
    """
    Class header + field declarations only (all method bodies stripped).
    Gives LLM the class-level structure without method noise.
    """
    file_path: str
    class_name: str
    class_annotations: list[str]   # @RestController, @Service, @Component, etc.
    implements: list[str]           # interface names
    extends: Optional[str]
    autowired_fields: list[str]     # "@Autowired\n  private PayerService payerService;"
    role: str
    repo_name: str = ""

    @property
    def focused_code(self) -> str:
        lines = (
            self.class_annotations
            + [f"public class {self.class_name}"
               + (f" extends {self.extends}" if self.extends else "")
               + (f" implements {', '.join(self.implements)}" if self.implements else "")
               + " {"]
            + ["    " + f for f in self.autowired_fields]
            + ["    // ... (other fields and methods omitted)", "}"]
        )
        return "\n".join(lines)


# ── Regex patterns ────────────────────────────────────────────────────────────

# Spring mapping annotations (group 1 = annotation name, group 2 = path)
_MAPPING_RE = re.compile(
    r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
    r'\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
    re.MULTILINE,
)

# Method signature: modifiers + return type + name + params
# Matches: "public ResponseEntity<?> getSummary(String id, @RequestParam String period)"
_METHOD_SIG_RE = re.compile(
    r'(?:(?:public|private|protected|static|final|synchronized|abstract|default)\s+)+'
    r'[\w<>\[\],\s?]+\s+'         # return type (may include generics)
    r'(\w+)\s*'                   # method name (group 1)
    r'\([^)]*\)',                  # params
    re.MULTILINE,
)

# @Autowired / @Inject field declarations
_FIELD_RE = re.compile(
    r'(?:@(?:Autowired|Inject|Resource|Value)\s*(?:\([^)]*\))?\s*)'
    r'(?:private|protected|public)?\s+'
    r'[\w<>]+\s+'
    r'(\w+)\s*;',
    re.MULTILINE,
)

# Class declaration (group 1 = class name, group 2 = extends, group 3 = implements)
_CLASS_DECL_RE = re.compile(
    r'(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+'
    r'(\w+)'
    r'(?:\s+extends\s+([\w<>]+))?'
    r'(?:\s+implements\s+([\w<>,\s]+))?'
    r'\s*\{',
    re.MULTILINE,
)

# Class-level annotations (everything above the class declaration)
_CLASS_ANN_RE = re.compile(
    r'(@\w+(?:\s*\([^)]*\))?)',
    re.MULTILINE,
)

# Method call: fieldName.methodName( or just methodName( for same-class calls
_CALL_RE = re.compile(r'\b(\w+)\.(\w+)\s*\(')

# JPA query method name pattern (findByX, findAllByX, countBy, existsBy, etc.)
_JPA_METHOD_RE = re.compile(r'\b(find|get|count|exists|delete|remove)\w+\s*\(')


# ── Main extractor ────────────────────────────────────────────────────────────

class SmartMethodExtractor:
    """
    Extracts focused method-chain code from Java Spring Boot source files.

    Usage:
        extractor = SmartMethodExtractor()
        chain = extractor.extract_chain(
            endpoint="/summary/competitors/payer",
            http_method="POST",
            controller_path="...CompetitivenessController.java",
            service_path="...CompetitivenessService.java",
            repo_path="...CompetitivenessRepository.java",
        )
    """

    def extract_chain(
        self,
        endpoint: str,
        http_method: str,
        controller_path: str,
        service_path: Optional[str] = None,
        repo_path: Optional[str] = None,
        repo_name: str = "",
    ) -> list[MethodExtract]:
        """
        Extract the focused method call chain for an endpoint.
        Returns a list of MethodExtracts in call order: controller → service → repository.
        """
        results: list[MethodExtract] = []

        # ── Controller layer ───────────────────────────────────────────────────
        ctrl_content = _read(controller_path)
        if not ctrl_content:
            return results

        ctrl_skeleton = self.extract_class_skeleton(ctrl_content, controller_path, "controller", repo_name)
        handler = self.find_handler_method(ctrl_content, endpoint, http_method, controller_path, repo_name)

        if not handler:
            log.warning("SmartMethodExtractor: no handler method found",
                        file=controller_path, endpoint=endpoint)
            # Fall back to class skeleton only
            if ctrl_skeleton:
                pseudo = _skeleton_to_method_extract(ctrl_skeleton)
                results.append(pseudo)
            return results

        results.append(handler)
        log.info("SmartMethodExtractor: handler extracted",
                 method=handler.method_name, chars=handler.char_count)

        # ── Service layer ──────────────────────────────────────────────────────
        if service_path:
            svc_content = _read(service_path)
            if svc_content:
                # Which service methods does the handler call?
                called_names = self._parse_method_calls_from_body(handler.body)
                for name in called_names[:3]:   # cap at 3 to avoid bloat
                    m = self.find_method_by_name(svc_content, name, service_path, "service", repo_name)
                    if m:
                        results.append(m)
                        log.info("SmartMethodExtractor: service method extracted",
                                 method=m.method_name, chars=m.char_count)

                # If no specific calls found, take the first public method that isn't a getter/setter
                if len(results) == 1:
                    m = self._first_significant_method(svc_content, service_path, "service", repo_name)
                    if m:
                        results.append(m)

        # ── Repository layer ───────────────────────────────────────────────────
        if repo_path:
            repo_content = _read(repo_path)
            if repo_content:
                # Gather method names called from service methods
                svc_bodies = " ".join(r.body for r in results if r.role == "service")
                called_names = self._parse_method_calls_from_body(svc_bodies)

                for name in called_names[:3]:
                    m = self.find_method_by_name(repo_content, name, repo_path, "repository", repo_name)
                    if m:
                        results.append(m)
                        log.info("SmartMethodExtractor: repository method extracted",
                                 method=m.method_name, chars=m.char_count)

                # Also grab any @Query methods if nothing was found
                if not any(r.role == "repository" for r in results):
                    queries = self._extract_query_methods(repo_content, repo_path, repo_name)
                    results.extend(queries[:2])

        total_chars = sum(r.char_count for r in results)
        log.info("SmartMethodExtractor: chain extraction complete",
                 methods=len(results),
                 total_chars=total_chars,
                 estimated_tokens=total_chars // 4)

        return results

    # ── Method finders ─────────────────────────────────────────────────────────

    def find_handler_method(
        self,
        content: str,
        endpoint: str,
        http_method: str,
        file_path: str,
        repo_name: str = "",
    ) -> Optional[MethodExtract]:
        """
        Find the Spring handler method that handles `endpoint` with `http_method`.
        Tries progressively shorter path suffixes (handles class-level + method-level split).
        """
        # Build candidate path segments from longest to shortest
        segments = _endpoint_suffixes(endpoint)

        for seg in segments:
            result = self._find_handler_for_segment(content, seg, http_method, file_path, repo_name)
            if result:
                return result

        return None

    def _find_handler_for_segment(
        self,
        content: str,
        segment: str,
        http_method: str,
        file_path: str,
        repo_name: str,
    ) -> Optional[MethodExtract]:
        """Look for a mapping annotation matching `segment` then extract the following method."""
        for match in _MAPPING_RE.finditer(content):
            ann_name, ann_path = match.group(1), match.group(2)

            # Check method match
            if http_method.upper() not in ("GET", "ANY"):
                expected_ann = f"{http_method.capitalize()}Mapping"
                if ann_name != "RequestMapping" and ann_name != expected_ann:
                    continue

            # Check path match — use trailing segment comparison
            if not _path_matches(ann_path, segment):
                continue

            # Found the annotation — extract the method that immediately follows
            method_start = match.start()
            extract = self._extract_method_after(content, method_start, file_path, "controller", repo_name)
            if extract:
                return extract

        return None

    def find_method_by_name(
        self,
        content: str,
        method_name: str,
        file_path: str,
        role: str,
        repo_name: str = "",
    ) -> Optional[MethodExtract]:
        """Extract a method by its exact name."""
        # Pattern: find method_name followed by (
        pattern = re.compile(
            rf'(?:(?:public|private|protected|default|abstract|static|final|synchronized)\s+)*'
            rf'[\w<>\[\]?,\s]+\s+{re.escape(method_name)}\s*\(',
            re.MULTILINE,
        )
        match = pattern.search(content)
        if not match:
            return None

        return self._extract_method_after(content, match.start(), file_path, role, repo_name,
                                          known_name=method_name)

    def extract_class_skeleton(
        self,
        content: str,
        file_path: str,
        role: str,
        repo_name: str = "",
    ) -> Optional[ClassSkeleton]:
        """Extract class header + autowired fields, stripping all method bodies."""
        m = _CLASS_DECL_RE.search(content)
        if not m:
            return None

        class_name = m.group(1)
        extends    = m.group(2)
        implements = [i.strip() for i in m.group(3).split(",")] if m.group(3) else []

        # Annotations immediately above class declaration
        preamble = content[:m.start()]
        ann_lines = _CLASS_ANN_RE.findall(preamble[-500:])   # look at last 500 chars before class

        # Autowired fields
        fields = [f.strip() for f in _FIELD_RE.findall(content)][:8]

        return ClassSkeleton(
            file_path=file_path,
            class_name=class_name,
            class_annotations=ann_lines[-5:],   # last 5 annotations
            extends=extends,
            implements=implements,
            autowired_fields=fields,
            role=role,
            repo_name=repo_name,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _extract_method_after(
        self,
        content: str,
        start_pos: int,
        file_path: str,
        role: str,
        repo_name: str = "",
        known_name: Optional[str] = None,
    ) -> Optional[MethodExtract]:
        """
        Starting at `start_pos` (could be an annotation or method sig),
        find all annotations, the method signature, and extract the method body
        using brace-balanced walking.
        """
        # Walk back from start_pos to collect any preceding annotations on the same block
        ann_start = self._find_annotation_block_start(content, start_pos)

        # Find the opening brace of the method body
        open_brace = content.find("{", start_pos)
        if open_brace == -1:
            # Interface method (no body) — extract just the signature
            sig_end = content.find(";", start_pos)
            if sig_end == -1:
                return None
            block = content[ann_start:sig_end + 1].strip()
            annotations, signature = self._split_annotations_from_sig(block)
            name = known_name or _extract_method_name(signature)
            if not name:
                return None
            return MethodExtract(
                file_path=file_path, class_name=_class_name_from_path(file_path),
                method_name=name, annotations=annotations, signature=signature,
                body="// interface method — no body", role=role, repo_name=repo_name,
            )

        # Check there's no new method signature between start_pos and open_brace
        # (to avoid capturing the wrong method)
        between = content[start_pos:open_brace]
        if between.count("(") > 2:   # more than 1 method signature worth of parens
            # Skip to the first ( after start_pos and try again
            pass  # let it through — brace walking will handle it

        # Brace-balanced walk
        body_start = open_brace + 1
        depth = 1
        i = body_start
        while i < len(content) and depth > 0:
            ch = content[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            elif ch == '"' or ch == "'":
                # Skip string literals
                quote = ch
                i += 1
                while i < len(content) and content[i] != quote:
                    if content[i] == '\\':
                        i += 1
                    i += 1
            elif content[i:i+2] == "//":
                # Skip line comment
                i = content.find("\n", i)
                if i == -1:
                    break
            i += 1

        body = content[body_start:i - 1].strip()

        # Extract annotations and signature
        header = content[ann_start:open_brace].strip()
        annotations, signature = self._split_annotations_from_sig(header)
        name = known_name or _extract_method_name(signature)

        if not name:
            return None

        # Parse calls from body
        calls_out = self._parse_method_calls_from_body(body)

        # Trim body to max 800 chars — enough for a method, avoids LLM bloat
        body = _trim_body(body, max_chars=800)

        return MethodExtract(
            file_path=file_path,
            class_name=_class_name_from_path(file_path),
            method_name=name,
            annotations=annotations,
            signature=signature,
            body=body,
            role=role,
            repo_name=repo_name,
            calls_out=calls_out,
        )

    def _find_annotation_block_start(self, content: str, pos: int) -> int:
        """
        Walk backwards from pos to include annotation lines immediately above the method.
        Stops at blank lines and non-annotation lines — does NOT cross whitespace gaps,
        so field annotations (@Autowired ...) on previous members are not captured.
        """
        lines = content[:pos].split("\n")
        start = pos
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("@"):
                # Only include pure annotation lines, not @Autowired field declarations
                # (a field declaration has a type and identifier after the annotation)
                is_annotation_only = re.match(r'@\w+(\s*\([^)]*\))?\s*$', stripped) is not None
                if is_annotation_only:
                    start -= len(line) + 1
                    continue
            # Stop at anything else: blank line, code, field declarations
            break
        return max(0, start)

    @staticmethod
    def _split_annotations_from_sig(header: str) -> tuple[list[str], str]:
        """
        Split a method header into standalone annotations and the method signature.

        Standalone annotation: @GetMapping("/path")  — nothing after the annotation
        Parameter annotation:  @RequestParam String period  — has type+name after it

        Only pure annotation lines (matching @Name or @Name(...)) go into annotations.
        Everything else (method return type, name, params) goes into the signature.
        """
        lines = [l.strip() for l in header.strip().split("\n") if l.strip()]
        annotations: list[str] = []
        sig_lines: list[str] = []
        in_sig = False   # once we hit a non-annotation line, the rest is signature

        for line in lines:
            if not in_sig and re.match(r'@\w+(\s*\([^)]*\))?\s*$', line):
                # Pure annotation line — no trailing type / identifier
                annotations.append(line)
            else:
                in_sig = True
                sig_lines.append(line)

        signature = " ".join(sig_lines).strip()
        return annotations, signature

    @staticmethod
    def _parse_method_calls_from_body(body: str) -> list[str]:
        """
        Extract method names called on injected fields.
        e.g. "competitivenessService.getPayerCompetitors(" → "getPayerCompetitors"

        Skips:
        - Calls on utility/framework objects (log, System, Optional, ...)
        - Simple zero-arg property getters: getId(), getName() — no args at all
        - Does NOT skip business methods like getPayerCompetitors(request, viewBy)
          even though they start with "get"

        Uses the open-paren-only pattern (_CALL_RE) so nested calls like
          ResponseEntity.ok(service.doSomething(arg1, arg2))
        match BOTH `ok` and `doSomething` as separate hits.
        """
        _UTILITY_OBJECTS = frozenset({
            "log", "logger", "LOG", "LOGGER",
            "System", "Objects", "Optional", "String",
            "List", "Map", "Set", "Arrays", "Collections", "Stream",
            "ResponseEntity", "CompletableFuture",
            "Math", "Integer", "Long", "Boolean", "Double",
        })

        _SKIP_METHODS = frozenset({
            "toString", "equals", "hashCode", "clone",
            "ok", "noContent", "badRequest", "status", "build",
            "join", "allOf", "supplyAsync",
        })

        calls = []
        for m in _CALL_RE.finditer(body):
            obj, method = m.group(1), m.group(2)

            if obj in _UTILITY_OBJECTS:
                continue
            if method in _SKIP_METHODS:
                continue

            # Detect zero-arg calls: the char immediately after `(` is `)` or whitespace+`)`
            after_open = body[m.end():m.end() + 5].lstrip()
            has_args = after_open and not after_open.startswith(")")

            # Skip simple zero-arg property-style getters (getId(), getName(), etc.)
            # Business methods almost always have at least one argument
            if method.startswith("get") and method[3:4].isupper() and not has_args:
                continue

            calls.append(method)

        return list(dict.fromkeys(calls))  # deduplicate, preserve order

    def _first_significant_method(
        self,
        content: str,
        file_path: str,
        role: str,
        repo_name: str,
    ) -> Optional[MethodExtract]:
        """Fallback: extract the first non-trivial public method."""
        sig_re = re.compile(
            r'(public\s+[\w<>\[\]?,\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[^{]+)?\s*\{)',
            re.MULTILINE,
        )
        for m in sig_re.finditer(content):
            name = m.group(2)
            if name in ("get", "set", "toString", "equals", "hashCode", "clone"):
                continue
            result = self._extract_method_after(content, m.start(), file_path, role, repo_name, known_name=name)
            if result and len(result.body) > 20:
                return result
        return None

    def _extract_query_methods(
        self,
        content: str,
        file_path: str,
        repo_name: str,
    ) -> list[MethodExtract]:
        """Extract all @Query annotated methods from a repository."""
        results = []
        for m in re.finditer(r'@Query\s*\(', content):
            extract = self._extract_method_after(content, m.start(), file_path, "repository", repo_name)
            if extract:
                results.append(extract)
        return results


# ── Module-level helpers ──────────────────────────────────────────────────────

def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.debug("SmartMethodExtractor: could not read file", path=path, error=str(e))
        return None


def _endpoint_suffixes(endpoint: str) -> list[str]:
    """
    Generate progressively shorter trailing suffixes of an endpoint path.
    e.g. "/api/v1/mcheck/niq/competitiveness/summary/competitors/payer"
    → ["/summary/competitors/payer", "/competitors/payer", "/payer", ...]

    The longest suffix that appears in a @Mapping annotation wins.
    """
    parts = [p for p in endpoint.split("/") if p]
    suffixes = []
    for i in range(len(parts)):
        suffix = "/" + "/".join(parts[i:])
        suffixes.append(suffix)
    return suffixes


def _path_matches(ann_path: str, endpoint_suffix: str) -> bool:
    """True if ann_path is equal to or a trailing suffix of endpoint_suffix."""
    # Normalise: strip leading slash for comparison
    ann = ann_path.rstrip("/")
    seg = endpoint_suffix.rstrip("/")
    return ann == seg or seg.endswith(ann) or ann.endswith(seg)


def _extract_method_name(signature: str) -> Optional[str]:
    """Extract the method name from a Java method signature string."""
    m = re.search(r'(\w+)\s*\(', signature)
    return m.group(1) if m else None


def _class_name_from_path(file_path: str) -> str:
    return Path(file_path).stem


def _trim_body(body: str, max_chars: int = 800) -> str:
    """Trim a method body to max_chars, breaking at a sensible line boundary."""
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    cut = truncated.rfind("\n")
    if cut > max_chars // 2:
        return truncated[:cut] + "\n    // ... (truncated)"
    return truncated + "\n    // ... (truncated)"


def _skeleton_to_method_extract(skeleton: ClassSkeleton) -> MethodExtract:
    """Convert a ClassSkeleton to a synthetic MethodExtract for pipeline compatibility."""
    return MethodExtract(
        file_path=skeleton.file_path,
        class_name=skeleton.class_name,
        method_name=f"{skeleton.class_name}__class",
        annotations=skeleton.class_annotations,
        signature=f"class {skeleton.class_name}",
        body="// no handler method found — using class skeleton\n" + "\n".join(skeleton.autowired_fields),
        role=skeleton.role,
        repo_name=skeleton.repo_name,
    )
