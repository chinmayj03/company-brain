"""
Markdown / AsciiDoc / RST / plain-text doc extractor — ADR-0057.

Deterministic parsing: pull H1/H2 headings, fenced code blocks, and the raw
body. Long-doc summarisation is left to a follow-up (would be the only LLM
call in this extractor).
"""
from __future__ import annotations

import re
from pathlib import Path

from companybrain.extractors.base import Extractor
from companybrain.models.entities import Documentation, ExtractedBatch

_DOC_SUFFIXES = frozenset({".md", ".markdown", ".adoc", ".asciidoc", ".rst", ".txt"})

_MD_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_ADOC_HEADING = re.compile(r"^(={1,2})\s+(.+?)\s*$", re.MULTILINE)
_RST_HEADING = re.compile(r"^([^\n]+)\n([=\-~`'\"^*+#])\2{2,}\s*$", re.MULTILINE)

_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_ADOC_BLOCK = re.compile(r"----\s*\n(.*?)----", re.DOTALL)


class DocExtractor:
    kind = "doc"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _DOC_SUFFIXES

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        suffix = path.suffix.lower()
        if suffix in {".adoc", ".asciidoc"}:
            headings = [m.group(2).strip() for m in _ADOC_HEADING.finditer(content)]
            blocks = [m.group(1).rstrip() for m in _ADOC_BLOCK.finditer(content)]
        elif suffix == ".rst":
            headings = [m.group(1).strip() for m in _RST_HEADING.finditer(content)]
            blocks = []  # RST code-block syntax is heterogeneous; punt for now
        else:
            headings = [m.group(2).strip() for m in _MD_HEADING.finditer(content)]
            blocks = [m.group(1).rstrip() for m in _FENCED_BLOCK.finditer(content)]

        title = headings[0] if headings else path.stem

        doc = Documentation(
            file=str(path),
            repo=repo,
            title=title,
            headings=headings,
            code_blocks=blocks,
            raw_text=content,
        )
        return ExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind, documentation=[doc])
