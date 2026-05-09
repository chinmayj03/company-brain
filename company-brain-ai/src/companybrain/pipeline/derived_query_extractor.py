"""
DerivedQueryExtractor — Tier 1.C.

Language-agnostic detector for derived/named query methods that live in
repository/DAO interfaces. These are never caught by the LLM entity extractor
because they have no body (just a signature), but they ARE the actual SQL
executed at runtime via the framework's query-derivation engine.

Supported patterns (in priority order):
  1. Spring Data JPA: interface extends JpaRepository / CrudRepository /
     PagingAndSortingRepository / Repository — any method declared without a
     body is a potential derived query.  @Query("…") methods always are.
  2. Python/SQLAlchemy: abstract methods decorated with @abstractmethod or
     class-body stubs that match verb prefixes.
  3. Generic: any abstract-interface method starting with a query-verb prefix
     (find, get, fetch, count, list, search, load, query, select, read) in a
     file named *Repository*, *Repo*, *Dao*, *Store*, *Finder*.

The extractor emits InterfaceMethod entities with:
  - entity_type = "InterfaceMethod"
  - confidence  = 0.90 (structural detection, no LLM cost)
  - query_text  = @Query annotation body if present, else None
  - signature   = full method signature line
  - code_snippet = surrounding 3-line context

These entities are picked up by entity_filter.py (InterfaceMethod weight=9)
and by the RelationshipExtractor to build CALLS edges from service → repository
method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.models.entities import ExtractedEntity

log = structlog.get_logger(__name__)

# ── Verb prefixes that identify query-style method names ──────────────────────

_QUERY_VERB_RE = re.compile(
    r'^(?:find|get|fetch|count|list|search|load|query|select|read|exists|retrieve)',
    re.IGNORECASE,
)

# ── Repository file-name patterns ─────────────────────────────────────────────

_REPO_FILENAME_RE = re.compile(
    r'(?:Repository|Repo|Dao|Store|Finder)\.(java|kt|py|ts|js)$',
    re.IGNORECASE,
)

# ── Java / Kotlin patterns ────────────────────────────────────────────────────

# Matches: interface Foo extends JpaRepository<…>
_JAVA_REPO_IFACE = re.compile(
    r'\binterface\s+(\w+)\s+extends\s+[\w,<>\s]*(?:JpaRepository|CrudRepository|'
    r'PagingAndSortingRepository|Repository|MongoRepository|ElasticsearchRepository)',
    re.IGNORECASE,
)

# @Query("…") or @Query(value = "…")
_JAVA_QUERY_ANNOT = re.compile(
    r'@Query\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Abstract/interface method (no body — ends with `;` not `{`)
_JAVA_METHOD_SIG = re.compile(
    r'^[ \t]*(?:(?:@\w+[^\n]*\n)[ \t]*)*'          # optional annotations
    r'(?:public\s+|protected\s+|private\s+|default\s+)?'
    r'(?:<[^>]+>\s+)?'                               # optional generics
    r'[\w<>\[\]]+\s+'                                # return type
    r'(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*;',  # name(params);
    re.MULTILINE,
)


# ── Python patterns ───────────────────────────────────────────────────────────

_PY_ABSTRACT_METHOD = re.compile(
    r'@abstractmethod\s+def\s+(\w+)\s*\(([^)]*)\)',
    re.DOTALL,
)
_PY_STUB_METHOD = re.compile(
    r'def\s+(\w+)\s*\(([^)]*)\)[^:]*:\s*\.\.\.',
)


# ── Public API ────────────────────────────────────────────────────────────────

class DerivedQueryExtractor:
    """
    Zero-LLM extractor: scans code units for derived/named query methods and
    emits InterfaceMethod entities.

    Usage:
        extractor = DerivedQueryExtractor()
        entities  = extractor.extract(code_units, repo_name="niq-service")
    """

    def extract(
        self,
        code_units: list[CodeUnit],
        repo_name: str = "",
        commit_sha: str = "",
    ) -> list[ExtractedEntity]:
        all_entities: list[ExtractedEntity] = []
        for unit in code_units:
            if not unit.content or not unit.file_path:
                continue
            lang = _detect_language(unit.file_path)
            if lang == "java":
                entities = _extract_java(unit, repo_name, commit_sha)
            elif lang == "python":
                entities = _extract_python(unit, repo_name, commit_sha)
            else:
                continue
            all_entities.extend(entities)

        if all_entities:
            log.info(
                "[derived-query] Extracted interface method entities",
                count=len(all_entities),
                repo=repo_name,
            )
        return all_entities


# ── Java / Kotlin extraction ──────────────────────────────────────────────────

def _extract_java(
    unit: CodeUnit,
    repo_name: str,
    commit_sha: str,
) -> list[ExtractedEntity]:
    content   = unit.content
    file_path = unit.file_path
    entities: list[ExtractedEntity] = []

    # Determine if this file is a repository interface
    is_repo_file  = bool(_REPO_FILENAME_RE.search(file_path))
    is_repo_iface = bool(_JAVA_REPO_IFACE.search(content))

    if not (is_repo_file or is_repo_iface):
        return []

    # Extract interface name from file stem or regex
    class_name = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    lines = content.splitlines()

    # Collect @Query annotations mapped to the line index they appear on
    query_by_line: dict[int, str] = {}
    for m in _JAVA_QUERY_ANNOT.finditer(content):
        line_idx = content[:m.start()].count("\n")
        query_by_line[line_idx] = m.group(1).strip()

    # Find abstract method signatures
    for m in _JAVA_METHOD_SIG.finditer(content):
        method_name = m.group(1)
        if not _QUERY_VERB_RE.match(method_name):
            continue

        line_idx  = content[:m.start()].count("\n")
        # The method sig regex consumes annotation lines in its prefix group,
        # so @Query may land at line_idx, line_idx-1, or line_idx-2.
        query_text = (
            query_by_line.get(line_idx)
            or query_by_line.get(line_idx - 1)
            or query_by_line.get(line_idx - 2)
        )

        snippet_start = max(0, line_idx - 1)
        snippet_end   = min(len(lines), line_idx + 3)
        snippet       = "\n".join(lines[snippet_start:snippet_end])

        entities.append(_make_entity(
            entity_type  = "InterfaceMethod",
            name         = f"{class_name}.{method_name}",
            file_path    = file_path,
            repo_name    = repo_name,
            signature    = m.group(0).strip().rstrip(";"),
            query_text   = query_text,
            code_snippet = snippet,
            commit_sha   = commit_sha,
        ))

    return entities


# ── Python extraction ─────────────────────────────────────────────────────────

def _extract_python(
    unit: CodeUnit,
    repo_name: str,
    commit_sha: str,
) -> list[ExtractedEntity]:
    content   = unit.content
    file_path = unit.file_path

    if not _REPO_FILENAME_RE.search(file_path):
        return []

    class_name = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    entities: list[ExtractedEntity] = []

    for pattern in (_PY_ABSTRACT_METHOD, _PY_STUB_METHOD):
        for m in pattern.finditer(content):
            method_name = m.group(1)
            if not _QUERY_VERB_RE.match(method_name):
                continue
            line_idx = content[:m.start()].count("\n")
            lines    = content.splitlines()
            snippet  = "\n".join(lines[max(0, line_idx - 1):line_idx + 3])
            entities.append(_make_entity(
                entity_type  = "InterfaceMethod",
                name         = f"{class_name}.{method_name}",
                file_path    = file_path,
                repo_name    = repo_name,
                signature    = f"def {method_name}({m.group(2)})",
                query_text   = None,
                code_snippet = snippet,
                commit_sha   = commit_sha,
            ))

    return entities


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entity(
    *,
    entity_type: str,
    name: str,
    file_path: str,
    repo_name: str,
    signature: str,
    query_text: str | None,
    code_snippet: str,
    commit_sha: str,
) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type          = entity_type,
        name                 = name,
        file                 = file_path,
        repo                 = repo_name,
        signature            = signature,
        last_modified_commit = commit_sha,
        confidence           = 0.90,
        query_text           = query_text,
        code_snippet         = code_snippet,
    )


def _detect_language(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return {
        "java": "java",
        "kt":   "java",   # Kotlin — same patterns for interface/JPA
        "py":   "python",
    }.get(ext, "")
