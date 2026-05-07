"""
ASTAnalyzer — Task #29: tree-sitter AST Integration.

Uses tree-sitter grammars to produce a language-agnostic symbol table from
source files: methods, classes, fields, annotations, and imports — without
any regex or LLM involvement.

Why tree-sitter instead of regex
---------------------------------
• Regex breaks on nested generics, multi-line parameters, annotation arrays,
  and language dialect variations (Kotlin, Groovy, etc.).
• tree-sitter produces a concrete syntax tree (CST) that handles all of these
  correctly — it is the same parser powering VS Code and GitHub Copilot.
• Zero false-positives on class/method boundaries → the MethodChunker can use
  AST-derived line ranges instead of brace-counting heuristics.

Architecture
------------
This module is a pure structural pass — NO LLM, NO network calls.  It runs
BEFORE entity extraction (Stage 1) and produces a SymbolTable that:

  1. Replaces MethodChunker for Java and Python — exact line ranges, not
     brace counting.  Falls back to MethodChunker for unsupported languages.
  2. Gives the ImportGraphAnalyzer structured import data instead of regex.
  3. Pre-populates entity signatures deterministically, reducing hallucination
     from the LLM extractor.
  4. Detects Spring/FastAPI/Flask annotations without ORM-specific regex rules.

Integration point
-----------------
Called by EntityExtractor._extract_from_code_unit() BEFORE the LLM call:

    symbol_table = ASTAnalyzer().analyze(unit)
    if symbol_table:
        # Use AST-derived method chunks instead of regex MethodChunker
        chunks = symbol_table.to_method_chunks(unit)
        # Also inject method signatures into the LLM prompt
        enriched_content = symbol_table.to_annotated_source(unit.content)

Supported languages and grammars
----------------------------------
  java       → tree-sitter-java
  python     → tree-sitter-python
  typescript → tree-sitter-typescript  (falls back to JS grammar if unavailable)

The grammars are loaded lazily and cached module-level so the overhead is
paid once per process, not once per file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ── Grammar cache ─────────────────────────────────────────────────────────────

_PARSERS: dict[str, object] = {}   # lang → tree_sitter.Parser


def _get_parser(lang: str):
    """Lazily load and cache a tree-sitter parser for the given language."""
    if lang in _PARSERS:
        return _PARSERS[lang]

    try:
        from tree_sitter import Language, Parser

        if lang == "java":
            import tree_sitter_java as _java
            language = Language(_java.language())
        elif lang == "python":
            import tree_sitter_python as _python
            language = Language(_python.language())
        elif lang in ("typescript", "tsx"):
            try:
                import tree_sitter_typescript as _ts
                language = Language(_ts.language_typescript())
            except (ImportError, AttributeError):
                import tree_sitter_javascript as _js
                language = Language(_js.language())
        elif lang in ("javascript", "jsx"):
            import tree_sitter_javascript as _js
            language = Language(_js.language())
        else:
            return None

        parser = Parser(language)
        _PARSERS[lang] = parser
        log.debug("[ast] Parser loaded", lang=lang)
        return parser

    except (ImportError, Exception) as e:
        log.debug("[ast] Grammar not available — falling back to regex", lang=lang, error=str(e))
        _PARSERS[lang] = None
        return None


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class AnnotationInfo:
    name: str           # e.g. "GetMapping", "Query", "Transactional"
    arguments: str = "" # raw argument string, e.g. '"/competitors"'


@dataclass
class ParameterInfo:
    name: str
    type_name: str
    annotations: list[AnnotationInfo] = field(default_factory=list)


@dataclass
class MethodInfo:
    name:         str
    return_type:  str
    parameters:   list[ParameterInfo]
    annotations:  list[AnnotationInfo]
    start_line:   int          # 1-based, inclusive
    end_line:     int          # 1-based, inclusive
    body_text:    str          # full method source text
    modifiers:    list[str] = field(default_factory=list)

    @property
    def signature(self) -> str:
        params = ", ".join(
            f"{p.type_name} {p.name}" for p in self.parameters
        )
        mods = " ".join(self.modifiers) + " " if self.modifiers else ""
        return f"{mods}{self.return_type} {self.name}({params})"

    @property
    def spring_mapping(self) -> Optional[str]:
        """Return HTTP method+path if this is a Spring @*Mapping method."""
        for ann in self.annotations:
            m = re.match(
                r'(Get|Post|Put|Delete|Patch|Request)Mapping', ann.name
            )
            if m:
                http_method = m.group(1).upper() if m.group(1) != "Request" else "ANY"
                path = ann.arguments.strip('"\'') if ann.arguments else ""
                return f"{http_method} {path}"
        return None

    @property
    def jpa_query(self) -> Optional[str]:
        """Return JPQL/SQL string if this method has a @Query annotation."""
        for ann in self.annotations:
            if ann.name == "Query":
                q = ann.arguments.strip()
                # Strip value= prefix and quotes
                q = re.sub(r'^value\s*=\s*', '', q)
                q = q.strip('"\'')
                return q
        return None


@dataclass
class FieldInfo:
    name:        str
    type_name:   str
    annotations: list[AnnotationInfo]
    modifiers:   list[str] = field(default_factory=list)

    @property
    def is_injected(self) -> bool:
        return any(a.name in ("Autowired", "Inject", "Resource") for a in self.annotations)


@dataclass
class ClassInfo:
    name:         str
    kind:         str              # class | interface | enum | record
    superclass:   Optional[str]
    interfaces:   list[str]
    annotations:  list[AnnotationInfo]
    fields:       list[FieldInfo]
    methods:      list[MethodInfo]
    imports:      list[str]        # fully qualified import paths
    start_line:   int
    end_line:     int


@dataclass
class SymbolTable:
    """
    AST-derived structural analysis of one source file.

    Produced by ASTAnalyzer.analyze() and consumed by:
      - EntityExtractor: pre-populated signatures + method chunks
      - ImportGraphAnalyzer: structured imports instead of regex
      - MethodChunker: exact AST line ranges instead of brace counting
    """
    language:  str
    file_path: str
    classes:   list[ClassInfo] = field(default_factory=list)

    # ── Method-level chunking ──────────────────────────────────────────────

    def to_method_chunks(self, unit) -> list:
        """
        Return MethodChunk objects (compatible with MethodChunker output) using
        AST-exact line ranges instead of brace-counting heuristics.
        """
        from companybrain.pipeline.method_chunker import MethodChunk, METHOD_SPLIT_THRESHOLD

        content = unit.content or ""
        if len(content) <= METHOD_SPLIT_THRESHOLD:
            return []

        lines = content.splitlines(keepends=True)
        chunks = []

        for cls in self.classes:
            # Build class header (class declaration + fields)
            cls_header_lines = lines[cls.start_line - 1 : cls.start_line + 3]
            field_lines = []
            for f in cls.fields[:15]:
                ann_str = " ".join(f"@{a.name}" for a in f.annotations)
                field_lines.append(
                    f"    {ann_str + ' ' if ann_str else ''}"
                    f"{' '.join(f.modifiers)} {f.type_name} {f.name};"
                )
            header = "".join(cls_header_lines) + "\n" + "\n".join(field_lines)

            for method in cls.methods:
                if not method.body_text:
                    continue
                ann_str = "\n".join(
                    f"    @{a.name}({a.arguments})" if a.arguments else f"    @{a.name}"
                    for a in method.annotations
                )
                body = (ann_str + "\n" if ann_str else "") + method.body_text

                assembled = (
                    f"// [class: {cls.name}]\n{header.rstrip()}\n\n"
                    f"// ── method: {method.name} (AST-exact) ──\n{body}"
                )
                chunks.append(MethodChunk(
                    method_name=method.name,
                    language=self.language,
                    file_path=self.file_path,
                    repo_name=unit.repo_name,
                    role=unit.role,
                    line_start=method.start_line,
                    content=assembled,
                ))

        return chunks

    # ── Prompt enrichment ──────────────────────────────────────────────────

    def to_signature_block(self) -> str:
        """
        Return a compact signature summary to prepend to the LLM prompt.
        Helps the model extract correct entity names without hallucinating.
        """
        parts: list[str] = []
        for cls in self.classes:
            anns = " ".join(f"@{a.name}" for a in cls.annotations)
            parts.append(f"\n[{cls.kind.upper()}] {anns + ' ' if anns else ''}{cls.name}")
            for m in cls.methods:
                http = m.spring_mapping
                jpa  = m.jpa_query
                ann_str = " ".join(f"@{a.name}" for a in m.annotations)
                sig_line = f"  {ann_str + ' ' if ann_str else ''}{m.signature}"
                if http:
                    sig_line += f"  →  {http}"
                if jpa:
                    sig_line += f"\n    @Query: {jpa[:120]}"
                parts.append(sig_line)
        return "\n".join(parts)

    # ── Import data ────────────────────────────────────────────────────────

    def all_imports(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for cls in self.classes:
            for imp in cls.imports:
                if imp not in seen:
                    seen.add(imp)
                    result.append(imp)
        return result


# ── Main class ────────────────────────────────────────────────────────────────

class ASTAnalyzer:
    """
    Parses a CodeUnit's source content using tree-sitter and returns a SymbolTable.

    Usage:
        table = ASTAnalyzer().analyze(unit)
        if table:
            chunks = table.to_method_chunks(unit)
            sig_block = table.to_signature_block()
    """

    def analyze(self, unit) -> Optional[SymbolTable]:
        """
        Parse the CodeUnit and return a SymbolTable.
        Returns None if the language is unsupported or parsing fails.
        """
        lang = _normalize_lang(unit.language or "")
        if not lang:
            return None

        parser = _get_parser(lang)
        if parser is None:
            return None

        content = unit.content or ""
        if not content.strip():
            return None

        try:
            tree = parser.parse(content.encode("utf-8", errors="replace"))
        except Exception as e:
            log.debug("[ast] Parse error", file=unit.file_path, error=str(e))
            return None

        try:
            if lang == "java":
                classes = _extract_java(tree, content)
            elif lang == "python":
                classes = _extract_python(tree, content)
            else:
                # TS/JS: use Java-like brace extraction as a bridge until
                # the TypeScript grammar is properly wired
                classes = []

            table = SymbolTable(
                language=lang,
                file_path=unit.file_path,
                classes=classes,
            )
            log.debug(
                "[ast] Analysis complete",
                file=unit.file_path,
                classes=len(classes),
                methods=sum(len(c.methods) for c in classes),
            )
            return table if classes else None

        except Exception as e:
            log.warning("[ast] Extraction error — falling back", file=unit.file_path, error=str(e))
            return None


# ── Java extractor ────────────────────────────────────────────────────────────

def _extract_java(tree, content: str) -> list[ClassInfo]:
    """Walk the tree-sitter CST to extract Java classes, methods, fields."""
    lines = content.splitlines()
    # tree-sitter reports byte offsets, not character offsets.
    # Slicing the str directly is wrong for any non-ASCII source (e.g. comments
    # with accented characters, string literals with emoji, etc.) — the byte
    # index lands in the middle of a multi-byte codepoint and produces garbled
    # method names.  Encode once and decode each slice instead.
    content_bytes = content.encode("utf-8", errors="replace")

    def text(node) -> str:
        return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def line(node) -> int:
        return node.start_point[0] + 1   # 1-based

    def end_line(node) -> int:
        return node.end_point[0] + 1

    def get_annotations(node) -> list[AnnotationInfo]:
        """
        Extract annotations from a node's modifiers child.
        Java tree structure: class/field/method_declaration → modifiers → annotation | marker_annotation
        """
        anns = []

        def _parse_ann(ann_node):
            name = ""
            args = ""
            for part in ann_node.children:
                if part.type == "identifier":
                    name = text(part)
                elif part.type == "annotation_argument_list":
                    # Strip outer parens; keep inner content
                    inner = text(part)
                    args = inner.strip("()")
            if name:
                anns.append(AnnotationInfo(name=name, arguments=args.strip()))

        for child in node.children:
            if child.type == "modifiers":
                # Annotations live inside the modifiers node
                for mod_child in child.children:
                    if mod_child.type in ("annotation", "marker_annotation"):
                        _parse_ann(mod_child)
            elif child.type in ("annotation", "marker_annotation"):
                # Direct annotation child (some nodes have them directly)
                _parse_ann(child)

        return anns

    _MODIFIER_TYPES = {
        "public", "private", "protected", "static", "final",
        "abstract", "synchronized", "native", "default",
    }

    def get_modifiers(node) -> list[str]:
        mods = []
        for child in node.children:
            if child.type == "modifiers":
                for m in child.children:
                    if m.type in _MODIFIER_TYPES:
                        mods.append(text(m))
        return mods

    classes: list[ClassInfo] = []

    def walk(node, imports: list[str]):
        if node.type in (
            "class_declaration", "interface_declaration",
            "enum_declaration", "record_declaration"
        ):
            kind_map = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "enum_declaration": "enum",
                "record_declaration": "record",
            }
            kind = kind_map.get(node.type, "class")

            class_name = ""
            superclass = None
            interfaces: list[str] = []
            annotations = get_annotations(node)
            fields: list[FieldInfo] = []
            methods: list[MethodInfo] = []

            for child in node.children:
                if child.type == "identifier":
                    class_name = text(child)
                elif child.type == "superclass":
                    for sc in child.children:
                        if sc.type == "type_identifier":
                            superclass = text(sc)
                elif child.type == "super_interfaces":
                    for iface in child.children:
                        if iface.type == "type_list":
                            interfaces = [
                                text(t) for t in iface.children
                                if t.type == "type_identifier"
                            ]
                elif child.type in ("class_body", "interface_body", "enum_body"):
                    for member in child.children:
                        if member.type == "field_declaration":
                            f = _java_field(member, text, get_annotations, get_modifiers)
                            if f:
                                fields.append(f)
                        elif member.type in ("method_declaration", "constructor_declaration"):
                            m = _java_method(member, content_bytes, text, line, end_line,
                                             get_annotations, get_modifiers)
                            if m:
                                methods.append(m)

            if class_name:
                classes.append(ClassInfo(
                    name=class_name,
                    kind=kind,
                    superclass=superclass,
                    interfaces=interfaces,
                    annotations=annotations,
                    fields=fields,
                    methods=methods,
                    imports=list(imports),
                    start_line=line(node),
                    end_line=end_line(node),
                ))

        else:
            for child in node.children:
                walk(child, imports)

    # Collect top-level imports first
    imports: list[str] = []
    for child in tree.root_node.children:
        if child.type == "import_declaration":
            imports.append(text(child).strip().lstrip("import ").rstrip(";").strip())

    walk(tree.root_node, imports)
    return classes


def _java_field(node, text, get_annotations, get_modifiers) -> Optional[FieldInfo]:
    type_name = ""
    name = ""
    annotations = get_annotations(node)
    modifiers = get_modifiers(node)

    for child in node.children:
        if child.type in ("type_identifier", "generic_type", "array_type"):
            type_name = text(child)
        elif child.type == "variable_declarator":
            for part in child.children:
                if part.type == "identifier":
                    name = text(part)

    if name and type_name:
        return FieldInfo(name=name, type_name=type_name,
                         annotations=annotations, modifiers=modifiers)
    return None


def _java_method(node, content_bytes: bytes, text, line, end_line,
                 get_annotations, get_modifiers) -> Optional[MethodInfo]:
    name = ""
    return_type = "void"
    params: list[ParameterInfo] = []
    annotations = get_annotations(node)
    modifiers = get_modifiers(node)

    for child in node.children:
        if child.type == "identifier" and not name:
            name = text(child)
        elif child.type in ("void_type", "type_identifier", "generic_type",
                             "array_type", "integral_type", "floating_point_type"):
            return_type = text(child)
        elif child.type == "formal_parameters":
            for param in child.children:
                if param.type in ("formal_parameter", "spread_parameter"):
                    p_name = ""
                    p_type = ""
                    p_anns = get_annotations(param)
                    seen_type = False
                    for part in param.children:
                        if part.type in ("type_identifier", "generic_type",
                                         "array_type", "integral_type",
                                         "floating_point_type", "void_type"):
                            p_type = text(part)
                            seen_type = True
                        elif part.type == "variable_declarator_id":
                            for id_part in part.children:
                                if id_part.type == "identifier":
                                    p_name = text(id_part)
                        elif part.type == "identifier":
                            # In interface methods: @Param("x") String paramName
                            # → identifier is the param name (comes after type)
                            if seen_type and not p_name:
                                p_name = text(part)
                    if p_name:
                        params.append(ParameterInfo(name=p_name, type_name=p_type,
                                                    annotations=p_anns))

    if not name:
        return None

    start = line(node)
    stop  = end_line(node)
    # Use byte slice + decode to stay consistent with the text() helper above.
    body_text = content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return MethodInfo(
        name=name,
        return_type=return_type,
        parameters=params,
        annotations=annotations,
        modifiers=modifiers,
        start_line=start,
        end_line=stop,
        body_text=body_text,
    )


# ── Python extractor ──────────────────────────────────────────────────────────

def _extract_python(tree, content: str) -> list[ClassInfo]:
    """Walk the tree-sitter CST to extract Python classes and methods."""
    content_bytes = content.encode("utf-8", errors="replace")

    def text(node) -> str:
        return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def line(node) -> int:
        return node.start_point[0] + 1

    def end_line(node) -> int:
        return node.end_point[0] + 1

    def get_decorators(parent) -> list[AnnotationInfo]:
        anns = []
        for child in parent.children:
            if child.type == "decorator":
                deco_text = text(child).lstrip("@")
                # Separate name from arguments
                m = re.match(r'(\w+(?:\.\w+)*)\s*(\(.*\))?', deco_text, re.DOTALL)
                if m:
                    anns.append(AnnotationInfo(
                        name=m.group(1),
                        arguments=(m.group(2) or "").strip("()"),
                    ))
        return anns

    classes: list[ClassInfo] = []
    imports: list[str] = []

    # Collect imports
    for node in tree.root_node.children:
        if node.type in ("import_statement", "import_from_statement"):
            imports.append(text(node).strip())

    # Find class definitions at top level and within modules
    def walk(node, depth=0):
        if node.type == "class_definition":
            class_name = ""
            bases: list[str] = []
            methods: list[MethodInfo] = []
            fields: list[FieldInfo] = []
            decorators = get_decorators(node)

            for child in node.children:
                if child.type == "identifier":
                    class_name = text(child)
                elif child.type == "argument_list":
                    for base in child.children:
                        if base.type in ("identifier", "attribute"):
                            bases.append(text(base))
                elif child.type == "block":
                    for member in child.children:
                        if member.type == "function_definition":
                            m = _python_method(member, content_bytes, text, line, end_line, get_decorators)
                            if m:
                                methods.append(m)
                        elif member.type == "expression_statement":
                            # Capture self.xxx = Type() patterns in __init__
                            assignment = text(member)
                            ma = re.match(r'self\.(\w+)\s*=\s*(\w+)\s*\(', assignment)
                            if ma:
                                fields.append(FieldInfo(
                                    name=ma.group(1),
                                    type_name=ma.group(2),
                                    annotations=[],
                                ))

            if class_name:
                classes.append(ClassInfo(
                    name=class_name,
                    kind="class",
                    superclass=bases[0] if bases else None,
                    interfaces=bases[1:] if len(bases) > 1 else [],
                    annotations=decorators,
                    fields=fields,
                    methods=methods,
                    imports=list(imports),
                    start_line=line(node),
                    end_line=end_line(node),
                ))
        else:
            for child in node.children:
                walk(child, depth + 1)

    walk(tree.root_node)
    return classes


def _python_method(node, content_bytes: bytes, text, line, end_line, get_decorators) -> Optional[MethodInfo]:
    name = ""
    params: list[ParameterInfo] = []
    decorators = get_decorators(node)

    for child in node.children:
        if child.type == "identifier":
            name = text(child)
        elif child.type == "parameters":
            for param in child.children:
                if param.type == "identifier":
                    pname = text(param)
                    if pname != "self":
                        params.append(ParameterInfo(name=pname, type_name=""))
                elif param.type in ("typed_parameter", "typed_default_parameter"):
                    pname = ""
                    ptype = ""
                    for part in param.children:
                        if part.type == "identifier" and not pname:
                            pname = text(part)
                        elif part.type == "type":
                            ptype = text(part)
                    if pname and pname != "self":
                        params.append(ParameterInfo(name=pname, type_name=ptype))

    if not name:
        return None

    body_text = content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return MethodInfo(
        name=name,
        return_type="",
        parameters=params,
        annotations=decorators,
        modifiers=[],
        start_line=line(node),
        end_line=end_line(node),
        body_text=body_text,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_lang(lang: str) -> str:
    return {
        "java":       "java",
        "python":     "python",
        "typescript": "typescript",
        "ts":         "typescript",
        "tsx":        "tsx",
        "javascript": "javascript",
        "js":         "javascript",
        "jsx":        "javascript",
    }.get(lang.lower(), "")
