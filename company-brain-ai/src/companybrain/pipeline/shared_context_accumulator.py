"""
SharedContextAccumulator — rule-based updater for L2SharedContext.

Called by the orchestrator after every successful entity extraction.
No LLM call — pure pattern matching and heuristics.

The accumulator is what makes L2 grow: by the time we're extracting the
8th class, L2 knows the domain vocabulary, service registry, and patterns
that were discovered in the first 7.
"""

from __future__ import annotations

import re
from typing import Optional

import structlog

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.context_hierarchy import L2SharedContext

log = structlog.get_logger(__name__)

# ── Pattern sets for architecture detection ────────────────────────────────────

_SAGA_INDICATORS    = frozenset({"EventPublisher", "ApplicationEventPublisher", "publish(", "emit(", "publishEvent"})
_AUTH_INDICATORS    = frozenset({"@PreAuthorize", "@Secured", "SecurityContext", "Authentication", "hasRole", "hasAuthority", "UserDetails"})
_CACHE_INDICATORS   = frozenset({"@Cacheable", "@CacheEvict", "@CachePut", "CacheManager", "RedisTemplate", "Cache.put"})
_ASYNC_INDICATORS   = frozenset({"@Async", "CompletableFuture", "Mono<", "Flux<", "@EventListener", "ExecutorService"})
_RETRY_INDICATORS   = frozenset({"@Retryable", "RetryTemplate", "@CircuitBreaker", "Resilience4j"})

# Field name suffixes that suggest business semantics
_SEMANTIC_FIELD_RE  = re.compile(
    r'(score|rate|ratio|amount|price|fee|tax|count|limit|threshold|index|rank|weight|percent|days|hours)',
    re.IGNORECASE,
)

# Service/role class suffixes
_SERVICE_SUFFIXES    = ("Service", "ServiceImpl")
_REPO_SUFFIXES       = ("Repository", "Repo", "RepositoryImpl", "Dao")
_CLIENT_SUFFIXES     = ("Client", "Adapter", "Gateway", "Connector", "Proxy")
_CONTROLLER_SUFFIXES = ("Controller", "Resource", "Handler", "RestController")

# Common abbreviation candidates: 2-6 all-uppercase letters not in tech exclusion list
_ABBREV_RE          = re.compile(r'\b([A-Z]{2,6})\b')
_TECH_ABBREVS       = frozenset({
    "API", "URL", "HTTP", "JSON", "XML", "JWT", "UUID", "DTO", "ID", "SQL",
    "JPA", "GET", "POST", "PUT", "RPC", "AWS", "SQS", "DB", "UI", "IO",
    "SSL", "TLS", "CDN", "SDK", "ORM", "DI", "AOP", "MVC", "REST", "GCP",
    "S3", "EC2", "RDS", "SNS", "IAM",
})


