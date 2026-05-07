"""Code-aware tokenizer — see harness §4.4.

Splits camelCase, snake_case, digits; lowercases; drops tokens < 2 chars.
Used as the BM25 tokenizer AND the input to dense embeddings if the embedder
expects pre-tokenised text (most don't).
"""
import re

_PUNCT_RE = re.compile(r"[\s\.\,\;\:\(\)\[\]\{\}\=\>\<\!\&\|\+\-\*\/\\\"']")
_CAMEL_RE = re.compile(r"([a-z])([A-Z])")
_DIGIT_RE = re.compile(r"([a-zA-Z])(\d)")


def tokenize_code(text: str) -> list[str]:
    """Return lowercased subword tokens, length ≥ 2."""
    out: list[str] = []
    for raw in _PUNCT_RE.split(text or ""):
        if not raw or len(raw) < 2:
            continue
        s = _CAMEL_RE.sub(r"\1 \2", raw)   # getUserId → get User Id
        s = _DIGIT_RE.sub(r"\1 \2", s)     # user3D → user 3 D
        for part in re.split(r"[_\-\s]+", s.lower()):
            if len(part) >= 2:
                out.append(part)
    return out
