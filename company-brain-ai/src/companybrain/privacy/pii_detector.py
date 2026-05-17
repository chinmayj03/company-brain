"""
PII detector — ADR-0064 M1.

scan(chunk: str) -> list[PIIFinding]

Pipeline:
  1. Regex scan — fast, always runs
  2. Dictionary scan — personal name detection using name lists
  3. LLM judge — for ambiguous names; async; cached by sha256(chunk)

The sync wrapper scan_sync() is provided for test convenience.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Optional

from .pii_patterns import (
    REGEX_PATTERNS,
    FIRST_NAMES,
    LAST_NAMES,
    luhn_valid,
)


# ── Data model ────────────────────────────────────────────────────────────────

PIIKind = Literal[
    "email", "phone", "ssn", "credit_card", "ip_address",
    "personal_name", "api_key", "physical_address", "dob", "passport",
]

DetectorSource = Literal["regex", "dictionary", "llm_judge"]


@dataclass
class PIIFinding:
    kind: str               # PIIKind value
    text: str
    span: tuple[int, int]   # (start, end) byte offsets in the original chunk
    confidence: float       # 0.0 – 1.0
    detector: str           # DetectorSource value

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "span": list(self.span),
            "confidence": self.confidence,
            "detector": self.detector,
        }


# ── Scan ─────────────────────────────────────────────────────────────────────

# In-process cache: sha256(chunk) -> list[PIIFinding]
# Avoids re-scanning the same chunk on re-ingest.
_SCAN_CACHE: dict[str, list[PIIFinding]] = {}

# LLM judge cache: sha256(name_token) -> bool (True = is a person name)
_LLM_JUDGE_CACHE: dict[str, bool] = {}


def _chunk_hash(chunk: str) -> str:
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def scan(chunk: str, *, enable_llm_judge: bool = False) -> list[PIIFinding]:
    """
    Synchronous PII scan.

    Args:
        chunk: The text to scan.
        enable_llm_judge: When True, calls the LLM judge for ambiguous
            personal_name findings.  Default False; set True in production
            (gated by PRIVACY_LLM_JUDGE_ENABLED config flag).

    Returns:
        List of PIIFinding.  May be empty.
    """
    key = _chunk_hash(chunk)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]

    findings: list[PIIFinding] = []

    # Step 1: regex scan
    findings.extend(_regex_scan(chunk))

    # Step 2: dictionary scan (personal names)
    findings.extend(_dictionary_scan(chunk, existing_spans={f.span for f in findings}))

    # Step 3: LLM judge (async, run in a thread-safe manner)
    if enable_llm_judge:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — caller should use async scan
                pass
            else:
                loop.run_until_complete(_llm_judge_pass(findings, chunk))
        except RuntimeError:
            pass  # No event loop; skip LLM judge

    _SCAN_CACHE[key] = findings
    return findings


async def scan_async(chunk: str, *, enable_llm_judge: bool = True) -> list[PIIFinding]:
    """Async version; runs the LLM judge pass if enabled."""
    key = _chunk_hash(chunk)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]

    findings: list[PIIFinding] = []
    findings.extend(_regex_scan(chunk))
    findings.extend(_dictionary_scan(chunk, existing_spans={f.span for f in findings}))

    if enable_llm_judge:
        await _llm_judge_pass(findings, chunk)

    _SCAN_CACHE[key] = findings
    return findings


def clear_cache() -> None:
    """Clear the scan cache (useful in tests)."""
    _SCAN_CACHE.clear()
    _LLM_JUDGE_CACHE.clear()


# ── Step 1: Regex scan ────────────────────────────────────────────────────────

def _regex_scan(chunk: str) -> list[PIIFinding]:
    findings: list[PIIFinding] = []

    for kind, pattern in REGEX_PATTERNS.items():
        for m in pattern.finditer(chunk):
            text = m.group(0)
            span = (m.start(), m.end())
            confidence = 0.95

            # Extra validation for credit cards
            if kind == "credit_card":
                digits_only = re.sub(r"[\s\-]", "", text)
                if not luhn_valid(digits_only):
                    continue
                confidence = 0.99

            # Filter out very short matches for generic api_key pattern
            if kind == "api_key" and len(text) < 20:
                continue

            # Filter private IP ranges from ip_address (optional; keep for now at lower conf)
            if kind == "ip_address":
                if _is_private_ip(text):
                    confidence = 0.5  # lower confidence for private IPs

            findings.append(PIIFinding(
                kind=kind,
                text=text,
                span=span,
                confidence=confidence,
                detector="regex",
            ))

    return findings


def _is_private_ip(ip: str) -> bool:
    """Return True for RFC-1918 / loopback / link-local addresses."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    first, second = octets[0], octets[1]
    return (
        first == 10
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
        or first == 127
        or (first == 169 and second == 254)
    )


