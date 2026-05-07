"""
CodeTracer — Stage 0a of the redesigned context builder pipeline.

Instead of mining git diffs, this module traces the actual live code graph:

  1. Finds the handler class/method for the given endpoint
     (annotation-aware for Java Spring Boot, pattern-aware for TS/JS/Python)
  2. Delegates deep traversal to NavigatorAgent — an LLM-driven agent that
     uses code navigation tools to follow the call chain without hardcoding
     any architecture patterns (Spring MVC, hexagonal, CQRS, DDD, etc.)
  3. Converts the agent's NavigatorNode list → CodeUnit list for the pipeline
  4. Falls back to file-level extraction if the agent returns nothing

This FocalContext is the primary LLM input for entity extraction.
Git history is used only as secondary "why" context (business context synthesis).

Supported languages / frameworks:
  Java    — any architecture (Spring Boot, Hexagonal, CQRS, DDD, etc.)
            via NavigatorAgent + code navigation tools
  TypeScript / JavaScript — axios, fetch, api.get/post  (regex, unchanged)
  Python  — FastAPI @router.get / Flask @app.route      (regex, unchanged)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ── Module-level hybrid searcher singleton ────────────────────────────────────
# Keeps the BM25 index + Qdrant connection alive across calls so we don't
# rebuild the index on every trace invocation.
_HYBRID_SEARCHER: Optional["HybridSearcher"] = None


def _get_hybrid_searcher() -> "HybridSearcher":
    global _HYBRID_SEARCHER
    if _HYBRID_SEARCHER is None:
        from companybrain.retrieval.hybrid_search import HybridSearcher
        _HYBRID_SEARCHER = HybridSearcher()
    return _HYBRID_SEARCHER


# ── Regex patterns ────────────────────────────────────────────────────────────

# Java Spring: captures the HTTP method annotation name + first string arg
# Handles both @GetMapping("/path") and @RequestMapping(value = "/path", method = …)
_JAVA_MAPPING_RE = re.compile(
    r'@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
    r'\s*\(?[^)]*?'
    r'(?:value\s*=\s*)?'       # optional value= label
    r'\{?\s*["\']([^"\']+)["\']',   # the path string
    re.MULTILINE,
)

# Java import statement
_JAVA_IMPORT_RE = re.compile(r'^import\s+([\w.]+);', re.MULTILINE)

# Java class/interface declaration
_JAVA_CLASS_RE  = re.compile(r'(?:public\s+)?(?:class|interface|enum)\s+(\w+)')

# TypeScript/JS import
_TS_IMPORT_RE = re.compile(
    r"import\s+.*?\s+from\s+['\"]([./][^'\"]+)['\"]",
    re.MULTILINE,
)

# Axios/fetch calls: axios.get('/path'), fetch('/path'), apiClient.post('/path')
_TS_API_CALL_RE = re.compile(
    r'(?:axios|fetch|apiClient|api|http|client)\s*'
    r'(?:\.)?\s*(?:get|post|put|delete|patch)\s*'
    r'\(\s*[`\'"]([^`\'"]+)[`\'"]',
    re.MULTILINE | re.IGNORECASE,
)

# Python FastAPI / Flask route
_PY_ROUTE_RE = re.compile(
    r'@(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)

# Python import
_PY_IMPORT_RE = re.compile(
    r'^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))',
    re.MULTILINE,
)

# Common naming suffixes that indicate a code layer
_LAYER_HINTS: dict[str, str] = {
    "Controller": "controller",
    "Resource":   "controller",
    "Handler":    "controller",
    "Endpoint":   "controller",
    "Service":    "service",
    "ServiceImpl":"service",
    "Repository": "repository",
    "Repo":       "repository",
    "DAO":        "repository",
    "Mapper":     "repository",
    "Client":     "client",
    "Adapter":    "client",
    "Gateway":    "client",
    "Model":      "model",
    "Entity":     "model",
    "DTO":        "model",
    "Request":    "model",
    "Response":   "model",
    "Payload":    "model",
}

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target",           # Maven/Gradle
    "__pycache__",
    ".gradle",
    ".venv", "venv", "env",        # Python virtual envs inside repos
    "site-packages",               # pip installs
    ".tox", ".mypy_cache",
    "vendor",                      # Go/Ruby vendored deps
})
_CODE_EXTS  = {".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".py"}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class CodeUnit:
    """A single class / module that is relevant to the target endpoint."""
    file_path: str          # relative to repo root
    repo_name: str
    role: str               # controller | service | repository | model | client | component
    language: str           # java | typescript | python | javascript
    content: str            # trimmed current source (capped at MAX_UNIT_CHARS)
    class_name: str = ""
    imports: list[str] = field(default_factory=list)

    def brief(self) -> str:
        """Short description for log lines."""
        return f"{self.role}/{self.class_name or Path(self.file_path).stem}"

    def to_llm_block(self) -> str:
        """Format for LLM context window — labelled code block."""
        return (
            f"### {self.role.upper()} — {self.class_name or Path(self.file_path).stem}\n"
            f"File: {self.file_path}\n"
            f"```{self.language}\n{self.content}\n```"
        )


@dataclass
class FocalContext:
    """
    The set of code units that implement a given API endpoint,
    ordered from most-to-least specific (controller first).
    """
    endpoint: str
    method: str                       # HTTP verb: GET | POST | PUT | DELETE | …
    entry_method: str = ""            # Java/TS handler method name: e.g. "getPayerCompetitors"
    code_units: list[CodeUnit] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.code_units) == 0

    def to_llm_context(self, max_chars: int = 12_000) -> str:
        """
        Render all code units as a single LLM-ready string.
        Stops adding units once max_chars is reached so we never blow context.
        """
        parts: list[str] = [
            f"## Endpoint: {self.method} {self.endpoint}\n",
            "The following code units implement this endpoint (controller → service → repository → models):\n",
        ]
        used = sum(len(p) for p in parts)

        for unit in self.code_units:
            block = unit.to_llm_block() + "\n\n"
            if used + len(block) > max_chars:
                parts.append(f"### (additional units omitted — context budget reached)\n")
                break
            parts.append(block)
            used += len(block)

        return "\n".join(parts)


# ── Main class ────────────────────────────────────────────────────────────────

MAX_UNIT_CHARS = 8_000   # per file — enough to capture full repository interfaces with @Query
MAX_UNITS      = 20      # total code units in one FocalContext
MAX_IMPORT_DEPTH = 3     # how many hops to follow from the handler


class CodeTracer:
    """
    Traces the live code graph for an API endpoint across multiple repos.

    ADR-006 §28: When *workspace_id* and *db_url* are supplied, the structural
    index is queried FIRST to narrow candidate files.  The regex-based filesystem
    scans (_JAVA_MAPPING_RE, _PY_ROUTE_RE, _TS_API_CALL_RE, _TS_IMPORT_RE,
    _PY_IMPORT_RE) are retained as a fallback for workspaces that have not yet
    been indexed.

    Usage::

        # Without structural index (legacy, full filesystem scan):
        tracer = CodeTracer()

        # With structural index (ADR-006, targeted scan):
        tracer = CodeTracer(workspace_id="uuid", db_url="postgresql://...")

        ctx = tracer.trace(
            endpoint="/api/v1/mcheck/niq/competitiveness/summary/competitors/payer",
            method="GET",
            repos=[
                {"path": "/Users/you/backend", "type": "backend"},
                {"path": "/Users/you/frontend", "type": "frontend"},
            ],
        )
        print(ctx.to_llm_context())
    """

    def __init__(
        self,
        workspace_id: Optional[str] = None,
        db_url: Optional[str] = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._structural: Optional[object] = None  # StructuralIndexHelper, type-erased to avoid hard dep

        if workspace_id and db_url:
            try:
                from companybrain.structural.index_helper import StructuralIndexHelper
                self._structural = StructuralIndexHelper(db_url=db_url, workspace_id=workspace_id)
                log.info(
                    "CodeTracer: structural index helper enabled",
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                log.warning("CodeTracer: could not initialise structural index helper: %s", exc)

    async def trace(self, endpoint: str, method: str, repos: list[dict]) -> FocalContext:
        ctx = FocalContext(endpoint=endpoint, method=method)

        for repo_info in repos:
            repo_path = Path(repo_info["path"])
            repo_type = repo_info.get("type", "backend")
            repo_name = repo_path.name

            if not repo_path.exists():
                log.warning("Repo path does not exist", path=str(repo_path))
                continue

            # Preflight: log repo file statistics before any scanning
            try:
                from companybrain.pipeline.file_walker import FileWalker as _FileWalker
                _walker = _FileWalker(repo_path)
                _stats = _walker.stats()
                log.info("Repo preflight stats", repo=repo_name, **_stats)
            except Exception as _stats_exc:
                log.debug("Preflight stats failed (non-fatal)", error=str(_stats_exc))

            try:
                if repo_type == "backend":
                    units, entry_method = await self._trace_backend(repo_path, repo_name, endpoint, method)
                    if entry_method and not ctx.entry_method:
                        ctx.entry_method = entry_method
                elif repo_type == "frontend":
                    units = self._trace_frontend(repo_path, repo_name, endpoint)
                else:
                    units, entry_method = await self._trace_backend(repo_path, repo_name, endpoint, method)
                    if entry_method and not ctx.entry_method:
                        ctx.entry_method = entry_method

                ctx.code_units.extend(units)
                log.info(
                    "CodeTracer collected units",
                    repo=repo_name,
                    repo_type=repo_type,
                    unit_count=len(units),
                    roles=[u.role for u in units],
                )
            except Exception as e:
                log.error("CodeTracer failed for repo", repo=str(repo_path), error=str(e))

        # Deduplicate by file path
        seen: set[str] = set()
        deduped: list[CodeUnit] = []
        for unit in ctx.code_units:
            key = f"{unit.repo_name}/{unit.file_path}"
            if key not in seen:
                seen.add(key)
                deduped.append(unit)
        ctx.code_units = deduped[:MAX_UNITS]

        log.info(
            "FocalContext built",
            endpoint=endpoint,
            total_units=len(ctx.code_units),
            roles=[u.role for u in ctx.code_units],
        )
        return ctx

    # ── Backend tracing ───────────────────────────────────────────────────────

    async def _trace_backend(self, repo_path: Path, repo_name: str, endpoint: str, method: str = "GET") -> tuple[list[CodeUnit], str]:
        """
        Framework-agnostic backend tracing.

        Step 1: LLMHandlerFinder — uses a fast LLM call to identify the entry handler
                from signature-only previews of candidate files.  Works for any language
                or framework (Spring, NestJS, FastAPI, Go chi, Rails, etc.).

        Step 2: KnowledgeNavigatorAgent — agentic deep traversal from the identified
                entry point down the call chain.

        Step 3: Fallback — if LLM finder fails, fall back to the per-language regex
                tracers as a last resort.
        """
        from companybrain.collectors.llm_handler_finder import find_entry_handler_llm

        # ── Step 1: LLM-based handler discovery ──────────────────────────────
        entry = await find_entry_handler_llm(repo_path, endpoint, http_method=method)

        if entry and entry.get("file"):
            entry_file_str    = entry["file"]
            entry_class       = entry.get("class", "")
            entry_method_name = entry.get("method", "")

            try:
                handler_content = Path(entry_file_str).read_text(errors="ignore")
            except OSError:
                handler_content = ""

            log.info(
                "CodeTracer: KnowledgeNavigatorAgent starting (LLM-discovered handler)",
                entry_file=str(Path(entry_file_str).relative_to(repo_path)
                               if Path(entry_file_str).is_absolute() else entry_file_str),
                entry_class=entry_class,
                entry_method=entry_method_name,
                endpoint=endpoint,
            )

            from companybrain.agents.knowledge_navigator_agent import KnowledgeNavigatorAgent
            from companybrain.agents.navigator_agent import NavigatorAgent

            agent = KnowledgeNavigatorAgent()
            result = await agent.navigate(
                entry_file=entry_file_str,
                entry_method=entry_method_name,
                endpoint=endpoint,
                http_method=method,
                repo_path=str(repo_path),
                repo_name=repo_name,
            )

            if result and result.knowledge:
                units = _knowledge_to_code_units(result, repo_path, repo_name)
                if units:
                    return units, entry_method_name

            # KnowledgeNavigatorAgent returned nothing — try import-graph agent
            fallback_agent = NavigatorAgent()
            nodes = await fallback_agent.discover(
                entry_file=entry_file_str,
                entry_method=entry_method_name,
                entry_class=entry_class,
                endpoint=endpoint,
                http_method=method,
                repo_path=str(repo_path),
                repo_name=repo_name,
            )

            if nodes:
                units = []
                for node in nodes:
                    try:
                        rel_path = str(Path(node.file_path).relative_to(repo_path))
                    except ValueError:
                        rel_path = node.file_path
                    units.append(CodeUnit(
                        file_path=rel_path,
                        repo_name=repo_name,
                        role=node.role,
                        language=_detect_language_from_path(entry_file_str),
                        content=node.to_code_unit_content(),
                        class_name=node.class_name,
                        imports=[],
                    ))
                return units, entry_method_name

            # Both agents failed — return just the handler file as a single unit
            if handler_content:
                return [self._make_unit(
                    Path(entry_file_str), repo_path, repo_name,
                    "controller", _detect_language_from_path(entry_file_str), handler_content
                )], entry_method_name

        # ── Step 2: Language-specific regex fallback ──────────────────────────
        log.info("LLMHandlerFinder found nothing — falling back to regex tracers", endpoint=endpoint)
        units = await self._trace_backend_regex_fallback(repo_path, repo_name, endpoint, method)
        return units, ""

    async def _trace_backend_regex_fallback(self, repo_path: Path, repo_name: str, endpoint: str, method: str) -> list[CodeUnit]:
        """Last-resort language detection + regex tracing."""
        def _count(pattern: str, limit: int = 0) -> int:
            n = 0
            for p in repo_path.rglob(pattern):
                if any(skip in p.parts for skip in _SKIP_DIRS):
                    continue
                n += 1
                if limit and n >= limit:
                    break
            return n

        java_count = _count("*.java")
        py_count   = _count("*.py")
        ts_count   = _count("*.ts", limit=50)

        if java_count >= py_count and java_count >= ts_count:
            return await self._trace_java(repo_path, repo_name, endpoint, method)
        elif py_count > ts_count:
            return self._trace_python(repo_path, repo_name, endpoint)
        else:
            return self._trace_typescript(repo_path, repo_name, endpoint)

    async def _trace_java(self, repo_path: Path, repo_name: str, endpoint: str, method: str = "GET") -> list[CodeUnit]:
        """
        Agentic codebase navigation via KnowledgeNavigatorAgent.

        The agent is language-agnostic: it uses read_file, find_class, search_code,
        and extract_method tools in a ReAct loop to navigate the full call chain.
        It understands any architecture pattern (Spring MVC, hexagonal, CQRS, DDD,
        FastAPI, NestJS, etc.) because the LLM does the structural reasoning.

        Strategy:
        1. Fast regex-based handler discovery to find the entry file + method (no LLM).
        2. KnowledgeNavigatorAgent navigates the chain with tool calls.
        3. Convert agent knowledge → CodeUnit list for the entity extractor.
        4. Fall back to import-graph NavigatorAgent if the knowledge agent fails.
        """
        from companybrain.agents.knowledge_navigator_agent import KnowledgeNavigatorAgent
        from companybrain.agents.navigator_agent import NavigatorAgent
        from companybrain.agents.tools.code_tools import find_entry_handler

        # ── Step 1: find entry handler (cheap regex, no LLM) ──────────────────
        entry = find_entry_handler(endpoint, method, str(repo_path))

        if entry:
            entry_file_str    = entry["file"]
            entry_class       = entry.get("class", "")
            entry_method_name = entry.get("method", "")
            handler_file      = Path(entry_file_str)
            try:
                handler_content = handler_file.read_text(errors="ignore")
            except OSError:
                handler_content = ""
        else:
            handler_file, handler_content = self._find_java_handler(repo_path, endpoint)
            if not handler_file:
                # Hybrid search fallback: find most relevant files using BM25 + dense
                search_results = await _get_hybrid_searcher().search(
                    query=f"{endpoint} {method}",
                    repo_name=repo_name,
                    repo_path=repo_path,
                    top_k=10,
                )
                if search_results:
                    # Use top result as handler
                    handler_file = repo_path / search_results[0].path
                    try:
                        handler_content = handler_file.read_text(errors="ignore")
                    except OSError:
                        handler_content = ""
                    log.info(
                        "HybridSearch: found handler candidate",
                        path=search_results[0].path,
                        score=search_results[0].score,
                        source=search_results[0].source,
                    )
                else:
                    log.info("No Java handler found", repo=repo_name, endpoint=endpoint)
                    return []
            entry_file_str    = str(handler_file)
            entry_class       = _extract_class_name(handler_content, "java")
            entry_method_name = ""

        log.info(
            "CodeTracer: KnowledgeNavigatorAgent starting",
            entry_file=str(Path(entry_file_str).relative_to(repo_path)
                           if Path(entry_file_str).is_absolute() else entry_file_str),
            entry_class=entry_class,
            entry_method=entry_method_name,
            endpoint=endpoint,
        )

        # ── Step 2: agentic navigation ────────────────────────────────────────
        agent = KnowledgeNavigatorAgent()
        result = await agent.navigate(
            entry_file=entry_file_str,
            entry_method=entry_method_name,
            endpoint=endpoint,
            http_method=method,
            repo_path=str(repo_path),
            repo_name=repo_name,
        )

        # ── Step 3a: convert agent result → CodeUnit list ─────────────────────
        if result and result.knowledge:
            units = _knowledge_to_code_units(result, repo_path, repo_name)
            if units:
                log.info(
                    "CodeTracer: KnowledgeNavigatorAgent complete",
                    units=len(units),
                    turns=result.turns_used,
                    files=result.files_visited,
                    roles=[u.role for u in units],
                )
                return units

        # ── Step 3b: fallback — import-graph NavigatorAgent ───────────────────
        log.warning(
            "KnowledgeNavigatorAgent: empty or failed — falling back to import-graph NavigatorAgent",
            endpoint=endpoint,
        )
        fallback_agent = NavigatorAgent()
        nodes = await fallback_agent.discover(
            entry_file=entry_file_str,
            entry_method=entry_method_name,
            entry_class=entry_class,
            endpoint=endpoint,
            http_method=method,
            repo_path=str(repo_path),
            repo_name=repo_name,
        )

        if not nodes:
            return self._trace_java_file_level(
                repo_path, repo_name, handler_file, handler_content, [], []
            )

        units: list[CodeUnit] = []
        for node in nodes:
            try:
                rel_path = str(Path(node.file_path).relative_to(repo_path))
            except ValueError:
                rel_path = node.file_path
            unit = CodeUnit(
                file_path=rel_path,
                repo_name=repo_name,
                role=node.role,
                language="java",
                content=node.to_code_unit_content(),
                class_name=node.class_name,
                imports=[],
            )
            units.append(unit)

        log.info(
            "CodeTracer: NavigatorAgent extraction complete",
            units=len(units),
            total_chars=sum(len(u.content) for u in units),
            estimated_tokens=sum(len(u.content) for u in units) // 4,
            roles=[u.role for u in units],
        )
        return units

    def _trace_java_file_level(
        self,
        repo_path: Path,
        repo_name: str,
        handler_file: Path,
        handler_content: str,
        service_files: list[tuple[Path, str]],
        repo_files: list[tuple[Path, str]],
    ) -> list[CodeUnit]:
        """
        Fallback: full-file extraction trimmed to MAX_UNIT_CHARS.
        Used when SmartMethodExtractor cannot find the handler method.
        """
        units = [self._make_unit(handler_file, repo_path, repo_name, "controller", "java", handler_content)]

        for svc_file, svc_content in service_files[:4]:
            if len(units) >= MAX_UNITS:
                break
            units.append(self._make_unit(svc_file, repo_path, repo_name,
                                         _infer_role(str(svc_file.stem)), "java", svc_content))

        for repo_file, repo_content in repo_files[:3]:
            if len(units) >= MAX_UNITS:
                break
            units.append(self._make_unit(repo_file, repo_path, repo_name,
                                         _infer_role(str(repo_file.stem)), "java", repo_content))

        return units

    def _find_java_handler(self, repo_path: Path, endpoint: str) -> tuple[Optional[Path], str]:
        """
        Scan Java files for the Spring Boot handler matching the endpoint.

        ADR-006 §28 — structural-index fast path:
          If StructuralIndexHelper is available, it narrows the candidate files to
          only Controller/Handler nodes that match endpoint segments.  The regex
          matching below (_JAVA_MAPPING_RE) then runs only against those files.
          Falls back to scanning ALL Java files if the structural index is empty.

        Three matching strategies (most to least strict):
          1. Literal path reconstruction — class @RequestMapping + method @*Mapping
          2. Fuzzy path variable substitution — replace {param} with a wildcard so
             paths like "/competitors/{payerType}" match "/competitors/payer"
          3. Keyword segment scoring — if the controller file name or content contains
             3+ unique endpoint segments, accept it as the best candidate

        The third strategy catches controllers that split routing across multiple
        levels (e.g. NiqModule → CompetitivenessModule → PayerController).
        """
        parts = [p for p in endpoint.split("/") if p and not re.match(r'^v\d+$', p) and p != "api"]
        candidates_lower = {
            ("/".join(parts[i:])).lower().rstrip("/")
            for i in range(len(parts))
        }
        # Also add prefixed variants
        candidates_lower |= {
            ("/" + "/".join(parts[i:])).lower().rstrip("/")
            for i in range(len(parts))
        }

        # Unique meaningful segments (skip very short ones that collide easily)
        keyword_segments = {p.lower() for p in parts if len(p) > 3}

        best_file: Optional[Path] = None
        best_score = 0
        best_content = ""

        # ADR-006 §28: Try structural index first — get candidate files from DB.
        # _JAVA_MAPPING_RE (DEPRECATED as primary scan) is now only applied to
        # structurally-identified candidates, not the entire repo.
        structural_candidates: set[Path] = set()
        if self._structural:
            try:
                raw_paths = self._structural.find_handler_candidates(endpoint, "java")
                for rp in raw_paths:
                    # Paths from DB may be relative; resolve against repo root.
                    candidate = repo_path / rp if not Path(rp).is_absolute() else Path(rp)
                    if candidate.exists():
                        structural_candidates.add(candidate)
                if structural_candidates:
                    log.debug(
                        "_find_java_handler: structural index narrowed to %d files",
                        len(structural_candidates),
                    )
            except Exception as exc:
                log.debug("_find_java_handler: structural index query failed: %s", exc)

        # File iterator: structural candidates first; fall back to controller-only scan.
        # We never scan the full repo — only files that look like controllers
        # (name contains Controller/Resource/Handler/Endpoint or path includes
        # "controller" directory). This reduces the scan from ~500 files to ~10-30.
        def _java_files():
            if structural_candidates:
                yield from structural_candidates
                return
            from companybrain.pipeline.file_walker import FileWalker
            walker = FileWalker(repo_path)
            _CTRL_HINTS = ("controller", "resource", "handler", "endpoint", "rest", "api")
            for info in walker.walk_by_language("java"):
                p = str(info.path).lower()
                if any(h in p for h in _CTRL_HINTS):
                    yield info.path

        for java_file in _java_files():
            try:
                content = java_file.read_text(errors="ignore")
            except OSError:
                continue

            if "@RestController" not in content and "@Controller" not in content:
                continue

            mappings = list(_JAVA_MAPPING_RE.finditer(content))
            if not mappings:
                continue

            first_method_pos = _find_first_method_pos(content)
            class_paths: list[str] = []
            method_paths: list[str] = []

            for match in mappings:
                path_val = match.group(2)
                if match.start() < first_method_pos:
                    class_paths.append(path_val.rstrip("/"))
                else:
                    method_paths.append(path_val.rstrip("/"))

            if not method_paths:
                method_paths = [""]

            # Strategy 1 + 2: literal and fuzzy path variable matching
            for cp in (class_paths or [""]):
                for mp in method_paths:
                    full_raw = (cp + mp).lower().rstrip("/")
                    # Literal match
                    if full_raw in candidates_lower or _path_tail_matches(full_raw, candidates_lower):
                        log.info("Found Java handler (literal)", file=str(java_file.relative_to(repo_path)), matched_path=cp + mp)
                        return java_file, content
                    # Fuzzy: replace {param} segments with the corresponding endpoint segment
                    fuzzy = re.sub(r'\{[^}]+\}', '[^/]+', full_raw)
                    for cand in candidates_lower:
                        if re.fullmatch(fuzzy, cand) or re.search(fuzzy, cand):
                            log.info("Found Java handler (fuzzy)", file=str(java_file.relative_to(repo_path)), matched_path=cp + mp)
                            return java_file, content

            # Strategy 3: keyword scoring — count how many endpoint segments appear in
            # file path + annotation paths (catches deeply nested routing hierarchies)
            file_text = (str(java_file).lower() + " " + " ".join(
                m.group(2).lower() for m in mappings
            ))
            score = sum(1 for seg in keyword_segments if seg in file_text)
            if score > best_score:
                best_score = score
                best_file = java_file
                best_content = content

        # Accept keyword-scored best candidate if it matches at least half the segments
        if best_file and best_score >= max(2, len(keyword_segments) // 2):
            log.info(
                "Found Java handler (keyword score)",
                file=str(best_file.relative_to(repo_path)),
                score=best_score,
                max_score=len(keyword_segments),
            )
            return best_file, best_content

        return None, ""

    # ── Python tracing ────────────────────────────────────────────────────────

    def _trace_python(self, repo_path: Path, repo_name: str, endpoint: str) -> list[CodeUnit]:
        """ADR-006 §28: uses structural index to narrow candidate files.
        _PY_ROUTE_RE (DEPRECATED as primary scan) is fallback only."""
        parts = [p for p in endpoint.split("/") if p and not re.match(r'^v\d+$', p) and p != "api"]
        candidates_lower = {("/" + "/".join(parts[i:])).lower() for i in range(len(parts))}

        # Structural-index fast path — narrow to handler-like Python files.
        structural_candidates: set[Path] = set()
        if self._structural:
            try:
                raw_paths = self._structural.find_handler_candidates(endpoint, "python")
                for rp in raw_paths:
                    candidate = repo_path / rp if not Path(rp).is_absolute() else Path(rp)
                    if candidate.exists():
                        structural_candidates.add(candidate)
            except Exception as exc:
                log.debug("_trace_python: structural index query failed: %s", exc)

        def _py_files():
            if structural_candidates:
                yield from structural_candidates
                return
            from companybrain.pipeline.file_walker import FileWalker
            walker = FileWalker(repo_path)
            _CTRL_HINTS = ("router", "route", "view", "endpoint", "api", "handler", "controller")
            for info in walker.walk_by_language("python"):
                p = str(info.path).lower()
                if any(h in p for h in _CTRL_HINTS):
                    yield info.path

        for py_file in _py_files():
            try:
                content = py_file.read_text(errors="ignore")
            except OSError:
                continue

            for _, path_val in _PY_ROUTE_RE.findall(content):
                if path_val.lower() in candidates_lower:
                    unit = self._make_unit(py_file, repo_path, repo_name, "controller", "python", content)
                    return [unit]
        return []

    # ── Frontend tracing ──────────────────────────────────────────────────────

    def _trace_frontend(self, repo_path: Path, repo_name: str, endpoint: str) -> list[CodeUnit]:
        """Find TypeScript/JS files that call the target API endpoint.
        Also find the React components that use those API clients.

        ADR-006 §28: structural index narrows to known API-caller files first.
        _TS_API_CALL_RE (DEPRECATED as primary scan) is only applied when
        the structural index returns no candidates (workspace not yet indexed).
        """
        parts = [p for p in endpoint.split("/") if p and not re.match(r'^v\d+$', p) and p != "api"]
        # Use the last 2-3 meaningful segments for matching (most specific)
        leaf_terms = set()
        for i in range(max(0, len(parts) - 3), len(parts)):
            leaf_terms.add("/".join(parts[i:]).lower())
        leaf_terms.add(endpoint.lower())

        units: list[CodeUnit] = []
        api_files_found: list[Path] = []

        # ADR-006 §28: structural-index fast path — find API-caller files from DB.
        structural_candidates: set[Path] = set()
        if self._structural:
            try:
                raw_paths = self._structural.find_api_caller_candidates(endpoint)
                for rp in raw_paths:
                    candidate = repo_path / rp if not Path(rp).is_absolute() else Path(rp)
                    if candidate.exists():
                        structural_candidates.add(candidate)
                if structural_candidates:
                    log.debug(
                        "_trace_frontend: structural index narrowed to %d files",
                        len(structural_candidates),
                    )
            except Exception as exc:
                log.debug("_trace_frontend: structural index query failed: %s", exc)

        def _candidate_files():
            """Yield candidate files — structural index if available, else API-caller scan."""
            if structural_candidates:
                yield from structural_candidates
                return
            from companybrain.pipeline.file_walker import FileWalker
            walker = FileWalker(repo_path)
            # Only scan files that are likely to contain API calls — not all TS/JS files
            _API_HINTS = ("api", "service", "client", "fetch", "http", "request", "hook", "query")
            for lang in ("typescript", "javascript"):
                for info in walker.walk_by_language(lang):
                    p = str(info.path).lower()
                    if any(h in p for h in _API_HINTS):
                        yield info.path

        for ts_file in _candidate_files():
            try:
                content = ts_file.read_text(errors="ignore")
            except OSError:
                continue

            # Infer language from extension
            suf = ts_file.suffix.lower()
            lang = "typescript" if suf in (".ts", ".tsx") else "javascript"

            # Check for API calls matching our endpoint.
            # When structural candidates are present, any file in the list is already
            # known to call/import the endpoint — no further matching needed.
            if structural_candidates:
                has_match = True
            else:
                # Full-scan fallback: apply _TS_API_CALL_RE (DEPRECATED as primary scan)
                has_match = False
                for call_path in _TS_API_CALL_RE.findall(content):
                    call_lower = call_path.lower().rstrip("/")
                    if any(call_lower.endswith(term) or term in call_lower for term in leaf_terms):
                        has_match = True
                        break
                # Plain string match as last resort
                if not has_match:
                    content_lower = content.lower()
                    if any(term in content_lower for term in leaf_terms if len(term) > 6):
                        has_match = True

            if has_match:
                role = _infer_role_from_path(ts_file)
                unit = self._make_unit(ts_file, repo_path, repo_name, role, lang, content)
                units.append(unit)
                api_files_found.append(ts_file)

            if len(units) >= MAX_UNITS:
                break

        return units

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_unit(
        self,
        file_path: Path,
        repo_path: Path,
        repo_name: str,
        role: str,
        language: str,
        content: str,
    ) -> CodeUnit:
        rel = str(file_path.relative_to(repo_path))
        trimmed = _trim_content(content)
        class_name = _extract_class_name(content, language)
        imports = _extract_imports(content, language)
        return CodeUnit(
            file_path=rel,
            repo_name=repo_name,
            role=role,
            language=language,
            content=trimmed,
            class_name=class_name,
            imports=imports,
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

def _knowledge_to_code_units(result, repo_path: Path, repo_name: str) -> list[CodeUnit]:
    """
    Convert KnowledgeNavigatorAgent result → CodeUnit list.

    Reads the actual source for each visited file so the entity extractor
    gets full, untruncated content (the agent only summarises; we need raw source).
    Also embeds the agent's structured knowledge as a header comment.

    File sources (unioned, deduped):
      1. result.files_visited  — files the agent explicitly called read_file on.
      2. knowledge.modules[].file — files referenced in the submitted schema;
         the agent may have located these via find_class without reading them.
    """
    from companybrain.pipeline.universal_code_extractor import _detect_language

    knowledge = result.knowledge
    units: list[CodeUnit] = []
    seen: set[str] = set()

    # Build role map from knowledge modules
    role_map: dict[str, str] = {}
    for mod in knowledge.get("modules", []):
        role_map[mod.get("file", "")] = mod.get("module_type", "unknown")
        role_map[mod.get("module_name", "")] = mod.get("module_type", "unknown")

    # Union: visited files + module files from the submitted schema
    candidate_paths: list[str] = list(result.files_visited)
    for mod in knowledge.get("modules", []):
        mf = mod.get("file", "")
        if mf and mf not in candidate_paths:
            # module file can be relative; resolve against repo_path
            candidate_paths.append(mf)

    def _emit(fp: str):
        """Resolve path, read source, build CodeUnit. Returns unit or None."""
        p = Path(fp)
        if not p.is_absolute():
            p = repo_path / fp
        if not p.exists():
            return None
        try:
            raw = p.read_text(errors="ignore")
        except Exception:
            return None
        rel = str(p.relative_to(repo_path)) if p.is_absolute() else fp
        role = role_map.get(rel) or role_map.get(p.stem) or _infer_role(p.stem)
        language = _detect_language(fp)
        agent_header = _build_agent_header(knowledge, rel, p.stem)
        content = agent_header + "\n\n" + _trim_content(raw)
        return CodeUnit(
            file_path=rel,
            repo_name=repo_name,
            role=role,
            language=language,
            content=content,
            class_name=p.stem,
            imports=[],
        )

    for fp in candidate_paths:
        # Deduplicate on resolved absolute path
        p = Path(fp)
        abs_key = str((repo_path / fp).resolve()) if not p.is_absolute() else str(p.resolve())
        if abs_key in seen:
            continue
        seen.add(abs_key)

        unit = _emit(fp)
        if unit:
            units.append(unit)
            if len(units) >= MAX_UNITS:
                break

    if units:
        log.debug(
            "_knowledge_to_code_units: built units",
            count=len(units),
            visited=len(result.files_visited),
            module_files=len(knowledge.get("modules", [])),
            roles=[u.role for u in units],
        )

    return units


def _build_agent_header(knowledge: dict, file_rel: str, stem: str) -> str:
    """Build a structured comment block from agent knowledge for one file."""
    lines = ["// ── Agent-extracted knowledge ───────────────────────────────────────"]

    # Find the module entry for this file
    for mod in knowledge.get("modules", []):
        if mod.get("file", "").endswith(stem) or mod.get("module_name") == stem:
            lines.append(f"// Module: {mod.get('module_name', stem)} [{mod.get('module_type', '')}]")
            if mod.get("description"):
                lines.append(f"// Purpose: {mod['description']}")
            for dep in mod.get("dependencies", []):
                lines.append(f"// Depends on: {dep['name']} ({dep.get('dep_type', '')}): {dep.get('how_used', '')}")
            for fn in mod.get("functions", []):
                lines.append(f"// Function {fn['name']} [{fn.get('intent_label', '')}]: {fn.get('description', '')}")
                if fn.get("data_reads"):
                    lines.append(f"//   reads: {', '.join(fn['data_reads'])}")
                if fn.get("data_writes"):
                    lines.append(f"//   writes: {', '.join(fn['data_writes'])}")
            for q in mod.get("db_queries", []):
                lines.append(f"// DB [{q.get('operation', '')}] {q.get('method', '')}: {q.get('query_text', '')[:100]}")
            break

    # Add endpoint info if this is the controller
    for ep in knowledge.get("endpoints", []):
        if ep.get("handler_module", "") == stem:
            lines.append(f"// Endpoint: {ep['http_method']} {ep['path']}")
            for p in ep.get("parameters", []):
                req = "required" if p.get("required") else f"optional default={p.get('default_value')}"
                lines.append(f"//   param {p['name']} [{p.get('kind', '')}] ({req}): {p.get('purpose', '')}")
                if p.get("valid_values"):
                    lines.append(f"//   valid_values: {', '.join(p['valid_values'])}")
                if p.get("is_multiselect"):
                    lines.append(f"//   MULTISELECT: yes")
            break

    lines.append("// ─────────────────────────────────────────────────────────────────")
    return "\n".join(lines)


def _trim_content(content: str) -> str:
    """
    Trim file content to MAX_UNIT_CHARS.
    Tries to end at a sensible boundary (end of a method/block).
    """
    if len(content) <= MAX_UNIT_CHARS:
        return content
    truncated = content[:MAX_UNIT_CHARS]
    # Try to end at last closing brace or blank line
    last_brace = truncated.rfind("\n}")
    last_blank = truncated.rfind("\n\n")
    cut = max(last_brace, last_blank)
    if cut > MAX_UNIT_CHARS // 2:
        return truncated[:cut] + "\n// ... (truncated)"
    return truncated + "\n// ... (truncated)"


def _extract_class_name(content: str, language: str) -> str:
    if language == "java":
        m = _JAVA_CLASS_RE.search(content)
        return m.group(1) if m else ""
    # TypeScript/JS: export default class Foo or export function Foo
    m = re.search(r'(?:export\s+)?(?:default\s+)?(?:class|function)\s+(\w+)', content)
    return m.group(1) if m else ""


def _extract_imports(content: str, language: str) -> list[str]:
    """Extract import specifiers from raw file content.

    ADR-006 §28: _TS_IMPORT_RE and _PY_IMPORT_RE are DEPRECATED as the
    primary extraction path. Prefer StructuralIndexHelper.get_import_targets()
    which queries the edges table (populated by the structural parser).

    This function remains as: (a) the fallback when no structural index is
    available, and (b) the Java import extractor (Java regex is unchanged).
    """
    if language == "java":
        return _JAVA_IMPORT_RE.findall(content)
    elif language in ("typescript", "javascript"):
        # DEPRECATED: regex scan — use StructuralIndexHelper.get_import_targets() instead.
        return _TS_IMPORT_RE.findall(content)
    elif language == "python":
        # DEPRECATED: regex scan — use StructuralIndexHelper.get_import_targets() instead.
        return [
            m.group(1) or m.group(2)
            for m in _PY_IMPORT_RE.finditer(content)
        ]
    return []


def _infer_role(class_name: str) -> str:
    for suffix, role in _LAYER_HINTS.items():
        if class_name.endswith(suffix):
            return role
    return "service"


def _infer_role_from_path(file_path: Path) -> str:
    parts_lower = [p.lower() for p in file_path.parts]
    if any(p in parts_lower for p in ("component", "components", "pages", "views", "screens")):
        return "component"
    if any(p in parts_lower for p in ("api", "services", "client", "clients", "hooks")):
        return "client"
    return "component"


def _find_first_method_pos(content: str) -> int:
    """
    Heuristic: find the character position of the first Java method declaration.
    Used to distinguish class-level vs method-level annotations.
    """
    # Look for lines like: "public ResponseEntity<...> methodName("
    m = re.search(
        r'(?:public|protected|private)\s+[\w<>?,\s\[\]]+\s+\w+\s*\(',
        content,
    )
    return m.start() if m else len(content)


def _detect_language_from_path(file_path: str) -> str:
    """Detect programming language from file extension."""
    suf = Path(file_path).suffix.lower()
    return {
        ".java":  "java",
        ".kt":    "kotlin",
        ".ts":    "typescript",
        ".tsx":   "typescript",
        ".js":    "javascript",
        ".jsx":   "javascript",
        ".py":    "python",
        ".rb":    "ruby",
        ".go":    "go",
        ".cs":    "csharp",
        ".cpp":   "cpp",
        ".c":     "c",
        ".rs":    "rust",
    }.get(suf, "unknown")


def _path_tail_matches(full_path: str, candidates: set[str]) -> bool:
    """
    Check if the tail of full_path matches any candidate.
    e.g. full_path = "competitiveness/summary/competitors/payer"
         candidates = {"/summary/competitors/payer", "competitors/payer", "payer"}
    """
    parts = full_path.strip("/").split("/")
    for i in range(len(parts)):
        tail = "/" + "/".join(parts[i:])
        if tail in candidates:
            return True
        tail_no_slash = "/".join(parts[i:])
        if tail_no_slash in candidates:
            return True
    return False
