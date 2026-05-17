"""
Name-based matching for entity resolution (ADR-0093).

Provides two helpers:

* ``normalize(text)``  — lowercases, splits on camelCase/PascalCase and
  non-word chars, then rejoins as space-separated tokens.
  "PayerModule" → "payer module"
  "payer_module" → "payer module"
  "PAYER MODULE" → "payer module"

* ``names_match(a, b)`` — True when the normalized forms are equal.

Normalization is deliberately lightweight (no stemming, no stop-word removal)
so it stays deterministic and fast.  Fuzzy / semantic matching lives in
embed_matcher.py.
"""
from __future__ import annotations

import re

# Split on transitions: word→UPPER, lower→UPPER, or any run of non-alphanumeric.
_CAMEL_RE   = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD   = re.compile(r"[^a-zA-Z0-9]+")


def normalize(text: str) -> str:
    """
    Return a lowercase space-joined token string for *text*.

    Examples
    --------
    >>> normalize("PayerModule")
    'payer module'
    >>> normalize("payer_module")
    'payer module'
    >>> normalize("PAYER MODULE")
    'payer module'
    >>> normalize("source://notion/page/abc123@ws")
    'source notion page abc123 ws'
    """
    # First handle camelCase / PascalCase splitting
    text = _CAMEL_RE.sub(" ", text)
    # Replace non-alphanumeric sequences with spaces
    text = _NON_WORD.sub(" ", text)
    # Lowercase and strip
    return text.strip().lower()


def normalize_title(title: str) -> str:
    """
    Normalize a human-readable artifact title for name matching.

    Same as ``normalize()`` but also collapses duplicate whitespace so the
    result is suitable for direct equality comparison.
    """
    return " ".join(normalize(title).split())


def names_match(title_a: str, title_b: str) -> bool:
    """Return True when the two titles normalize to the same token string."""
    return normalize_title(title_a) == normalize_title(title_b)
