# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/parser.py
#
# Key changes from the original:
#   - Removed SQLite store dependency; emits plain NodeInfo / EdgeInfo records
#     that callers persist to our Postgres schema (nodes + edges tables).
#   - Qualified-name scheme preserved verbatim: path/to/file.py::Class.method
#   - Grammar loading tries tree-sitter-languages (bundled) first, then falls
#     back to individual tree-sitter-* packages, then returns an empty result.
#   - Language coverage scoped to: Java, TypeScript, TSX, JavaScript, Python, Go.
#     (Other languages from CRG's full map can be added incrementally.)
#   - No notebook / Jupyter / Vue / Svelte / Solidity support in this port.
"""Tree-sitter multi-language structural parser for company-brain.

For every source file it produces:
  - NodeInfo records  → maps to rows in `nodes` (with qualified_name, line_start, …)
  - EdgeInfo records  → maps to rows in `edges` (CALLS, IMPORTS_FROM, INHERITS, …)

Usage::

    from companybrain.structural.parser import parse_file, parse_directory

    # Single file
    result = parse_file("/path/to/MyService.java", repo_root="/path/to/repo")

    # Whole directory tree
    results = parse_directory("/path/to/repo")
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class NodeInfo:
    """Structural entity extracted from source.

    Maps to a row in the ``nodes`` table.  The caller is responsible for
    the UUID primary key; ``qualified_name`` is the stable structural identity.
    """

    kind: str           # 'File' | 'Class' | 'Function' | 'Test' | 'Type'
    name: str           # Simple name: "chargePayment"
    qualified_name: str # Full path: "backend/src/Payment.java::PaymentService.chargePayment"
    file_path: str      # Repo-relative path: "backend/src/Payment.java"
    line_start: int
    line_end: int
    file_hash: str      # SHA-256 of the file bytes
    language: str = ""
    parent_name: Optional[str] = None   # enclosing class or module
    is_test: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class EdgeInfo:
    """Structural relationship between two entities.

    Maps to a row in the ``edges`` table.
    ``source`` and ``target`` are qualified names.
    """

    kind: str           # 'CALLS' | 'IMPORTS_FROM' | 'INHERITS' | 'IMPLEMENTS' | 'CONTAINS' | 'TESTED_BY'
    source: str         # qualified_name of the calling / importing entity
    target: str         # qualified_name of the called / imported entity
    file_path: str
    line: int = 0


@dataclass
class ParseResult:
    """All structural entities extracted from one file."""

    file_path: str
    file_hash: str
    language: str
    nodes: list[NodeInfo] = field(default_factory=list)
    edges: list[EdgeInfo] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Language ↔ file extension mapping
# Subset of CRG's full EXTENSION_TO_LANGUAGE map; covers our current stack.
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".java": "java",
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".go":   "go",
}

# ---------------------------------------------------------------------------
# Tree-sitter node type maps (language → list of TS AST node type strings)
# Ported from CRG's _CLASS_TYPES / _FUNCTION_TYPES / _IMPORT_TYPES.
# ---------------------------------------------------------------------------

_CLASS_TYPES: dict[str, list[str]] = {
    "java":       ["class_declaration", "interface_declaration", "enum_declaration"],
    "python":     ["class_definition"],
    "javascript": ["class_declaration", "class"],
    "typescript": ["class_declaration", "class"],
    "tsx":        ["class_declaration", "class"],
    "go":         ["type_declaration"],
}

_FUNCTION_TYPES: dict[str, list[str]] = {
    "java":       ["method_declaration", "constructor_declaration"],
    "python":     ["function_definition"],
    "javascript": ["function_declaration", "method_definition", "arrow_function"],
    "typescript": ["function_declaration", "method_definition", "arrow_function"],
    "tsx":        ["function_declaration", "method_definition", "arrow_function"],
    "go":         ["function_declaration", "method_declaration"],
}

_IMPORT_TYPES: dict[str, list[str]] = {
    "java":       ["import_declaration"],
    "python":     ["import_statement", "import_from_statement"],
    "javascript": ["import_statement"],
    "typescript": ["import_statement"],
    "tsx":        ["import_statement"],
    "go":         ["import_declaration"],
}

_CALL_TYPES: dict[str, list[str]] = {
    "java":       ["method_invocation", "object_creation_expression"],
    "python":     ["call"],
    "javascript": ["call_expression", "new_expression"],
    "typescript": ["call_expression", "new_expression"],
    "tsx":        ["call_expression", "new_expression"],
    "go":         ["call_expression"],
}

# Test-file patterns — paths matching these are treated as test nodes.
_TEST_PATH_RE = re.compile(
    r"(?:test|tests|spec|specs|__tests__|Test|Tests|Spec)s?[/\\]"
    r"|[/\\](?:test|spec|it)_|_(?:test|spec|it)\.",
    re.IGNORECASE,
)
_TEST_NAME_RE = re.compile(r"^(?:test|it|describe|expect|assert)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Grammar loader — tries bundled tree-sitter-languages first
# ---------------------------------------------------------------------------

def _load_grammar(language: str):
    """Return a tree-sitter Language object for the given language string.

    Strategy:
      1. Try tree_sitter_languages (bundled pack — installed as ADR-006 §2).
      2. Try individual tree_sitter_<lang> packages.
      3. Return None on failure (parser skips the file gracefully).
    """
    # 1. Bundled grammar pack
    try:
        from tree_sitter_languages import get_language  # type: ignore[import]
        return get_language(language)
    except Exception:
        pass

    # 2. Individual packages for the languages we know about
    try:
        import importlib
        lang_module = importlib.import_module(f"tree_sitter_{language}")
        from tree_sitter import Language  # type: ignore[import]
        return Language(lang_module.language())
    except Exception:
        pass

    # 3. tsx falls back to typescript grammar if tsx not available
    if language == "tsx":
        return _load_grammar("typescript")

    return None


def _make_parser(language: str):
    """Return a (tree_sitter.Parser, language_name) pair or (None, language)."""
    try:
        from tree_sitter import Parser  # type: ignore[import]
    except ImportError:
        log.warning("tree-sitter not installed; structural parsing unavailable")
        return None, language

    lang_obj = _load_grammar(language)
    if lang_obj is None:
        log.debug("No grammar available for language=%s; skipping", language)
        return None, language

    try:
        parser = Parser()
        parser.set_language(lang_obj)
        return parser, language
    except Exception as exc:
        log.debug("Failed to initialise parser for %s: %s", language, exc)
        return None, language


# ---------------------------------------------------------------------------
# Qualified-name helpers
# ---------------------------------------------------------------------------

def _qualified_name(file_path: str, class_name: Optional[str], func_name: str) -> str:
    """Build CRG-compatible qualified name: path/to/file.py::Class.method"""
    base = file_path.lstrip("/")
    if class_name:
        return f"{base}::{class_name}.{func_name}"
    return f"{base}::{func_name}"


def _file_qname(file_path: str) -> str:
    return file_path.lstrip("/")


# ---------------------------------------------------------------------------
# Node text extraction helpers
# ---------------------------------------------------------------------------

def _node_text(ts_node, source_bytes: bytes) -> str:
    return source_bytes[ts_node.start_byte:ts_node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(ts_node, *types: str):
    for child in ts_node.children:
        if child.type in types:
            return child
    return None


def _find_identifier(ts_node, source_bytes: bytes) -> str:
    """Extract the primary identifier (name) from a TS node."""
    for child in ts_node.children:
        if child.type in ("identifier", "type_identifier", "field_identifier",
                          "property_identifier"):
            return _node_text(child, source_bytes)
    # Fallback: first named leaf
    if ts_node.child_count:
        return _node_text(ts_node.children[0], source_bytes)
    return "<unknown>"


# ---------------------------------------------------------------------------
# Per-language extraction
# ---------------------------------------------------------------------------

def _extract_java(
    tree,
    source_bytes: bytes,
    file_path: str,
    file_hash: str,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Walk a Java AST and extract classes, methods, and relationships."""
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []

    class_types = set(_CLASS_TYPES["java"])
    func_types  = set(_FUNCTION_TYPES["java"])
    import_types = set(_IMPORT_TYPES["java"])
    call_types  = set(_CALL_TYPES["java"])

    # File node
    file_node_qname = _file_qname(file_path)
    nodes.append(NodeInfo(
        kind="File", name=Path(file_path).name,
        qualified_name=file_node_qname,
        file_path=file_path,
        line_start=1, line_end=tree.root_node.end_point[0] + 1,
        file_hash=file_hash, language="java",
    ))

    # BFS over AST
    stack = [(tree.root_node, None)]  # (ts_node, enclosing_class_name)
    while stack:
        ts_node, enclosing_class = stack.pop()

        if ts_node.type in import_types:
            # import com.example.PaymentService;
            raw = _node_text(ts_node, source_bytes).strip().rstrip(";")
            target = raw.replace("import ", "").strip()
            edges.append(EdgeInfo(
                kind="IMPORTS_FROM",
                source=file_node_qname,
                target=target,
                file_path=file_path,
                line=ts_node.start_point[0] + 1,
            ))
            continue

        if ts_node.type in class_types:
            name_node = _find_child_by_type(ts_node, "identifier", "type_identifier")
            class_name = _node_text(name_node, source_bytes) if name_node else "<anon>"
            qname = f"{file_path.lstrip('/')}::{class_name}"
            nodes.append(NodeInfo(
                kind="Class", name=class_name,
                qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language="java",
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS", source=file_node_qname, target=qname,
                file_path=file_path, line=ts_node.start_point[0] + 1,
            ))
            # Handle inheritance (extends / implements)
            for child in ts_node.children:
                if child.type in ("superclass", "super_interfaces"):
                    parent_name = _node_text(child, source_bytes).replace("extends", "").replace("implements", "").strip()
                    rel_kind = "INHERITS" if child.type == "superclass" else "IMPLEMENTS"
                    edges.append(EdgeInfo(
                        kind=rel_kind, source=qname, target=parent_name,
                        file_path=file_path, line=child.start_point[0] + 1,
                    ))
            # Push children with updated class context
            for child in ts_node.children:
                stack.append((child, class_name))
            continue

        if ts_node.type in func_types:
            name_node = _find_child_by_type(ts_node, "identifier")
            func_name = _node_text(name_node, source_bytes) if name_node else "<anon>"
            qname = _qualified_name(file_path, enclosing_class, func_name)
            is_test = (
                _TEST_PATH_RE.search(file_path) is not None
                or _TEST_NAME_RE.match(func_name) is not None
                or any(
                    "@Test" in _node_text(c, source_bytes)
                    for c in ts_node.children if c.type in ("modifiers",)
                )
            )
            nodes.append(NodeInfo(
                kind="Test" if is_test else "Function",
                name=func_name,
                qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language="java",
                parent_name=enclosing_class,
                is_test=is_test,
            ))
            if enclosing_class:
                class_qname = f"{file_path.lstrip('/')}::{enclosing_class}"
                edges.append(EdgeInfo(
                    kind="CONTAINS", source=class_qname, target=qname,
                    file_path=file_path, line=ts_node.start_point[0] + 1,
                ))
            # Calls inside the method body
            for call in _collect_descendants(ts_node, call_types):
                callee_node = _find_child_by_type(call, "identifier")
                if callee_node:
                    callee_name = _node_text(callee_node, source_bytes)
                    edges.append(EdgeInfo(
                        kind="CALLS", source=qname, target=callee_name,
                        file_path=file_path, line=call.start_point[0] + 1,
                    ))
            continue

        for child in ts_node.children:
            stack.append((child, enclosing_class))

    return nodes, edges