class SharedContextAccumulator:
    """
    Rule-based accumulator: updates L2SharedContext after each entity extraction.

    Usage:
        accumulator = SharedContextAccumulator()
        # ... after extracting entities from a code unit ...
        accumulator.update(l2, entities, code_unit)
    """

    def update(
        self,
        l2: L2SharedContext,
        entities: list[ExtractedEntity],
        code_unit: CodeUnit,
    ) -> None:
        """
        In-place update of L2 from just-extracted entities + the source code unit.
        All update methods are non-fatal — a bug in one rule doesn't abort extraction.
        """
        try:
            self._update_service_registry(l2, entities, code_unit)
        except Exception as e:
            log.debug("L2 service registry update failed (non-fatal)", error=str(e))

        try:
            self._update_domain_glossary(l2, entities, code_unit)
        except Exception as e:
            log.debug("L2 domain glossary update failed (non-fatal)", error=str(e))

        try:
            self._update_patterns(l2, code_unit)
        except Exception as e:
            log.debug("L2 pattern update failed (non-fatal)", error=str(e))

        try:
            self._update_field_semantics(l2, entities)
        except Exception as e:
            log.debug("L2 field semantics update failed (non-fatal)", error=str(e))

        try:
            self._update_entity_catalog(l2, entities)
        except Exception as e:
            log.debug("L2 entity catalog update failed (non-fatal)", error=str(e))

        log.debug(
            "L2 updated",
            glossary=len(l2.domain_glossary),
            services=len(l2.service_registry),
            patterns=len(l2.pattern_library),
            cross_cutting=len(l2.cross_cutting),
            field_semantics=len(l2.field_semantics),
            entity_catalog=len(l2.entity_catalog),
        )

    # ── Service registry ───────────────────────────────────────────────────────

    def _update_service_registry(
        self,
        l2: L2SharedContext,
        entities: list[ExtractedEntity],
        unit: CodeUnit,
    ) -> None:
        for e in entities:
            if e.entity_type != "Class":
                continue
            name = e.name
            if any(name.endswith(s) for s in _SERVICE_SUFFIXES):
                l2.service_registry.setdefault(name, {"role": "service", "file": e.file})
            elif any(name.endswith(s) for s in _REPO_SUFFIXES):
                l2.service_registry.setdefault(name, {"role": "repository", "file": e.file})
            elif any(name.endswith(s) for s in _CLIENT_SUFFIXES):
                l2.service_registry.setdefault(name, {"role": "client", "file": e.file})
            elif any(name.endswith(s) for s in _CONTROLLER_SUFFIXES):
                l2.service_registry.setdefault(name, {"role": "controller", "file": e.file})

    # ── Domain glossary ────────────────────────────────────────────────────────

    def _update_domain_glossary(
        self,
        l2: L2SharedContext,
        entities: list[ExtractedEntity],
        unit: CodeUnit,
    ) -> None:
        """
        Extract domain abbreviations from entity names + source code.
        Heuristic: all-caps segments that are NOT standard tech abbreviations.
        Tries to find an expansion in comments/Javadoc.
        """
        all_names = " ".join(e.name + " " + (e.signature or "") for e in entities)
        candidates = set(_ABBREV_RE.findall(all_names)) - _TECH_ABBREVS

        content = unit.content or ""

        for abbr in candidates:
            if abbr in l2.domain_glossary:
                continue
            expansion = self._guess_expansion(abbr, content)
            if expansion:
                l2.domain_glossary[abbr] = expansion
                log.debug("L2 glossary: discovered abbreviation", abbr=abbr, expansion=expansion)

    @staticmethod
    def _guess_expansion(abbr: str, source_code: str) -> Optional[str]:
        """
        Look for an inline expansion of `abbr` in the source code.
        Checks Javadoc/comments, string literals, and package paths.
        """
        # Pattern: /** NIQ — Network IQ */ or // NIQ: Network IQ or * NIQ = ...
        comment_re = re.compile(
            rf'(?://\s*|/\*\*?\s*|\*\s*){re.escape(abbr)}\s*[-—:=]\s*([^\n*/]{{3,80}})',
            re.IGNORECASE,
        )
        m = comment_re.search(source_code)
        if m:
            return m.group(1).strip()[:100]

        # String literal: "NIQ stands for Network IQ" or @ApiOperation(value="NIQ - ...")
        string_re = re.compile(
            rf'"[^"]*{re.escape(abbr)}\s*[-—:]\s*([^"{{0,80}}])"',
            re.IGNORECASE,
        )
        m = string_re.search(source_code)
        if m:
            return m.group(1).strip()[:100]

        # Package/import path: com.company.niq.service → "niq module/package"
        pkg_re = re.search(
            rf'\.({re.escape(abbr.lower())})\.', source_code, re.IGNORECASE
        )
        if pkg_re:
            return f"internal module/package: {pkg_re.group(1)}"

        return None

    # ── Architecture patterns ──────────────────────────────────────────────────

    def _update_patterns(self, l2: L2SharedContext, unit: CodeUnit) -> None:
        content = unit.content or ""
        label = unit.class_name or unit.file_path

        # SAGA / event sourcing: @Transactional + event publishing together
        if "@Transactional" in content and any(p in content for p in _SAGA_INDICATORS):
            entry = f"SAGA/event pattern in {label}"
            if entry not in l2.pattern_library:
                l2.pattern_library.append(entry)

        # Auth concern
        if any(p in content for p in _AUTH_INDICATORS):
            entry = f"Spring Security / auth in {label}"
            if entry not in l2.cross_cutting:
                l2.cross_cutting.append(entry)

        # Caching
        if any(p in content for p in _CACHE_INDICATORS):
            entry = f"Caching (@Cacheable/Redis) in {label}"
            if entry not in l2.cross_cutting:
                l2.cross_cutting.append(entry)

        # Async / reactive
        if any(p in content for p in _ASYNC_INDICATORS):
            entry = f"Async/reactive in {label}"
            if entry not in l2.cross_cutting:
                l2.cross_cutting.append(entry)

        # Retry / circuit breaker
        if any(p in content for p in _RETRY_INDICATORS):
            entry = f"Retry/circuit-breaker in {label}"
            if entry not in l2.cross_cutting:
                l2.cross_cutting.append(entry)

    # ── Field semantics ────────────────────────────────────────────────────────

    def _update_field_semantics(
        self,
        l2: L2SharedContext,
        entities: list[ExtractedEntity],
    ) -> None:
        for e in entities:
            if e.entity_type not in ("SchemaField", "DatabaseColumn"):
                continue
            # Strip ClassName. prefix if present (e.g. "Payer.niq_score" → "niq_score")
            field_name = e.name.split(".")[-1]
            if _SEMANTIC_FIELD_RE.search(field_name) and field_name not in l2.field_semantics:
                description = (e.signature or e.name)[:80]
                l2.field_semantics[field_name] = description

    # ── Entity catalog ─────────────────────────────────────────────────────────

    def _update_entity_catalog(
        self,
        l2: L2SharedContext,
        entities: list[ExtractedEntity],
    ) -> None:
        """
        Add high-confidence entities to the catalog.
        Keeps the catalog at max 30 entries, sorted by confidence descending.
        Entities with confidence >= 0.8 are included.
        """
        existing_names = {e["name"] for e in l2.entity_catalog}
        for e in entities:
            if e.confidence >= 0.8 and e.name not in existing_names:
                l2.entity_catalog.append({
                    "name":        e.name,
                    "entity_type": e.entity_type,
                    "file":        e.file,
                    "confidence":  e.confidence,
                })
                existing_names.add(e.name)

        # Keep top 30
        l2.entity_catalog.sort(key=lambda x: x["confidence"], reverse=True)
        l2.entity_catalog = l2.entity_catalog[:30]
