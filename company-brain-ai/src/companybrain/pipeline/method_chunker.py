"""
MethodChunker — Task #33.

Splits a large CodeUnit into per-method sub-chunks so the EntityExtractor
can issue one small, focused LLM call per method instead of one large call
for the whole file.

Why this matters
----------------
• A typical Spring service class is 300-800 lines.  Sent as-is, the LLM must
  reason about the entire file and often misses DatabaseQuery entities buried
  inside helper methods.
• A single method is 10-50 lines → fits comfortably in a 512-token call,
  leaving plenty of budget for the system prompt and output JSON.
• Each chunk carries the class header + field declarations so the LLM still
  knows the class context (DI fields, class name, annotations).

Strategy per language
---------------------
  Java   — brace counting from each method/constructor opening brace
  Python — indentation-based (class body → def blocks)
  TS/JS  — brace counting, same as Java

Threshold
---------
Only split when the unit content exceeds METHOD_SPLIT_THRESHOLD characters
(default 2 000).  Small classes are sent whole to avoid splitting overhead.

Output
------
list[MethodChunk]  — each has `header`, `body`, `method_name`, `line_start`
The caller re-assembles them as:
    {header}

    // ── method: {method_name} ──
    {body}
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# Only split units larger than this
METHOD_SPLIT_THRESHOLD = 2_000   # chars ≈ 500 tokens


@dataclass
class MethodChunk:
    """One LLM-sized slice of a code file."""
    method_name:  str
    language:     str
    file_path:    str
    repo_name:    str
    role:         str
    line_start:   int             # 1-based line number of method declaration
    content:      str             # class header + method body, ready for LLM
    body_hash:    str = ""        # sha256 of the raw method body (E4 freshness)


def _sha256_body(body: str) -> str:
    """SHA-256 of the whitespace-normalised method body for stable freshness comparison."""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

class MethodChunker:
    """
    Splits CodeUnit content into per-method MethodChunk objects.

    Usage:
        chunks = MethodChunker().split(unit)
        if not chunks:
            # unit is small or unsplittable — process whole
            ...
        else:
            for chunk in chunks:
                await extractor._extract_from_code_unit_content(chunk.content, ...)
    """

    def split(self, unit) -> list[MethodChunk]:
        """
        Split a CodeUnit into per-method chunks.

        Returns an empty list if:
        - content is below the threshold (caller should process whole unit)
        - language is unsupported (Kotlin, etc.)
        - splitting would produce ≤1 chunk (nothing to gain)
        """
        content = unit.content or ""
        if len(content) <= METHOD_SPLIT_THRESHOLD:
            return []

        lang = unit.language.lower()
        if lang == "java":
            chunks = _split_java(content, unit)
        elif lang in ("typescript", "ts", "javascript", "js"):
            chunks = _split_typescript(content, unit)
        elif lang == "python":
            chunks = _split_python(content, unit)
        else:
            return []

        if len(chunks) <= 1:
            return []

        log.debug(
            "[method-chunker] Split unit into method chunks",
            file=unit.file_path,
            language=lang,
            chunks=len(chunks),
            original_chars=len(content),
        )
        return chunks


# ── Java splitter ─────────────────────────────────────────────────────────────

# Matches a Java method or constructor declaration line.
# Captures: optional annotations, visibility/modifiers, return type, method name
_JAVA_METHOD_RE = re.compile(
    r'^(?:[ \t]*(?:@\w+[^\n]*\n)*)?'            # optional annotations
    r'[ \t]*(?:public|protected|private|static|final|default|abstract|synchronized|native|'
    r'override|@Override|transactional|@Transactional|\bvoid\b)*\s*'
    r'(?:<[^>]+>\s*)?'                            # optional generic type params
    r'(?:[\w<>\[\],\s]+?)\s+'                     # return type
    r'(\w+)\s*\(',                                # method name + opening paren
    re.MULTILINE,
)

# Faster: just detect lines that look like a method/constructor opener
_JAVA_METHOD_SIMPLE = re.compile(
    r'^([ \t]*)(?:(?:public|protected|private|static|final|synchronized|'
    r'abstract|default)\s+){0,4}'
    r'(?:[\w<>\[\]]+\s+){1,3}'
    r'(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
    re.MULTILINE,
)


def _split_java(content: str, unit) -> list[MethodChunk]:
    lines = content.splitlines(keepends=True)

    # Extract class header: everything up to (but not including) the first method
    header_lines: list[str] = []
    field_lines:  list[str] = []
    method_starts: list[tuple[int, str]] = []   # (line_index, method_name)

    # Pass 1 — find top-level method/constructor openings.
    # KEY: check depth BEFORE counting { on the current line so that a method
    # opener "  public void foo() {" is seen at depth 1 (class body level),
    # not depth 2 (after the { on that line increments the counter).
    brace_depth = 0
    class_open_found = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        depth_before = brace_depth
        brace_depth += stripped.count("{") - stripped.count("}")

        if not class_open_found:
            header_lines.append(line)
            if "{" in stripped and re.search(
                r'\bclass\b|\binterface\b|\benum\b|\brecord\b', stripped
            ):
                class_open_found = True
            continue

        # depth_before == 1  →  this line is at the class body level
        if depth_before == 1:
            m = _JAVA_METHOD_SIMPLE.match(line)
            if m:
                method_name = m.group(2)
                method_starts.append((idx, method_name))
            elif stripped and not stripped.startswith("//") and not stripped.startswith("*"):
                field_lines.append(line)

    if not method_starts:
        return []

    class_header = "".join(header_lines)
    field_section = "".join(field_lines[:30])   # cap field section at 30 lines

    # Pass 2 — extract each method body using brace counting from its opening brace
    chunks: list[MethodChunk] = []
    for i, (start_idx, method_name) in enumerate(method_starts):
        end_idx = method_starts[i + 1][0] if i + 1 < len(method_starts) else len(lines)
        # Collect lines from start to next method (or EOF)
        body_lines = lines[start_idx:end_idx]
        body = "".join(body_lines).rstrip()

        assembled = (
            f"// [class header]\n{class_header.rstrip()}\n\n"
            + (f"// [fields]\n{field_section.rstrip()}\n\n" if field_section.strip() else "")
            + f"// ── method: {method_name} ──\n{body}\n}}"
        )
        chunks.append(MethodChunk(
            method_name=method_name,
            language="java",
            file_path=unit.file_path,
            repo_name=unit.repo_name,
            role=unit.role,
            line_start=start_idx + 1,
            content=assembled,
            body_hash=_sha256_body(body),
        ))

    return chunks


# ── Python splitter ───────────────────────────────────────────────────────────

_PY_DEF_RE = re.compile(r'^([ \t]*)def\s+(\w+)\s*\(', re.MULTILINE)
_PY_CLASS_RE = re.compile(r'^class\s+\w+', re.MULTILINE)


def _split_python(content: str, unit) -> list[MethodChunk]:
    lines = content.splitlines(keepends=True)

    # Find class-level indentation
    class_match = _PY_CLASS_RE.search(content)
    if not class_match:
        return []

    # Extract class header (up to first def)
    first_def = _PY_DEF_RE.search(content)
    if not first_def:
        return []

    header = content[:first_def.start()].rstrip()

    # Collect all top-level (class body) defs — indented exactly one level
    # We detect "class body" defs as those indented with the same prefix
    def_matches = list(_PY_DEF_RE.finditer(content))

    # Find the indentation of the first method body def
    first_indent = def_matches[0].group(1) if def_matches else "    "

    # Keep only defs at the class body level (same indent)
    class_defs = [(m.start(), m.end(), m.group(2)) for m in def_matches
                  if m.group(1) == first_indent]

    if len(class_defs) <= 1:
        return []

    chunks: list[MethodChunk] = []
    for i, (start, _, method_name) in enumerate(class_defs):
        end = class_defs[i + 1][0] if i + 1 < len(class_defs) else len(content)
        body = content[start:end].rstrip()
        line_start = content[:start].count("\n") + 1

        assembled = f"{header}\n\n    # ── method: {method_name} ──\n{body}"
        chunks.append(MethodChunk(
            method_name=method_name,
            language="python",
            file_path=unit.file_path,
            repo_name=unit.repo_name,
            role=unit.role,
            line_start=line_start,
            content=assembled,
            body_hash=_sha256_body(body),
        ))

    return chunks


# ── TypeScript / JavaScript splitter ─────────────────────────────────────────

_TS_METHOD_RE = re.compile(
    r'^([ \t]*)(?:(?:public|private|protected|static|async|override|abstract|readonly)\s+)*'
    r'(?:get\s+|set\s+)?'
    r'(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|,\s.?]+)?\s*\{',
    re.MULTILINE,
)

_TS_CLASS_RE = re.compile(r'(?:export\s+)?(?:abstract\s+)?class\s+\w+')


def _split_typescript(content: str, unit) -> list[MethodChunk]:
    lines = content.splitlines(keepends=True)

    class_match = _TS_CLASS_RE.search(content)
    if not class_match:
        return []

    # Find the opening brace of the class body
    class_body_start = content.find("{", class_match.end())
    if class_body_start == -1:
        return []

    header = content[:class_body_start + 1]

    # Find methods at class body level (brace depth == 1)
    method_starts: list[tuple[int, str]] = []
    brace_depth = 0
    in_class = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        pos_in_content = sum(len(l) for l in lines[:idx])

        if not in_class:
            if pos_in_content >= class_body_start:
                in_class = True
                brace_depth = 1
                continue
        else:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth == 1:
                m = _TS_METHOD_RE.match(line)
                if m:
                    method_starts.append((idx, m.group(2)))

    if len(method_starts) <= 1:
        return []

    chunks: list[MethodChunk] = []
    for i, (start_idx, method_name) in enumerate(method_starts):
        end_idx = method_starts[i + 1][0] if i + 1 < len(method_starts) else len(lines)
        body = "".join(lines[start_idx:end_idx]).rstrip()

        assembled = (
            f"// [class header]\n{header.rstrip()}\n\n"
            f"  // ── method: {method_name} ──\n{body}\n}}"
        )
        chunks.append(MethodChunk(
            method_name=method_name,
            language=unit.language,
            file_path=unit.file_path,
            repo_name=unit.repo_name,
            role=unit.role,
            line_start=start_idx + 1,
            content=assembled,
            body_hash=_sha256_body(body),
        ))

    return chunks
