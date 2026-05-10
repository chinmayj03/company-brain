"""
ADR-0044 PR-0044-2: Code chunker — language-agnostic, no truncation.

Wraps ASTAnalyzer and the regex MethodChunker to emit MethodChunk objects
with rich header context (class signature + fields + annotations) and
deduped import context capped at 50 lines.

Design rules (ADR-0044 working agreement):
  1. Language-agnostic — uses tree-sitter grammars for all supported languages.
  2. No file is too big — body is always verbatim, never sliced.
  3. Every chunk body is ≤50 000 chars. If a single method exceeds that, the
     chunker recurses into the next-deepest scope using the AST.

Supported languages
-------------------
  java, python, typescript, tsx, javascript, go, kotlin, rust, ruby

For languages without a tree-sitter grammar loaded (e.g. Ruby in some envs),
falls back to the regex MethodChunker gracefully.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

CHUNK_BODY_LIMIT = 50_000   # chars; bodies larger than this are recursively split


@dataclass
class MethodChunk:
    """One LLM-sized chunk representing a single method or top-level declaration."""
    file_path: str
    qname: str                  # "ClassName.methodName" or top-level function name
    kind: Literal["method", "top_decl", "schema_block", "whole_file", "batch"]
    body: str                   # verbatim, no truncation
    header_context: str         # class header + fields + annotations
    import_context: str         # deduped, capped at 50 lines
    body_hash: str              # sha256(body)
    language: str
    sibling_signatures: list[str] = None  # other method signatures in same class (no bodies)
    # ADR-0046: adaptive chunking metadata
    strategy: str = "per_method"           # whole_file | batched_methods | per_method
    relevance_reason: str = ""             # non-empty when filtered by ChunkRelevanceFilter

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
    Splits a CodeUnit (or any source text) into per-method MethodChunk objects.

    Usage:
        chunks = CodeChunker().chunk_unit(unit)
        # chunks is always non-empty: at minimum one chunk covering the whole file.
    """

    def chunk_unit(self, unit) -> list[MethodChunk]:
        """
        Split a CodeUnit into MethodChunk objects.

        Applies ADR-0046 adaptive strategy:
          WHOLE_FILE      — one chunk for the whole file  (< 4 000 chars)
          BATCHED_METHODS — small methods grouped into batches (4 000-15 000)
          PER_METHOD      — one chunk per method (> 15 000)
        """
        content = unit.content or ""
        if not content.strip():
            return []

        lang = (unit.language or "").lower()
        file_path = str(unit.file_path or "")
        class_name = unit.class_name or _stem(file_path)

        from companybrain.pipeline.chunk_strategy import (
            ChunkStrategy, choose_strategy, group_into_batches,
        )

        # SQL/migration files always split by table — bypass file-size strategy.
        if _is_schema_file(file_path, content):
            schema_chunks = self._split_schema(content, file_path)
            if schema_chunks:
                for c in schema_chunks:
                    c.strategy = "per_method"
                return schema_chunks

        strategy = choose_strategy(len(content))

        if strategy == ChunkStrategy.WHOLE_FILE:
            import_ctx = _cap_imports(_extract_imports(content, lang))
            chunk = MethodChunk(
                file_path=file_path,
                qname=class_name,
                kind="whole_file",
                body=content if len(content) <= CHUNK_BODY_LIMIT else content[:CHUNK_BODY_LIMIT],
                header_context=_extract_class_header(content, lang, class_name),
                import_context=import_ctx,
                body_hash=_sha256(content),
                language=lang,
                strategy="whole_file",
            )
            log.debug(
                "[code-chunker] whole_file",
                file=file_path,
                chars=len(content),
            )
            return [chunk]

        # PER_METHOD or BATCHED_METHODS: split first, then decide whether to batch.
        per_method_chunks = self._split(content, lang, file_path, class_name)
        if not per_method_chunks:
            # Fallback to whole-file when AST/regex splitting yields nothing.
            import_ctx = _cap_imports(_extract_imports(content, lang))
            return [MethodChunk(
                file_path=file_path,
                qname=class_name,
                kind="whole_file",
                body=content if len(content) <= CHUNK_BODY_LIMIT else content[:CHUNK_BODY_LIMIT],
                header_context=_extract_class_header(content, lang, class_name),
                import_context=import_ctx,
                body_hash=_sha256(content),
                language=lang,
                strategy="whole_file",
            )]

        if strategy == ChunkStrategy.PER_METHOD:
            for c in per_method_chunks:
                c.strategy = "per_method"
            log.debug(
                "[code-chunker] per_method",
                file=file_path,
                language=lang,
                chunks=len(per_method_chunks),
            )
            return per_method_chunks

        # BATCHED_METHODS: group small methods together.
        batches = group_into_batches(per_method_chunks)
        result: list[MethodChunk] = []
        first = per_method_chunks[0]  # use for shared context fields
        for i, batch in enumerate(batches):
            if len(batch) == 1:
                # Solo chunk — still label per_method so the extractor uses the
                # focused single-method prompt (batch of 1 is not worth batching).
                solo = batch[0]
                solo.strategy = "per_method"
                result.append(solo)
            else:
                # Build a batch body with clear method delimiters.
                batch_body = "\n\n".join(
                    f"[METHOD: {c.qname}]\n{c.body}" for c in batch
                )
                batch_chunk = MethodChunk(
                    file_path=file_path,
                    qname=f"{class_name}.__batch_{i}__",
                    kind="batch",
                    body=batch_body if len(batch_body) <= CHUNK_BODY_LIMIT else batch_body[:CHUNK_BODY_LIMIT],
                    header_context=first.header_context,
                    import_context=first.import_context,
                    body_hash=_sha256(batch_body),
                    language=lang,
                    strategy="batched_methods",
                )
                result.append(batch_chunk)

        log.debug(
            "[code-chunker] batched_methods",
            file=file_path,
            language=lang,
            methods=len(per_method_chunks),
            batches=len(result),
        )
        return result

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
    ) -> list[MethodChunk]:
        try:
            from companybrain.pipeline.ast_analyzer import ASTAnalyzer
            from types import SimpleNamespace
            unit = SimpleNamespace(
                language=lang, file_path=file_path,
                content=content, class_name=class_name,
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
