"""
Javadoc / JSDoc / docstring extractor — ADR-0057.

Pulls structured method documentation out of source files. Heuristic:
  • Java / JS / TS: ``/** ... */`` blocks immediately preceding a method/function.
  • Python: triple-quoted strings immediately inside ``def name(...):``.

Deterministic — no LLM. The method URN is composed from ``repo + file + method``
in the same shape used by ExtractedEntity.external_id.
"""
from __future__ import annotations

import re
from pathlib import Path

from companybrain.extractors.base import Extractor
from companybrain.models.entities import ExtractedBatch, MethodDoc


_JAVA_LIKE_EXTS = frozenset({".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".mts"})
_PY_EXTS = frozenset({".py"})

# /** ... */ followed by method signature (java / js / ts / kotlin shape).
# Capture: doc body, then a "name(" pattern. The filler before the method name
# refuses to cross another /** block — that prevents a class-level Javadoc from
# being mis-attributed to the first method's name, and refuses to cross a
# "class" / "interface" / "enum" keyword (which would belong to a type doc).
# Body matches "anything except */" so the engine can't backtrack across multiple
# Javadoc blocks to find a method signature.
_DOC_BLOCK = re.compile(
    r"/\*\*\s*\n((?:[^*]|\*(?!/))*)\*/"                    # doc body, no */
    r"(?:(?!/\*\*)(?!\bclass\b)(?!\binterface\b)(?!\benum\b).)*?"  # filler
    r"\s*(?:public|private|protected|static|final|async|export|function|fun|def)?\s*"
    r"(?:[A-Za-z0-9_<>,\s\[\]?]+\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",                       # METHOD NAME captured
    re.DOTALL,
)

# Tag regexes run against the cleaned doc body (after `* ` prefixes are stripped),
# so the lookahead terminator is "next @tag at start of line, or end of body".
_TAG_PARAM = re.compile(r"@param\s+(\S+)\s+(.+?)(?=\n\s*@|\Z)", re.DOTALL)
_TAG_RETURNS = re.compile(r"@returns?\s+(.+?)(?=\n\s*@|\Z)", re.DOTALL)
_TAG_THROWS = re.compile(r"@(?:throws|exception)\s+(\S+)\s+(.+?)(?=\n\s*@|\Z)", re.DOTALL)


class JavadocExtractor:
    kind = "javadoc"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _JAVA_LIKE_EXTS or path.suffix.lower() in _PY_EXTS

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        suffix = path.suffix.lower()
        docs: list[MethodDoc] = []
        if suffix in _JAVA_LIKE_EXTS:
            docs = _extract_jsdoc_style(content, file=str(path), repo=repo)
        elif suffix in _PY_EXTS:
            docs = _extract_python_docstrings(content, file=str(path), repo=repo)
        return ExtractedBatch(
            file=str(path), repo=repo, extractor_kind=self.kind, method_docs=docs,
        )


def _extract_jsdoc_style(content: str, *, file: str, repo: str) -> list[MethodDoc]:
    out: list[MethodDoc] = []
    for m in _DOC_BLOCK.finditer(content):
        body = _clean_block(m.group(1))
        method = m.group(2)
        summary = _summary_line(body)
        params = {p.group(1): _strip_block_continuation(p.group(2)) for p in _TAG_PARAM.finditer(body)}
        throws = {p.group(1): _strip_block_continuation(p.group(2)) for p in _TAG_THROWS.finditer(body)}
        ret = _TAG_RETURNS.search(body)
        out.append(MethodDoc(
            file=file, repo=repo,
            method_urn=f"{repo}/{file}::{method}" if repo else f"{file}::{method}",
            summary=summary,
            params=params,
            returns=_strip_block_continuation(ret.group(1)) if ret else None,
            throws=throws,
        ))
    return out


_PY_DEF = re.compile(
    r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:->\s*[^:]+)?:\s*\n"
    r"(?P=indent)\s+(?P<quote>\"\"\"|''')(?P<body>.*?)(?P=quote)",
    re.DOTALL | re.MULTILINE,
)


def _extract_python_docstrings(content: str, *, file: str, repo: str) -> list[MethodDoc]:
    out: list[MethodDoc] = []
    for m in _PY_DEF.finditer(content):
        method = m.group("name")
        body = m.group("body").strip()
        summary = _summary_line(body)
        out.append(MethodDoc(
            file=file, repo=repo,
            method_urn=f"{repo}/{file}::{method}" if repo else f"{file}::{method}",
            summary=summary,
        ))
    return out


def _clean_block(body: str) -> str:
    """Strip leading ``* `` from each line of a /** ... */ block."""
    lines = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("*"):
            line = line[1:].lstrip()
        lines.append(line)
    return "\n".join(lines).strip()


def _summary_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("@"):
            return line
    return ""


def _strip_block_continuation(s: str) -> str:
    return " ".join(part.strip() for part in s.splitlines()).strip()
