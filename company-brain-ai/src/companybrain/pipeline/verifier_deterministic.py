"""
ADR-0056 Mode A — deterministic verifier (no LLM).

For each entity with a claim field (``query_text`` or ``code_snippet``),
read the source file and check whether the claim appears verbatim. Whitespace
is normalised before comparison so formatter reflows don't fail an otherwise
genuine match. When the exact substring is absent, fall back to a fuzzy ratio
computed on a sliding window anchored at the first few tokens of the claim.

Cost: $0. Catches the dominant failure mode where the LLM emits SQL or a code
snippet that resembles what the method "probably" runs but isn't verbatim in
source.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from companybrain.models.entities import ExtractedEntity

# Below this ratio against the source, a claim is treated as hallucinated.
# At-or-above this ratio (but not exact) is "fuzzy" — kept but tagged.
FUZZY_THRESHOLD = 0.95

# Cap on the window size we hand to SequenceMatcher; full files can be megabytes
# and the matcher is O(N*M). The anchor scan keeps us at small constant cost.
_WINDOW_PAD = 40


@dataclass
class DeterministicResult:
    """Outcome of one Mode-A verification pass."""

    status: str   # "confirmed" | "fuzzy" | "hallucinated" | "skipped"
    notes: str
    matched_field: str = ""   # which entity field was checked
    ratio: float = 0.0        # best fuzzy ratio observed (1.0 on exact match)


def verify_entity(
    entity: ExtractedEntity,
    source_roots: list[Path],
) -> DeterministicResult:
    """Check ``entity`` against the live source at ``entity.file``.

    ``source_roots`` is the list of repo roots to try in order — typically
    the ``local_path`` of every repo in the pipeline run. The first root that
    contains ``entity.file`` wins.

    Returns one of:
      - confirmed:    the claim appears verbatim (after whitespace normalisation)
      - fuzzy:        the claim differs by < (1 - FUZZY_THRESHOLD) by SequenceMatcher
      - hallucinated: the claim is not present in source
      - skipped:      no claim text on the entity, or the file could not be read
    """
    claim, matched_field = _pick_claim(entity)
    if not claim:
        return DeterministicResult("skipped", "no claim text to verify")

    source = _read_source(entity.file, source_roots)
    if source is None:
        return DeterministicResult(
            "skipped",
            f"source file not found: {entity.file}",
            matched_field=matched_field,
        )

    needle = _normalise(claim)
    haystack = _normalise(source)
    if not needle:
        return DeterministicResult("skipped", "claim normalised to empty string",
                                   matched_field=matched_field)

    if needle in haystack:
        return DeterministicResult(
            "confirmed", "exact substring match (whitespace-normalised)",
            matched_field=matched_field, ratio=1.0,
        )

    ratio = _best_fuzzy_ratio(needle, haystack)
    if ratio >= FUZZY_THRESHOLD:
        return DeterministicResult(
            "fuzzy", f"fuzzy match ratio={ratio:.3f}",
            matched_field=matched_field, ratio=ratio,
        )

    return DeterministicResult(
        "hallucinated", f"no source match (best ratio={ratio:.3f})",
        matched_field=matched_field, ratio=ratio,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _pick_claim(entity: ExtractedEntity) -> tuple[str, str]:
    """Return (claim_text, field_name) for the first non-empty claim field."""
    if entity.query_text and entity.query_text.strip():
        return entity.query_text, "query_text"
    if entity.code_snippet and entity.code_snippet.strip():
        return entity.code_snippet, "code_snippet"
    return "", ""


def _normalise(text: str) -> str:
    """Whitespace-collapse + lowercase. Tolerates formatter reflows and case
    differences in SQL keywords."""
    return " ".join(text.split()).lower()


def _read_source(rel_path: str, roots: list[Path]) -> Optional[str]:
    if not rel_path:
        return None
    candidate: Optional[Path] = None
    p = Path(rel_path)
    if p.is_absolute() and p.is_file():
        candidate = p
    else:
        for root in roots:
            full = root / rel_path
            if full.is_file():
                candidate = full
                break
    if candidate is None:
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _best_fuzzy_ratio(needle: str, haystack: str) -> float:
    """Best SequenceMatcher ratio between ``needle`` and any window of
    ``haystack`` anchored at a leading token of the needle.

    Keeps cost bounded: at most a handful of windows of size
    ``len(needle) + _WINDOW_PAD`` are scored, so the worst case is O(k * N) for
    k windows rather than O(N * M) over the whole file.
    """
    if not needle or len(haystack) < len(needle) // 2:
        return SequenceMatcher(a=needle, b=haystack).ratio()

    # Anchor: first non-trivial token (≥ 4 chars) of the needle. SQL/code
    # snippets nearly always begin with a recognisable keyword or identifier
    # ("select", "insert", "competitorsService", etc.).
    tokens = [t for t in needle.split() if len(t) >= 4]
    if not tokens:
        return SequenceMatcher(a=needle, b=haystack).ratio()
    anchor = tokens[0]

    best = 0.0
    pos = 0
    scans = 0
    while pos < len(haystack) and scans < 16:
        idx = haystack.find(anchor, pos)
        if idx < 0:
            break
        scans += 1
        start = max(0, idx - _WINDOW_PAD)
        end = min(len(haystack), idx + len(needle) + _WINDOW_PAD)
        window = haystack[start:end]
        ratio = SequenceMatcher(a=needle, b=window).ratio()
        if ratio > best:
            best = ratio
            if best >= 0.999:
                return best
        pos = idx + len(anchor)

    return best
