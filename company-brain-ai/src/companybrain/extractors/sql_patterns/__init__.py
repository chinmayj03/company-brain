"""SQL pattern matchers for embedded SQL in Java source files.

Each sub-module exports a single ``extract(content: str) -> list[RawMatch]``
function where ``RawMatch = (sql_text: str, line_no: int, tier: str)``.

Consumers: ``sql_embedded_scanner.py``.
"""
from __future__ import annotations

from typing import NamedTuple


class RawMatch(NamedTuple):
    """A candidate SQL string found inside a source file."""
    sql_text: str
    line_no: int      # 1-based
    tier: str         # one of the TIER_* constants in sql_deep
    pattern_type: str  # human-readable label for the match (e.g. "@Query", "prepareStatement")
