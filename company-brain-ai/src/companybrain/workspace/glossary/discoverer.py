"""
Glossary candidate discovery.

Scans brain entities for recurring domain-specific capitalized noun phrases,
PascalCase identifiers, and ALL_CAPS constants. Groups aliases together and
returns candidates ranked by occurrence count.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator


# ── Common English / code stop-terms ─────────────────────────────────────────

_STOP_TERMS: frozenset[str] = frozenset({
    # English article / connector fragments that appear Title-Cased
    "The", "This", "That", "With", "From", "Into", "When", "Where", "What",
    "Which", "Then", "They", "Their", "There", "These", "Those",
    # Common code keywords (Title-Cased or PascalCase)
    "Return", "Class", "Function", "Method", "Import", "Public", "Private",
    "Static", "Final", "Abstract", "Interface", "Override", "Default",
    "Async", "Await", "Yield",
    # Common generic type names
    "String", "Integer", "Boolean", "List", "Map", "Set", "Dict", "Array",
    "Object", "Value", "Type", "Data", "Item", "Node", "Event",
    # Generic bool/null literals
    "True", "False", "None", "Null",
    # Generic exception / request / response words
    "Error", "Exception", "Response", "Request", "Result", "Config",
    "Service", "Manager", "Handler", "Controller", "Repository",
    # Common ALL_CAPS non-domain words
    "SQL", "API", "URL", "HTTP", "JSON", "XML", "CSV", "PDF",
    "GET", "PUT", "POST", "DELETE", "PATCH",
    "NULL", "TRUE", "FALSE", "NONE",
})

# Very short ALL_CAPS words that are almost certainly not domain terms
_COMMON_ACRONYMS: frozenset[str] = frozenset({
    "ID", "IDs", "DB", "IO", "UI", "OS", "VM", "IP", "TCP", "UDP",
})


@dataclass
class GlossaryCandidate:
    """A candidate glossary term discovered in the workspace corpus."""

    term: str
    normalized: str           # lowercase, underscores/spaces stripped
    occurrences: int
    source_types: set[str]    # {"code", "sql", "doc", "comment", ...}
    contexts: list[str]       # up to 5 example sentences containing the term
    aliases: list[str]        # other surface forms (e.g. "PriorAuth" → ["prior_auth", "PA"])
    definition: str = ""      # filled by Haiku call in promoter
    promoted: bool = False

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "normalized": self.normalized,
            "occurrences": self.occurrences,
            "source_types": list(self.source_types),
            "contexts": self.contexts,
            "aliases": self.aliases,
            "definition": self.definition,
            "promoted": self.promoted,
        }


def _normalize(term: str) -> str:
    """Canonical form: lowercase, strip underscores and spaces."""
    return re.sub(r"[_\s]+", "", term.lower())


def _source_type_from_entity(entity: dict) -> str:
    """Infer a coarse source type from an entity dict."""
    et = (entity.get("entity_type") or "").lower()
    file_path = (entity.get("file") or "").lower()

    if et in {"data_model", "database_table"}:
        return "sql"
    if file_path.endswith((".sql", ".ddl")):
        return "sql"
    if file_path.endswith((".md", ".rst", ".txt", ".adoc")):
        return "doc"
    if et == "business_context":
        return "doc"
    if et in {"component", "function_node", "api_contract"}:
        return "code"
    # Fallback: guess from extension
    if any(file_path.endswith(ext) for ext in (".py", ".java", ".kt", ".ts", ".js", ".go")):
        return "code"
    return "code"


def _text_fields(entity: dict) -> list[tuple[str, str]]:
    """Return (text, source_type) pairs extracted from an entity dict."""
    source = _source_type_from_entity(entity)
    fields: list[tuple[str, str]] = []

    for f in ("qualified_name", "t1_summary", "t0_token", "t1_token"):
        val = entity.get(f) or ""
        if val:
            fields.append((val, source))

    meta = entity.get("metadata") or {}
    for mkey in ("description", "docstring", "comment", "code_snippet",
                 "query_text", "intent_summary", "purpose"):
        val = meta.get(mkey) or ""
        if val:
            # Comments and docstrings get a "comment" label
            src = "comment" if mkey in ("docstring", "comment") else source
            fields.append((val, src))

    return fields


class GlossaryDiscoverer:
    """Scan brain entities for candidate glossary terms."""

    def __init__(self, min_occurrences: int = 20, min_source_types: int = 2):
        self._min_occurrences = min_occurrences
        self._min_source_types = min_source_types

    # ── Public API ────────────────────────────────────────────────────────────

    def discover(self, entities: list[dict]) -> list[GlossaryCandidate]:
        """
        Scan entity names, descriptions, and code snippets for recurring
        non-dictionary capitalized noun phrases and domain identifiers.

        Heuristics:
        - Capitalized noun phrases (PascalCase, ALL_CAPS, or Title Case) appearing N+ times
        - Not in the common English / code stop-term set
        - Not a common code keyword
        - Appears in 2+ different source types

        Returns candidates sorted by occurrence count descending.
        """
        # term -> {occurrences, source_types, contexts}
        term_occurrences: dict[str, int] = defaultdict(int)
        term_sources: dict[str, set[str]] = defaultdict(set)
        term_contexts: dict[str, list[str]] = defaultdict(list)

        for entity in entities:
            for text, source in _text_fields(entity):
                sentences = self._split_sentences(text)
                for sentence in sentences:
                    for term in self._extract_terms(sentence):
                        if self._is_stopterm(term):
                            continue
                        term_occurrences[term] += 1
                        term_sources[term].add(source)
                        if len(term_contexts[term]) < 5:
                            ctx = sentence.strip()
                            if ctx and ctx not in term_contexts[term]:
                                term_contexts[term].append(ctx)

        candidates: list[GlossaryCandidate] = []
        for term, count in term_occurrences.items():
            normalized = _normalize(term)
            cand = GlossaryCandidate(
                term=term,
                normalized=normalized,
                occurrences=count,
                source_types=term_sources[term],
                contexts=term_contexts[term][:5],
                aliases=[],
            )
            candidates.append(cand)

        return sorted(candidates, key=lambda c: c.occurrences, reverse=True)

    def cluster_aliases(self, candidates: list[GlossaryCandidate]) -> list[GlossaryCandidate]:
        """
        Group aliases together.

        E.g., PriorAuth, prior_auth, PA, prior auth → one entry under the
        canonical form (the one with the most occurrences). Aliases are stored
        in the canonical entry; non-canonical entries are removed.
        """
        # Group by normalized form
        norm_groups: dict[str, list[GlossaryCandidate]] = defaultdict(list)
        for c in candidates:
            norm_groups[c.normalized].append(c)

        merged: list[GlossaryCandidate] = []
        for norm, group in norm_groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            # Pick canonical: highest occurrences; break ties by term length (longest)
            canonical = max(group, key=lambda c: (c.occurrences, len(c.term)))
            aliases = []
            merged_sources: set[str] = set(canonical.source_types)
            merged_contexts: list[str] = list(canonical.contexts)
            total_occurrences = canonical.occurrences

            for c in group:
                if c is canonical:
                    continue
                if c.term not in aliases and c.term != canonical.term:
                    aliases.append(c.term)
                merged_sources.update(c.source_types)
                for ctx in c.contexts:
                    if ctx not in merged_contexts and len(merged_contexts) < 5:
                        merged_contexts.append(ctx)
                total_occurrences += c.occurrences

            canonical.aliases = aliases
            canonical.source_types = merged_sources
            canonical.contexts = merged_contexts
            canonical.occurrences = total_occurrences
            merged.append(canonical)

        return sorted(merged, key=lambda c: c.occurrences, reverse=True)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_terms(self, text: str) -> Iterator[str]:
        """Extract candidate terms from text using regex patterns."""
        seen: set[str] = set()

        def _yield(term: str) -> Iterator[str]:
            if term not in seen:
                seen.add(term)
                yield term

        # PascalCase words (two or more capitalized segments, no spaces)
        for m in re.finditer(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text):
            yield from _yield(m.group())

        # ALL_CAPS identifiers (3+ chars, not trivial single-word acronyms)
        for m in re.finditer(r'\b[A-Z][A-Z0-9_]*[A-Z0-9]\b', text):
            word = m.group()
            if len(word) >= 3 and word not in _COMMON_ACRONYMS:
                yield from _yield(word)

        # Title Case phrases (2–3 capitalised words)
        for m in re.finditer(r'\b(?:[A-Z][a-z]+\s){1,2}[A-Z][a-z]+\b', text):
            yield from _yield(m.group())

    def _is_stopterm(self, term: str) -> bool:
        if len(term) < 3:
            return True
        # Strip trailing 's' to catch plurals like "Strings"
        base = term.rstrip("s") if term.endswith("s") else term
        return term in _STOP_TERMS or base in _STOP_TERMS

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Very lightweight sentence splitter — split on newline or '. '."""
        parts: list[str] = []
        for line in text.splitlines():
            # Further split on '. ' to catch prose
            for part in re.split(r'\.\s+', line):
                part = part.strip()
                if part:
                    parts.append(part)
        return parts