def _extract_python(
    tree,
    source_bytes: bytes,
    file_path: str,
    file_hash: str,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Walk a Python AST and extract classes, functions, and relationships."""
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []

    class_types  = set(_CLASS_TYPES["python"])
    func_types   = set(_FUNCTION_TYPES["python"])
    import_types = set(_IMPORT_TYPES["python"])
    call_types   = set(_CALL_TYPES["python"])

    file_qname = _file_qname(file_path)
    nodes.append(NodeInfo(
        kind="File", name=Path(file_path).name,
        qualified_name=file_qname,
        file_path=file_path,
        line_start=1, line_end=tree.root_node.end_point[0] + 1,
        file_hash=file_hash, language="python",
    ))

    stack = [(tree.root_node, None)]
    while stack:
        ts_node, enclosing_class = stack.pop()

        if ts_node.type in import_types:
            raw = _node_text(ts_node, source_bytes).strip()
            # Normalise: "from foo import bar" → "foo", "import os.path" → "os.path"
            if raw.startswith("from "):
                parts = raw.split()
                target = parts[1] if len(parts) > 1 else raw
            else:
                parts = raw.replace("import ", "").split(",")
                target = parts[0].strip()
            edges.append(EdgeInfo(
                kind="IMPORTS_FROM", source=file_qname, target=target,
                file_path=file_path, line=ts_node.start_point[0] + 1,
            ))
            continue

        if ts_node.type in class_types:
            name_node = _find_child_by_type(ts_node, "identifier")
            class_name = _node_text(name_node, source_bytes) if name_node else "<anon>"
            qname = f"{file_path.lstrip('/')}::{class_name}"
            nodes.append(NodeInfo(
                kind="Class", name=class_name, qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language="python",
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS", source=file_qname, target=qname,
                file_path=file_path, line=ts_node.start_point[0] + 1,
            ))
            # Inheritance
            arg_list = _find_child_by_type(ts_node, "argument_list")
            if arg_list:
                for base in arg_list.children:
                    if base.type == "identifier":
                        edges.append(EdgeInfo(
                            kind="INHERITS", source=qname,
                            target=_node_text(base, source_bytes),
                            file_path=file_path, line=base.start_point[0] + 1,
                        ))
            for child in ts_node.children:
                stack.append((child, class_name))
            continue

        if ts_node.type in func_types:
            name_node = _find_child_by_type(ts_node, "identifier")
            func_name = _node_text(name_node, source_bytes) if name_node else "<anon>"
            qname = _qualified_name(file_path, enclosing_class, func_name)
            is_test = (
                _TEST_PATH_RE.search(file_path) is not None
                or func_name.startswith("test_")
                or func_name.startswith("Test")
            )
            nodes.append(NodeInfo(
                kind="Test" if is_test else "Function",
                name=func_name, qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language="python",
                parent_name=enclosing_class,
                is_test=is_test,
            ))
            if enclosing_class:
                class_qname = f"{file_path.lstrip('/')}::{enclosing_class}"
                edges.append(EdgeInfo(
                    kind="CONTAINS", source=class_qname, target=qname,
                    file_path=file_path, line=ts_node.start_point[0] + 1,
                ))
            for call in _collect_descendants(ts_node, call_types):
                fn_node = call.children[0] if call.children else None
                if fn_node:
                    # Handle attribute access: obj.method(...)
                    if fn_node.type == "attribute":
                        attr = _find_child_by_type(fn_node, "identifier")
                        callee = _node_text(attr, source_bytes) if attr else _node_text(fn_node, source_bytes)
                    else:
                        callee = _node_text(fn_node, source_bytes)
                    edges.append(EdgeInfo(
                        kind="CALLS", source=qname, target=callee,
                        file_path=file_path, line=call.start_point[0] + 1,
                    ))
            continue

        for child in ts_node.children:
            stack.append((child, enclosing_class))

    return nodes, edges


def _extract_typescript(
    tree,
    source_bytes: bytes,
    file_path: str,
    file_hash: str,
    language: str = "typescript",
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Walk a TypeScript/TSX/JS AST and extract classes, functions, imports."""
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []

    class_types  = set(_CLASS_TYPES.get(language, _CLASS_TYPES["typescript"]))
    func_types   = set(_FUNCTION_TYPES.get(language, _FUNCTION_TYPES["typescript"]))
    import_types = set(_IMPORT_TYPES.get(language, _IMPORT_TYPES["typescript"]))
    call_types   = set(_CALL_TYPES.get(language, _CALL_TYPES["typescript"]))

    file_qname = _file_qname(file_path)
    nodes.append(NodeInfo(
        kind="File", name=Path(file_path).name,
        qualified_name=file_qname,
        file_path=file_path,
        line_start=1, line_end=tree.root_node.end_point[0] + 1,
        file_hash=file_hash, language=language,
    ))

    stack = [(tree.root_node, None)]
    while stack:
        ts_node, enclosing_class = stack.pop()

        if ts_node.type in import_types:
            # import { foo } from './foo'
            source_str_node = None
            for child in ts_node.children:
                if child.type == "string":
                    source_str_node = child
            if source_str_node:
                raw = _node_text(source_str_node, source_bytes).strip("'\"` ")
                edges.append(EdgeInfo(
                    kind="IMPORTS_FROM", source=file_qname, target=raw,
                    file_path=file_path, line=ts_node.start_point[0] + 1,
                ))
            continue

        if ts_node.type in class_types:
            name_node = _find_child_by_type(ts_node, "identifier", "type_identifier")
            class_name = _node_text(name_node, source_bytes) if name_node else "<anon>"
            qname = f"{file_path.lstrip('/')}::{class_name}"
            nodes.append(NodeInfo(
                kind="Class", name=class_name, qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language=language,
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS", source=file_qname, target=qname,
                file_path=file_path, line=ts_node.start_point[0] + 1,
            ))
            for child in ts_node.children:
                stack.append((child, class_name))
            continue

        if ts_node.type in func_types:
            # Arrow functions assigned to a variable get the variable name
            func_name = _resolve_ts_func_name(ts_node, source_bytes)
            qname = _qualified_name(file_path, enclosing_class, func_name)
            is_test = (
                _TEST_PATH_RE.search(file_path) is not None
                or _TEST_NAME_RE.match(func_name) is not None
            )
            nodes.append(NodeInfo(
                kind="Test" if is_test else "Function",
                name=func_name, qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language=language,
                parent_name=enclosing_class,
                is_test=is_test,
            ))
            if enclosing_class:
                class_qname = f"{file_path.lstrip('/')}::{enclosing_class}"
                edges.append(EdgeInfo(
                    kind="CONTAINS", source=class_qname, target=qname,
                    file_path=file_path, line=ts_node.start_point[0] + 1,
                ))
            for call in _collect_descendants(ts_node, call_types):
                fn_node = call.children[0] if call.children else None
                if fn_node and fn_node.type in ("identifier", "member_expression"):
                    edges.append(EdgeInfo(
                        kind="CALLS", source=qname,
                        target=_node_text(fn_node, source_bytes),
                        file_path=file_path, line=call.start_point[0] + 1,
                    ))
            continue

        for child in ts_node.children:
            stack.append((child, enclosing_class))

    return nodes, edges


def _extract_go(
    tree,
    source_bytes: bytes,
    file_path: str,
    file_hash: str,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Walk a Go AST and extract types, functions, and relationships."""
    nodes: list[NodeInfo] = []
    edges: list[EdgeInfo] = []

    file_qname = _file_qname(file_path)
    nodes.append(NodeInfo(
        kind="File", name=Path(file_path).name,
        qualified_name=file_qname,
        file_path=file_path,
        line_start=1, line_end=tree.root_node.end_point[0] + 1,
        file_hash=file_hash, language="go",
    ))

    call_types   = set(_CALL_TYPES["go"])
    import_types = set(_IMPORT_TYPES["go"])

    stack = [(tree.root_node, None)]
    while stack:
        ts_node, enclosing_struct = stack.pop()

        if ts_node.type in import_types:
            # import "fmt" or import ( "fmt" \n "os" )
            for spec in _collect_descendants(ts_node, {"interpreted_string_literal"}):
                raw = _node_text(spec, source_bytes).strip('"')
                edges.append(EdgeInfo(
                    kind="IMPORTS_FROM", source=file_qname, target=raw,
                    file_path=file_path, line=spec.start_point[0] + 1,
                ))
            continue

        if ts_node.type == "type_declaration":
            # type Foo struct { ... }
            for spec in ts_node.children:
                if spec.type == "type_spec":
                    name_node = _find_child_by_type(spec, "type_identifier")
                    if name_node:
                        struct_name = _node_text(name_node, source_bytes)
                        qname = f"{file_path.lstrip('/')}::{struct_name}"
                        nodes.append(NodeInfo(
                            kind="Class", name=struct_name, qualified_name=qname,
                            file_path=file_path,
                            line_start=ts_node.start_point[0] + 1,
                            line_end=ts_node.end_point[0] + 1,
                            file_hash=file_hash, language="go",
                        ))
                        edges.append(EdgeInfo(
                            kind="CONTAINS", source=file_qname, target=qname,
                            file_path=file_path, line=ts_node.start_point[0] + 1,
                        ))
            continue

        if ts_node.type in ("function_declaration", "method_declaration"):
            name_node = _find_child_by_type(ts_node, "identifier", "field_identifier")
            func_name = _node_text(name_node, source_bytes) if name_node else "<anon>"

            # For method declarations, extract receiver type as the "class"
            recv_class = None
            if ts_node.type == "method_declaration":
                recv_node = _find_child_by_type(ts_node, "parameter_list")
                if recv_node:
                    for p in recv_node.children:
                        if p.type in ("parameter_declaration",):
                            type_node = p.children[-1] if p.children else None
                            if type_node:
                                raw = _node_text(type_node, source_bytes).strip("*")
                                recv_class = raw if raw.isidentifier() else None

            qname = _qualified_name(file_path, recv_class, func_name)
            is_test = (
                _TEST_PATH_RE.search(file_path) is not None
                or func_name.startswith("Test")
                or func_name.startswith("Benchmark")
            )
            nodes.append(NodeInfo(
                kind="Test" if is_test else "Function",
                name=func_name, qualified_name=qname,
                file_path=file_path,
                line_start=ts_node.start_point[0] + 1,
                line_end=ts_node.end_point[0] + 1,
                file_hash=file_hash, language="go",
                parent_name=recv_class,
                is_test=is_test,
            ))
            edges.append(EdgeInfo(
                kind="CONTAINS", source=file_qname, target=qname,
                file_path=file_path, line=ts_node.start_point[0] + 1,
            ))
            for call in _collect_descendants(ts_node, call_types):
                fn_node = call.children[0] if call.children else None
                if fn_node:
                    edges.append(EdgeInfo(
                        kind="CALLS", source=qname,
                        target=_node_text(fn_node, source_bytes),
                        file_path=file_path, line=call.start_point[0] + 1,
                    ))
            continue

        for child in ts_node.children:
            stack.append((child, enclosing_struct))

    return nodes, edges


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_descendants(ts_node, types: set[str]) -> list:
    """BFS over AST children; collect all nodes whose type is in `types`."""
    result = []
    q = list(ts_node.children)
    while q:
        node = q.pop(0)
        if node.type in types:
            result.append(node)
        else:
            q.extend(node.children)
    return result


def _resolve_ts_func_name(ts_node, source_bytes: bytes) -> str:
    """Extract function name including arrow-function variable names."""
    if ts_node.type == "arrow_function":
        # Check if parent is a variable declaration: const foo = () => {}
        # We look at sibling via parent, but tree-sitter doesn't give parent refs.
        # Fallback: use 'identifier' child if exists.
        id_child = _find_child_by_type(ts_node, "identifier")
        return _node_text(id_child, source_bytes) if id_child else "<arrow>"
    id_child = _find_child_by_type(ts_node, "identifier", "property_identifier")
    return _node_text(id_child, source_bytes) if id_child else "<anon>"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_language(file_path: str) -> Optional[str]:
    """Return a language string for the file, or None if unsupported."""
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(suffix)


def parse_file(
    file_path: str,
    repo_root: Optional[str] = None,
) -> ParseResult:
    """Parse a single source file and return its structural entities.

    Args:
        file_path: Absolute path to the source file.
        repo_root: Repo root to compute relative paths for qualified names.
                   If omitted, file_path is used as-is.

    Returns:
        ParseResult containing NodeInfo and EdgeInfo lists.
        On grammar-unavailable or parse error, returns an empty result with
        ``error`` set so callers can log and continue.
    """
    abs_path = Path(file_path)
    if not abs_path.exists():
        return ParseResult(
            file_path=file_path, file_hash="", language="",
            error=f"File not found: {file_path}",
        )

    language = detect_language(file_path)
    if language is None:
        return ParseResult(
            file_path=file_path, file_hash="", language="",
            error=f"Unsupported language for extension: {abs_path.suffix}",
        )

    try:
        source_bytes = abs_path.read_bytes()
    except OSError as exc:
        return ParseResult(
            file_path=file_path, file_hash="", language=language,
            error=f"Cannot read file: {exc}",
        )

    file_hash = _sha256(source_bytes)

    # Compute repo-relative path for qualified names
    if repo_root:
        try:
            rel = str(abs_path.relative_to(repo_root))
        except ValueError:
            rel = file_path
    else:
        rel = file_path

    parser, lang = _make_parser(language)
    if parser is None:
        return ParseResult(
            file_path=rel, file_hash=file_hash, language=language,
            error=f"No tree-sitter grammar for {language}",
        )

    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:
        return ParseResult(
            file_path=rel, file_hash=file_hash, language=language,
            error=f"Parse error: {exc}",
        )

    # Dispatch to language-specific extractor
    try:
        if language == "java":
            node_list, edge_list = _extract_java(tree, source_bytes, rel, file_hash)
        elif language == "python":
            node_list, edge_list = _extract_python(tree, source_bytes, rel, file_hash)
        elif language in ("typescript", "tsx", "javascript"):
            node_list, edge_list = _extract_typescript(tree, source_bytes, rel, file_hash, language)
        elif language == "go":
            node_list, edge_list = _extract_go(tree, source_bytes, rel, file_hash)
        else:
            return ParseResult(
                file_path=rel, file_hash=file_hash, language=language,
                error=f"No extractor implemented for {language}",
            )
    except Exception as exc:
        log.exception("Extractor error for %s (%s): %s", rel, language, exc)
        return ParseResult(
            file_path=rel, file_hash=file_hash, language=language,
            error=f"Extractor exception: {exc}",
        )

    return ParseResult(
        file_path=rel,
        file_hash=file_hash,
        language=language,
        nodes=node_list,
        edges=edge_list,
    )


def parse_directory(
    directory: str,
    repo_root: Optional[str] = None,
    max_files: int = 5000,
) -> list[ParseResult]:
    """Parse all supported source files under a directory.

    Skips: node_modules, .git, __pycache__, target/, build/, dist/.

    Args:
        directory:  Root directory to scan.
        repo_root:  Passed through to parse_file for qualified names.
        max_files:  Safety cap — stops after this many files to prevent OOM.

    Returns:
        List of ParseResult, one per file (including error results for
        unsupported / unparseable files).
    """
    _SKIP_DIRS = {
        "node_modules", ".git", "__pycache__", "target", "build", "dist",
        ".idea", ".vscode", "vendor", "venv", ".venv", ".mypy_cache",
    }

    root = Path(directory)
    results: list[ParseResult] = []
    count = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if detect_language(str(path)) is None:
            continue
        if count >= max_files:
            log.warning("parse_directory: reached max_files=%d cap", max_files)
            break

        result = parse_file(str(path), repo_root=repo_root or directory)
        if result.error and "No tree-sitter grammar" not in result.error:
            log.debug("parse_file error for %s: %s", path, result.error)
        results.append(result)
        count += 1

    log.info(
        "parse_directory complete: %d files, %d errors",
        count,
        sum(1 for r in results if r.error),
    )
    return results
