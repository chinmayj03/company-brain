"""
ADR-0047: ChunkRelevanceFilter — three-tier language-agnostic relevance filter.

Filters MethodChunks before they enter the extraction queue.

Tier 1 — Deterministic AST-pattern triviality (no LLM):
  Drop chunks whose body matches trivial structural patterns:
    - Pure getter/setter (1-3 lines, single field access)
    - Empty constructor (no-arg, body is just `super()` or empty braces)
    - Boilerplate equals/hashCode/toString (body < 5 lines, only field refs)
    - Field initializer / constant declaration
  Rationale: These emit zero meaningful edges; burning an LLM call on them
  returns nothing but entity noise.

Tier 2 — Reachability BFS (deterministic, reuses existing graph):
  If the caller supplies a reachability set, drop chunks whose qname is not
  reachable from the entry-point.  Callers that don't have a reachability set
  (e.g. warm-cache runs without an endpoint) skip this tier.

Tier 3 — NOT implemented here. The caller may add an LLM manifest screen
  as a post-step if budget allows. This module does not fire LLM calls.

All three tiers record a `filter_reason` string on the FilterResult so
the queue row can be marked `filtered` for telemetry.

Usage:
    flt = ChunkRelevanceFilter()
    results = flt.filter(chunks, reachable_qnames=None)
    keep    = [r.chunk for r in results if r.keep]
    drop    = [r       for r in results if not r.keep]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import structlog

from companybrain.pipeline.code_chunker import MethodChunk

log = structlog.get_logger(__name__)


# ── Tier-1 trivial patterns ────────────────────────────────────────────────────

# A "trivial" body has at most this many non-blank lines.
_TRIVIAL_LINE_LIMIT = 4

# Method names that strongly suggest trivial boilerplate (language-agnostic).
_TRIVIAL_NAME_EXACT: frozenset[str] = frozenset({
    "equals", "hashCode", "hashcode", "toString", "to_string",
    "__eq__", "__hash__", "__repr__", "__str__",
    "__init_subclass__", "__class_getitem__",
    "compareTo", "compare_to",
})

# Name prefixes that suggest getter/setter (language-agnostic).
_TRIVIAL_NAME_PREFIXES: tuple[str, ...] = (
    "get", "set", "is", "has", "with",         # Java/Kotlin/TS style
)

# Optional method-declaration prefixes stripped before checking the body core.
# Python alternative must come FIRST: `def` is unambiguous and would otherwise
# be consumed as a Java "return type" by the second alternative.
_METHOD_DECL_PREFIX = re.compile(
    r'^(?:'
    r'def\s+\w+\s*\([^)]*\)\s*(?:->\s*\S+\s*)?:\s*'             # Python: def method(args) -> T:
    r'|(?:public\s+|private\s+|protected\s+|static\s+|final\s+)*'  # Java modifiers (may be 0)
    r'\w[\w<>\[\]]*\s+\w+\s*\([^)]*\)\s*(?:throws\s+\w+\s*)?\{?\s*'  # Java: Type name() {
    r')',
    re.DOTALL,
)

# Body content patterns that confirm triviality regardless of name.
# Matched against the *stripped* body (no leading/trailing whitespace).
# Also matched against the body with the method-declaration prefix stripped.
_TRIVIAL_BODY_PATTERNS: list[re.Pattern] = [
    # Pure single-field getter: "return this.foo;" / "return self.foo" / "return @foo"
    re.compile(r'^return\s+(?:this\.|self\.|@)?[\w.]+;?\s*\}?$', re.DOTALL),
    # Single-field setter: "this.foo = foo;" / "self.foo = value"
    re.compile(r'^(?:this\.|self\.)?[\w]+\s*=\s*[\w.]+;?\s*\}?$', re.DOTALL),
    # Empty body: only braces / pass / nothing
    re.compile(r'^\s*(?:\{?\s*\}?|pass)\s*$', re.DOTALL),
    # super() delegation only
    re.compile(r'^\s*(?:super|super\(\))[^;{]*;?\s*$', re.DOTALL),
]


@dataclass
class FilterResult:
    chunk: MethodChunk
    keep: bool
    filter_reason: str  # "" when keep=True; one of the tier labels when keep=False
    tier: int           # 0 = kept, 1 = dropped by tier1, 2 = dropped by tier2


class ChunkRelevanceFilter:
    """
    Applies tier-1 (deterministic triviality) and optional tier-2 (reachability)
    filtering to a flat list of MethodChunks.
    """

    def filter(
        self,
        chunks: list[MethodChunk],
        reachable_qnames: Optional[frozenset[str]] = None,
    ) -> list[FilterResult]:
        results: list[FilterResult] = []
        t1_dropped = 0
        t2_dropped = 0
        kept = 0

        for chunk in chunks:
            # Tier 1 — trivial pattern
            t1_reason = _tier1_reason(chunk)
            if t1_reason:
                results.append(FilterResult(chunk=chunk, keep=False,
                                            filter_reason=t1_reason, tier=1))
                t1_dropped += 1
                continue

            # Tier 2 — reachability (only when caller supplies the set)
            if reachable_qnames is not None and chunk.qname not in reachable_qnames:
                results.append(FilterResult(chunk=chunk, keep=False,
                                            filter_reason="tier2:unreachable", tier=2))
                t2_dropped += 1
                continue

            results.append(FilterResult(chunk=chunk, keep=True,
                                        filter_reason="", tier=0))
            kept += 1

        log.info(
            "chunk_relevance_filter",
            total=len(chunks),
            kept=kept,
            dropped_tier1=t1_dropped,
            dropped_tier2=t2_dropped,
            reachability_enabled=reachable_qnames is not None,
        )
        return results


# ── Tier-1 helpers ─────────────────────────────────────────────────────────────

def _tier1_reason(chunk: MethodChunk) -> str:
    """Return a non-empty reason string if the chunk is trivially filterable."""
    if chunk.kind != "method":
        return ""  # Only filter method chunks; keep top_decl and schema_block

    body = chunk.body.strip()
    if not body:
        return "tier1:empty_body"

    non_blank_lines = [l for l in body.splitlines() if l.strip()]

    # Short bodies — check name + content
    if len(non_blank_lines) <= _TRIVIAL_LINE_LIMIT:
        method_name = _method_name(chunk.qname)

        # Exact trivial name match
        if method_name in _TRIVIAL_NAME_EXACT:
            return f"tier1:boilerplate_name:{method_name}"

        # Strip the method declaration prefix (if present) to get the core body.
        # This handles both Java-style (`public String foo() {`) and Python-style
        # (`def foo(self):`) declarations so the same patterns work for both.
        m = _METHOD_DECL_PREFIX.match(body)
        core = body[m.end():].strip() if m else body

        # Prefix match + short body → getter/setter
        if method_name and any(method_name.startswith(p) and len(method_name) > len(p)
                               for p in _TRIVIAL_NAME_PREFIXES):
            if any(pat.match(core) for pat in _TRIVIAL_BODY_PATTERNS[:2]):
                return "tier1:trivial_accessor"

        # Pattern match regardless of name (empty body, super-only)
        for pat in _TRIVIAL_BODY_PATTERNS[2:]:  # empty + super patterns
            if pat.match(core) or pat.match(body):
                return "tier1:empty_or_super_only"

    return ""


def _method_name(qname: str) -> str:
    """Extract bare method name from 'ClassName.methodName' qname."""
    dot = qname.rfind(".")
    return qname[dot + 1:] if dot != -1 else qname
