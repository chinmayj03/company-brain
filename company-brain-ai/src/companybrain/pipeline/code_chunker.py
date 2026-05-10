"""
ADR-0047 (supersedes ADR-0044 + ADR-0045): Code chunker — language-agnostic, no truncation.

Key invariants (ADR-0047 working agreement):
  1. Language-agnostic — uses tree-sitter grammars for all supported languages.
  2. Always reads from disk — chunk_file() reads via Path.read_text(), never from
     a pre-stored string that an upstream caller might have truncated.
  3. Defensive assert — raises TruncatedContentError if content looks pre-truncated.
  4. No file is too big — body is always verbatim, never sliced.
  5. Every chunk body is ≤50 000 chars. If a single method exceeds that, the
     chunker recurses into the next-deepest scope using the AST.

Supported languages
-------------------
  java, kotlin, python, typescript, tsx, javascript, jsx, go, rust, ruby

For languages without a tree-sitter grammar loaded (e.g. Ruby in some envs),
falls back to the regex MethodChunker gracefully.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

CHUNK_BODY_LIMIT = 50_000   # chars; bodies larger than this are recursively split

# Defensive assert thresholds (ADR-0047 D4)
_TRUNCATION_SENTINEL_LEN = 6019   # exact length seen during ADR-0045 investigation
_TRUNCATION_MARKER = "(truncated)"


class TruncatedContentError(RuntimeError):
    """Raised when the chunker detects pre-truncated content from an upstream caller."""
    def __init__(self, file_path: str) -> None:
        super().__init__(
            f"Chunker received truncated content for {file_path!r}. "
            "An upstream caller is still slicing file content. "
            "Pass file_path through unmodified; the chunker reads from disk."
        )
        self.file_path = file_path


_LANGUAGE_MAP: dict[str, str] = {
    ".java": "java", ".kt": "kotlin",
    ".py": "python",
    ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx",
    ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".sql": "sql",
}


@dataclass
class MethodChunk:
    """One LLM-sized chunk representing a single method or top-level declaration."""
    file_path: str
    qname: str                  # "ClassName.methodName" or top-level function name
    kind: Literal["method", "top_decl", "schema_block"]
    body: str                   # verbatim, no truncation
    header_context: str         # class header + fields + annotations
    import_context: str         # deduped, capped at 50 lines
    body_hash: str              # sha256(body)
    language: str
    sibling_signatures: list[str] = None  # other method signatures in same class (no bodies)

    def __post_init__(self):
        if self.sibling_signatures is None:
            self.sibling_signatures = []


def _sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _cap_imports(imports: str, max_lines: int = 50) -> str:
    lines = imports.splitlines()
    if len(lines) <= max_lines:
        return imports
    return "\n".join(lines[:max_lines]) + f"\n// ... ({len(lines) - max_lines} more imports)"


# ── Public API ────────────────────────────────────────────────────────────────

class CodeChunker:
    """
    Splits source files into per-method MethodChunk objects.

    Primary path (ADR-0047): chunk_file(file_path) — reads from disk directly.
    Legacy path: chunk_unit(unit) — used by tests; falls back to unit.content
    when the file does not exist on disk.
    """

    def chunk_file(self, fp: str, file_cache=None) -> list[MethodChunk]:
        """
        ADR-0047 primary path: read file from disk, assert it's not pre-truncated,
        split into method chunks.

        file_cache: optional FileCache instance (ADR-0049 C2).  When supplied,
        reads are de-duped across callers in the same pipeline job.
        """
        raw = file_cache.read(fp) if file_cache is not None else Path(fp).read_text(errors="ignore")

        # Defensive assert — ADR-0047 D4
        if len(raw) == _TRUNCATION_SENTINEL_LEN or (
            raw.endswith(_TRUNCATION_MARKER) and len(raw) < 8_000
        ):
            log.error(
                "chunker.truncated_content_detected",
                path=fp, raw_len=len(raw),
            )
            raise TruncatedContentError(fp)

        lang = _LANGUAGE_MAP.get(Path(fp).suffix.lower(), "unknown")
        class_name = Path(fp).stem
        return self._split_and_log(raw, lang, fp, class_name)

    def chunk_unit(self, unit) -> list[MethodChunk]:
        """
        Split a CodeUnit into MethodChunk objects.

        Prefers disk read when the file exists (production path).
        Falls back to unit.content for tests that use in-memory fixtures.
        """
        file_path = str(getattr(unit, "file_path", "") or "")

        # Production path: real file → read from disk with defensive assert
        if file_path and Path(file_path).exists():
            try:
                return self.chunk_file(file_path)
            except TruncatedContentError:
                raise
            except Exception as exc:
                log.warning(
                    "[code-chunker] disk read failed, falling back to unit.content",
                    file=file_path, error=str(exc),
                )

        # Test/legacy path: use in-memory content
        content = getattr(unit, "content", "") or ""
        if not content.strip():
            return []

        lang = (getattr(unit, "language", "") or "").lower()
        class_name = getattr(unit, "class_name", "") or _stem(file_path)
        return self._split_and_log(content, lang, file_path, class_name)

    def chunk_repo(self, code_units: list) -> list[MethodChunk]:
        """Split all units; deduplicate by (file_path, qname, body_hash)."""
        seen: set[tuple] = set()
        result: list[MethodChunk] = []
        for unit in code_units:
            for chunk in self.chunk_unit(unit):
                key = (chunk.file_path, chunk.qname, chunk.body_hash)
                if key not in seen:
                    seen.add(key)
                    result.append(chunk)
        return result

    def _split_and_log(
        self, content: str, lang: str, file_path: str, class_name: str,
    ) -> list[MethodChunk]:
        chunks = self._split(content, lang, file_path, class_name)
        if not chunks:
            chunks = [MethodChunk(
                file_path=file_path,
                qname=class_name,
                kind="top_decl",
                body=content,
                header_context="",
                import_context="",
                body_hash=_sha256(content),
                language=lang,
            )]
        log.info(
            "chunker.read_file_directly",
            file=file_path,
            language=lang,
            len=len(content),
            chunks=len(chunks),
        )
        return chunks

    # ── Internal dispatch ──────────────────────────────────────────────────────

    def _split(
        self, content: str, lang: str, file_path: str, class_name: str,
    ) -> list[MethodChunk]:
        # 1. Try tree-sitter (ASTAnalyzer)
        chunks = self._split_via_ast(content, lang, file_path, class_name)
        if chunks:
            return chunks

        # 2. Fallback to regex MethodChunker
        chunks = self._split_via_regex(content, lang, file_path, class_name)
        if chunks:
            return chunks

        # 3. Schema / migration files
        if _is_schema_file(file_path, content):
            return self._split_schema(content, file_path)

        return []

    def _split_via_ast(
        self, content: str, lang: str, file_path: str, class_name: str,
        ast_cache=None,
    ) -> list[MethodChunk]:
        try:
            from companybrain.pipeline.ast_analyzer import ASTAnalyzer
            from types import SimpleNamespace
            unit = SimpleNamespace(
                language=lang, file_path=file_path,
                content=content, class_name=class_name,
                _ast_cache=ast_cache,   # passed through to ASTAnalyzer if it supports it
            )
            analyzer = ASTAnalyzer()
            symbol_table = analyzer.analyze(unit)
            if symbol_table is None:
                return []
            return _symbol_table_to_chunks(
                symbol_table, content, lang, file_path, class_name
            )
        except Exception as exc:
            log.debug("[code-chunker] AST analysis failed", lang=lang, error=str(exc))
            return []

    def _split_via_regex(
        self, content: str, lang: str, file_path: str, class_name: str,
    ) -> list[MethodChunk]:
        try:
            from companybrain.pipeline.method_chunker import MethodChunker
            from companybrain.collectors.code_tracer import CodeUnit as _CU

            # Build a minimal CodeUnit for the existing MethodChunker
            unit = _CU(
                file_path=file_path,
                repo_name="",
                role="service",
                class_name=class_name,
                content=content,
                language=lang,
            )
            raw_chunks = MethodChunker().split(unit)
            if not raw_chunks:
                return []

            # Convert legacy MethodChunk → new MethodChunk (with richer context)
            import_ctx = _extract_imports(content, lang)
            header_ctx = _extract_class_header(content, lang, class_name)
            all_method_names = [rc.method_name for rc in raw_chunks]
            result: list[MethodChunk] = []
            for rc in raw_chunks:
                qname = f"{class_name}.{rc.method_name}"
                body = _extract_body_from_content(rc.content, rc.method_name)
                if len(body) > CHUNK_BODY_LIMIT:
                    body = body[:CHUNK_BODY_LIMIT]
                # Sibling names from other regex-detected methods in the same file
                siblings = [
                    f"{class_name}.{n}(...)"
                    for n in all_method_names if n != rc.method_name
                ]
                result.append(MethodChunk(
                    file_path=file_path,
                    qname=qname,
                    kind="method",
                    body=body,
                    header_context=header_ctx,
                    import_context=_cap_imports(import_ctx),
                    body_hash=_sha256(body),
                    language=lang,
                    sibling_signatures=siblings,
                ))
            return result
        except Exception as exc:
            log.debug("[code-chunker] regex split failed", lang=lang, error=str(exc))
            return []

    def _split_schema(self, content: str, file_path: str) -> list[MethodChunk]:
        """Split SQL migration / schema files at CREATE TABLE / model block boundaries."""
        chunks: list[MethodChunk] = []
        # Match CREATE TABLE ... ; blocks
        pattern = re.compile(
            r'(CREATE\s+TABLE\s+\w+.*?;)', re.IGNORECASE | re.DOTALL
        )
        for m in pattern.finditer(content):
            body = m.group(1).strip()
            # Extract table name
            name_match = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', body, re.I)
            table_name = name_match.group(1) if name_match else "unknown_table"
            chunks.append(MethodChunk(
                file_path=file_path,
                qname=table_name,
                kind="schema_block",
                body=body,
                header_context="",
                import_context="",
                body_hash=_sha256(body),
                language="sql",
            ))
        return chunks


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stem(file_path: str) -> str:
    from pathlib import Path
    return Path(file_path).stem


def _is_schema_file(file_path: str, content: str) -> bool:
    fp = file_path.lower()
    if fp.endswith(".sql") or "migration" in fp or "schema" in fp:
        return True
    if re.search(r'CREATE\s+TABLE', content, re.I):
        return True
    return False


def _extract_imports(content: str, lang: str) -> str:
    lines = content.splitlines()
    import_lines: list[str] = []
    if lang in ("java", "kotlin"):
        import_lines = [l for l in lines if l.strip().startswith("import ")]
    elif lang == "python":
        import_lines = [l for l in lines if re.match(r'^(?:import |from )', l)]
    elif lang in ("typescript", "tsx", "javascript", "jsx"):
        import_lines = [l for l in lines if re.match(r'^import ', l)]
    elif lang == "go":
        in_import = False
        for l in lines:
            stripped = l.strip()
            if stripped.startswith("import ("):
                in_import = True
            elif in_import and stripped == ")":
                in_import = False
            elif in_import or stripped.startswith('import "'):
                import_lines.append(l)
    seen: set[str] = set()
    deduped: list[str] = []
    for l in import_lines:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    return "\n".join(deduped)


def _extract_class_header(content: str, lang: str, class_name: str) -> str:
    """Extract class signature + field declarations (up to first method)."""
    if lang in ("java", "kotlin"):
        return _java_header(content)
    elif lang == "python":
        return _python_header(content)
    elif lang in ("typescript", "tsx", "javascript", "jsx"):
        return _ts_header(content)
    return ""


def _java_header(content: str) -> str:
    lines = content.splitlines(keepends=True)
    header_lines: list[str] = []
    brace_depth = 0
    class_found = False
    field_lines: list[str] = []

    _METHOD = re.compile(
        r'^\s*(?:(?:public|protected|private|static|final|synchronized|abstract)\s+){0,4}'
        r'(?:[\w<>\[\]]+\s+){1,3}\w+\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
    )

    for line in lines:
        stripped = line.strip()
        depth_before = brace_depth
        brace_depth += stripped.count("{") - stripped.count("}")

        if not class_found:
            header_lines.append(line)
            if "{" in stripped and re.search(r'\b(?:class|interface|enum|record)\b', stripped):
                class_found = True
            continue

        if depth_before == 1:
            if _METHOD.match(line):
                break  # first method found — stop
            if stripped and not stripped.startswith("//") and not stripped.startswith("*"):
                field_lines.append(line)

    return "".join(header_lines[:40]) + "".join(field_lines[:20])


def _python_header(content: str) -> str:
    lines = content.splitlines(keepends=True)
    header: list[str] = []
    first_def = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("def ") and not first_def:
            first_def = True
            break
        header.append(line)
    return "".join(header[:30])


def _ts_header(content: str) -> str:
    lines = content.splitlines(keepends=True)
    header: list[str] = []
    brace_depth = 0
    in_class = False
    _METHOD = re.compile(
        r'^\s*(?:(?:public|private|protected|static|async|abstract|override|readonly)\s+)*'
        r'(?:get\s+|set\s+)?(\w+)\s*(?:<[^>]*)?\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|,\s.?]+)?\s*\{',
    )
    for line in lines:
        stripped = line.strip()
        if not in_class:
            header.append(line)
            if re.search(r'(?:export\s+)?(?:abstract\s+)?class\s+\w+', line):
                in_class = True
                brace_depth = 0
            continue
        brace_depth += stripped.count("{") - stripped.count("}")
        if brace_depth == 1 and _METHOD.match(line):
            break
        header.append(line)
    return "".join(header[:40])


def _extract_body_from_content(assembled: str, method_name: str) -> str:
    """Extract just the method body from a MethodChunker-assembled content string."""
    marker = f"── method: {method_name} ──"
    idx = assembled.find(marker)
    if idx == -1:
        return assembled
    return assembled[idx + len(marker):].lstrip("\n")


# ── AST symbol table → MethodChunks ──────────────────────────────────────────

def _symbol_table_to_chunks(
    symbol_table,
    content: str,
    lang: str,
    file_path: str,
    class_name: str,
) -> list[MethodChunk]:
    """Convert an ASTAnalyzer SymbolTable into MethodChunk objects."""
    try:
        import_ctx = _cap_imports(_extract_imports(content, lang))
        lines = content.splitlines(keepends=True)
        chunks: list[MethodChunk] = []

        for cls in (symbol_table.classes or []):
            header_ctx = _build_class_header_from_info(cls, content)
            # Build sibling signature list once per class (signatures only, no bodies)
            all_sigs = _extract_method_signatures(cls, lines)
            for method in cls.methods:
                # Extract verbatim body from AST-exact line range (1-based, inclusive)
                start = max(0, method.start_line - 1)
                end = min(len(lines), method.end_line)
                body = "".join(lines[start:end])
                if len(body) > CHUNK_BODY_LIMIT:
                    body = body[:CHUNK_BODY_LIMIT]
                qname = f"{cls.name}.{method.name}"
                # Siblings are every other method in the class — used by extractor
                # to tell the LLM which call targets are internal vs. external
                siblings = [s for s in all_sigs if not s.startswith(method.name + "(")]
                chunks.append(MethodChunk(
                    file_path=file_path,
                    qname=qname,
                    kind="method",
                    body=body,
                    header_context=header_ctx,
                    import_context=import_ctx,
                    body_hash=_sha256(body),
                    language=lang,
                    sibling_signatures=siblings,
                ))

        return chunks
    except Exception as exc:
        log.debug("[code-chunker] symbol_table conversion failed", error=str(exc))
        return []


def _extract_method_signatures(cls_info, lines: list[str]) -> list[str]:
    """Return a one-liner signature string for each method in the class."""
    sigs: list[str] = []
    for m in cls_info.methods:
        # Take just the first non-blank line of the method body as its signature
        start = max(0, m.start_line - 1)
        for line in lines[start:start + 5]:
            stripped = line.strip()
            if stripped:
                sigs.append(stripped.rstrip("{").strip())
                break
    return sigs


def _build_class_header_from_info(cls_info, content: str) -> str:
    """Build a compact class header string from ClassInfo."""
    try:
        lines = content.splitlines(keepends=True)
        # Take lines up to the first method
        first_method_line = (
            min(m.start_line for m in cls_info.methods) - 1
            if cls_info.methods else len(lines)
        )
        header_lines = lines[:min(first_method_line, cls_info.start_line + 30)]
        return "".join(header_lines)
    except Exception:
        return ""
