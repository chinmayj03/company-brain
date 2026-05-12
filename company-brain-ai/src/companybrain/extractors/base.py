"""Shared Extractor protocol for ADR-0057 universal extraction."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from companybrain.models.entities import ExtractedBatch


@runtime_checkable
class Extractor(Protocol):
    """All universal extractors implement this two-method contract."""

    kind: str

    def supports(self, path: Path) -> bool:
        """Quick filename/extension test — must be cheap (no file I/O)."""
        ...

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        """Parse ``content`` and return entities. ``content`` is text (utf-8)."""
        ...
