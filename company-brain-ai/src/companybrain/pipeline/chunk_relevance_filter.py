"""
ADR-0046 D3: Relevance-first chunk filter.

Runs BEFORE workers pick up chunks from the queue — eliminates structurally
trivial methods without an LLM call.

Filters applied (all deterministic, no LLM):

  1. Lombok-generated bodies  — @Data/@Getter/@Setter on the class header +
     method name matches getter/setter/equals/hashCode/toString/builder patterns.
  2. Object-method overrides  — @Override of toString/equals/hashCode/clone/finalize.
  3. Pure delegations          — single-line bodies of the form
       return this.field;
       return delegate.method(args);
       this.field = value;  (one-line setter)
  4. Empty or stub bodies     — {}, or only `throw new UnsupportedOperationException`.
  5. @Deprecated methods      — skip in production extraction pass.
  6. @Test methods            — skip in the production extraction pass
                               (handled by the TESTED_BY pass, ADR-0042 E7).

Usage:
    to_extract, filtered = filter_chunks(method_chunks)
    # to_extract: chunks that should go through LLM extraction
    # filtered:   chunks that were dropped (with a .relevance_reason attribute set)
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from companybrain.pipeline.code_chunker import MethodChunk

log = structlog.get_logger(__name__)

# ── Lombok / trivial method name patterns (Java) ──────────────────────────────
_LOMBOK_HEADER_ANNOTATIONS = re.compile(
    r'@(?:Data|Getter|Setter|Value|Builder|AllArgsConstructor|NoArgsConstructor|RequiredArgsConstructor)\b'
)

_TRIVIAL_METHOD_NAMES = re.compile(
    r'^(?:'
    r'get[A-Z]|set[A-Z]|is[A-Z]|has[A-Z]|with[A-Z]'   # bean accessor / fluent
    r'|equals|hashCode|toString|canEqual'               # Object methods
    r'|builder|build|newBuilder'                        # Builder pattern
    r'|clone|finalize'                                  # Object lifecycle
    r')\w*$'
)

# ── Object.method overrides ───────────────────────────────────────────────────
_OBJECT_OVERRIDE_METHODS = frozenset({
    "toString", "equals", "hashCode", "clone", "finalize", "getClass",
    "notify", "notifyAll", "wait",
})

# ── Pure delegation / trivial body patterns ───────────────────────────────────
# Conservative: only match patterns that are unambiguously trivial.
# Avoid matching business-logic one-liners like `return repo.findAll(id);`
# because even a one-liner may carry meaningful semantics.
_PURE_DELEGATION = re.compile(
    r'^\s*(?:'
    r'return\s+this\.\w+\s*;'       # return this.field;  (field access, not a call)
    r'|this\.\w+\s*=\s*\w+\s*;'    # this.field = param;  (one-line setter)
    r'|super\.\w+\(.*?\)\s*;'      # super.method(args);  (super delegation)
    r')\s*$',
    re.DOTALL,
)

# ── Empty / stub body patterns ────────────────────────────────────────────────
_EMPTY_BODY = re.compile(r'^\s*\{?\s*\}?\s*$')
_UNSUPPORTED_STUB = re.compile(
    r'throw\s+new\s+(?:java\.lang\.)?UnsupportedOperationException',
)


def _extract_method_name(qname: str) -> str:
    """'ClassName.methodName' → 'methodName'."""
    return qname.split(".")[-1] if "." in qname else qname


def _body_only(chunk: "MethodChunk") -> str:
    """Strip the method signature line from body to get just the implementation."""
    lines = chunk.body.splitlines()
    # Skip annotation lines and the signature line; keep braces.
    impl_lines = []
    found_open_brace = False
    for line in lines:
        if "{" in line and not found_open_brace:
            found_open_brace = True
            # Include only the part after the opening brace on this line
            after = line[line.index("{") + 1:]
            impl_lines.append(after)
        elif found_open_brace:
            impl_lines.append(line)
    return "\n".join(impl_lines).strip().rstrip("}")


def _is_lombok_trivial(chunk: "MethodChunk") -> bool:
    """True if the class has a Lombok aggregate annotation AND method is a trivial accessor."""
    has_lombok = _LOMBOK_HEADER_ANNOTATIONS.search(chunk.header_context or "")
    if not has_lombok:
        return False
    name = _extract_method_name(chunk.qname)
    return bool(_TRIVIAL_METHOD_NAMES.match(name))


def _is_object_override(chunk: "MethodChunk") -> bool:
    """True if method is @Override of a well-known Object method."""
    name = _extract_method_name(chunk.qname)
    if name not in _OBJECT_OVERRIDE_METHODS:
        return False
    body = chunk.body
    # The @Override must appear in body or header context
    return "@Override" in body or "@Override" in (chunk.header_context or "")


def _is_pure_delegation(chunk: "MethodChunk") -> bool:
    """True if the method body is a single-line delegation."""
    impl = _body_only(chunk).strip()
    # Single non-blank line
    non_blank = [l for l in impl.splitlines() if l.strip()]
    if len(non_blank) != 1:
        return False
    return bool(_PURE_DELEGATION.match(non_blank[0]))


def _is_empty_or_stub(chunk: "MethodChunk") -> bool:
    """True if body is empty or only throws UnsupportedOperationException."""
    impl = _body_only(chunk).strip()
    if not impl or _EMPTY_BODY.match(impl):
        return True
    return bool(_UNSUPPORTED_STUB.search(impl))


def _is_deprecated(chunk: "MethodChunk") -> bool:
    """True if method is annotated @Deprecated."""
    return "@Deprecated" in chunk.body or "@Deprecated" in (chunk.header_context or "")


def _is_test_method(chunk: "MethodChunk") -> bool:
    """True if method is a JUnit/TestNG @Test method."""
    return re.search(r'@Test\b', chunk.body) is not None


# ── Public API ─────────────────────────────────────────────────────────────────

def filter_chunks(
    chunks: list["MethodChunk"],
) -> tuple[list["MethodChunk"], list["MethodChunk"]]:
    """
    Split chunks into (to_extract, filtered).

    Filtered chunks have their `relevance_reason` attribute set to a short string
    explaining why they were dropped.  They are NOT sent to the LLM.
    """
    to_extract: list["MethodChunk"] = []
    filtered: list["MethodChunk"] = []

    for chunk in chunks:
        reason = _classify(chunk)
        if reason:
            chunk.relevance_reason = reason   # type: ignore[attr-defined]
            filtered.append(chunk)
        else:
            to_extract.append(chunk)

    _pct = round(100 * len(filtered) / max(1, len(chunks)))
    log.info(
        "chunk_relevance_filter.done",
        total=len(chunks),
        to_extract=len(to_extract),
        filtered=len(filtered),
        filtered_pct=_pct,
    )
    return to_extract, filtered


def _classify(chunk: "MethodChunk") -> str | None:
    """Return a filter reason string, or None if the chunk should be extracted."""
    if chunk.kind in ("whole_file", "batch"):
        # Aggregated chunks bypass method-level filtering.
        return None

    if _is_lombok_trivial(chunk):
        return "lombok_trivial"
    if _is_object_override(chunk):
        return "object_override"
    if _is_empty_or_stub(chunk):
        return "empty_or_stub"
    if _is_pure_delegation(chunk):
        return "pure_delegation"
    if _is_deprecated(chunk):
        return "deprecated"
    if _is_test_method(chunk):
        return "test_method"
    return None