# ── Step 2: Dictionary scan ───────────────────────────────────────────────────

# Tokenise on word boundaries; check against name sets.
_WORD_RE = re.compile(r"\b[A-Z][a-z]{1,20}\b")


def _dictionary_scan(
    chunk: str,
    existing_spans: set[tuple[int, int]],
) -> list[PIIFinding]:
    """
    Look for capitalised tokens that match known first/last name dictionaries.

    Skip spans that already have a regex finding to avoid duplicate reports.
    """
    findings: list[PIIFinding] = []

    for m in _WORD_RE.finditer(chunk):
        span = (m.start(), m.end())
        # Skip if this span is already covered by a regex finding
        if any(s[0] <= span[0] < s[1] for s in existing_spans):
            continue

        token = m.group(0)
        if token in FIRST_NAMES or token in LAST_NAMES:
            findings.append(PIIFinding(
                kind="personal_name",
                text=token,
                span=span,
                confidence=0.70,   # dictionary match; needs context
                detector="dictionary",
            ))

    return findings


# ── Step 3: LLM judge (async) ─────────────────────────────────────────────────

async def _llm_judge_pass(findings: list[PIIFinding], chunk: str) -> None:
    """
    For dictionary-sourced personal_name findings, call an LLM to confirm
    whether the name is used as a person reference vs. a code symbol.

    Results are cached by token hash.  Entries that the LLM rejects are
    removed from the findings list in-place.
    """
    to_judge = [f for f in findings if f.detector == "dictionary"]
    if not to_judge:
        return

    try:
        from companybrain.llm.router import get_llm_client  # type: ignore
    except ImportError:
        # LLM subsystem not available; keep all dictionary findings as-is.
        return

    to_remove = []
    for finding in to_judge:
        token_hash = hashlib.sha256(finding.text.encode()).hexdigest()

        if token_hash in _LLM_JUDGE_CACHE:
            is_person = _LLM_JUDGE_CACHE[token_hash]
        else:
            is_person = await _call_llm_judge(finding.text, chunk, get_llm_client)
            _LLM_JUDGE_CACHE[token_hash] = is_person

        if not is_person:
            to_remove.append(finding)
        else:
            finding.confidence = 0.90
            finding.detector = "llm_judge"

    for f in to_remove:
        findings.remove(f)


async def _call_llm_judge(name: str, context: str, get_client) -> bool:
    """
    Ask a Haiku-class model whether `name` refers to a real person in `context`.
    Returns True if it's a person reference.
    """
    try:
        client = get_client(role="fast")
        prompt = (
            f"In the following text, is '{name}' used as a reference to a real person "
            f"(e.g., employee name, customer name) rather than a code variable, product name, "
            f"or generic noun?\n\nText:\n{context[:500]}\n\n"
            "Reply with exactly one word: YES or NO."
        )
        response = await client.complete(prompt, max_tokens=5)
        return "YES" in response.upper()
    except Exception:
        # If LLM call fails, keep the finding (conservative)
        return True
