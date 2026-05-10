"""
NavigatorAgent — two-phase codebase traversal.

Phase 1 — Pre-assembly (pure Python, zero LLM calls, ~50ms):
  Uses IMPORT-GRAPH traversal to walk the call chain:
    entry handler → parse Java imports → find @Service files
                  → parse service imports → find @Repository files
                  → include interface + implementation

  Why import-graph instead of call-chain?
    - Java imports are EXPLICIT and deterministic — no guessing from method bodies
    - Works with Lombok @RequiredArgsConstructor (constructor injection, no @Autowired)
    - Reaches @Repository interfaces even though they have no concrete method bodies
    - Doesn't break when a service method body is long, truncated, or delegates to helpers
    - call-chain traversal fails silently when body is truncated before the repo call

Phase 2 — Classification (single LLM call):
  The assembled code is shown to the LLM in one shot.
  The LLM assigns roles, identifies leaf nodes, writes discovery reasons.
  Output: structured NavigatorNode JSON.
  Full source from Phase 1 is injected BACK into nodes after classification —
  we don't trust the LLM to faithfully copy source content.

Architecture patterns handled automatically (LLM reasoning, not hardcoded):
  - Spring Boot MVC                    (Controller → Service → Repository)
  - Hexagonal / Ports & Adapters       (follows interface → finds @Component impl)
  - CQRS                               (CommandHandler → EventStore / QueryHandler)
  - NestJS / FastAPI / DDD             (any language the regex tools understand)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from companybrain.llm.base import ChatMessage, TaskRole, LLMProvider
from companybrain.llm import get_provider

log = structlog.get_logger(__name__)


# ── Output data model ──────────────────────────────────────────────────────────

@dataclass
class NavigatorNode:
    """
    A single node in the call chain discovered by the Navigator.
    """
    file_path: str          # absolute, verified to exist
    repo_name: str
    class_name: str
    method_name: str
    role: str               # controller | service | repository | client | model | unknown
    is_leaf: bool           # True = DB/external/stdlib — don't recurse
    discovery_reason: str   # why the Navigator included this
    hop_depth: int          # 0 = entry point, 1 = called by entry, etc.
    raw_source: str = ""    # method body / file content, set after extraction

    def brief(self) -> str:
        return f"{self.role}/{self.class_name}.{self.method_name}"

    def to_code_unit_content(self) -> str:
        """Format for entity extractor (replaces CodeUnit.content)."""
        lines = [
            f"// Role: {self.role} | Discovered: {self.discovery_reason}",
            self.raw_source or f"// {self.class_name}.{self.method_name} — source not extracted",
        ]
        return "\n".join(lines)


# ── Prompts ────────────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """You are classifying code nodes that form an API call chain.

For each code block shown, assign:
- role: one of controller | service | repository | client | model | event_handler | unknown
- is_leaf: true if this is a DB/external boundary (JpaRepository, EntityManager, JDBC,
           RestTemplate, WebClient, HTTP client, message queue publisher, stdlib utility).
           false if it contains business logic worth reading.
- discovery_reason: one concise sentence explaining why this node matters.
- hop_depth: already provided in the input — copy it unchanged.
- raw_source: copy the FULL code block content verbatim (do not truncate or summarise).

ROLE DEFINITIONS:
  controller    HTTP handler, @RestController, @Controller, @RequestMapping
  service       Business logic layer, @Service, @Component, use-case handler
  repository    Data access, @Repository, JPA/JDBC/ORM, query methods
  client        External HTTP call, Feign, RestTemplate, gRPC stub
  model         DTO, entity, value object, aggregate root
  event_handler Kafka consumer, @EventHandler, @SqsListener, @RabbitListener

Output ONLY a valid JSON object — no explanation, no markdown fences, nothing else:
{"nodes": [{"file_path": "...", "class_name": "...", "method_name": "...", "role": "...", "is_leaf": false, "discovery_reason": "...", "hop_depth": 0, "raw_source": "..."}]}"""


# ── Skip / utility sets ────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle", ".idea", ".vscode",
})

_UTILITY_OBJECTS = frozenset({
    # Logging — all languages
    "log", "logger", "LOG", "LOGGER", "Logger", "LoggerFactory",
    # Universal primitives / stdlib types (language-agnostic naming)
    "Optional", "String", "List", "Map", "Set", "Arrays",
    "Collections", "Stream", "CompletableFuture",
    "Math", "Integer", "Long", "Boolean", "Double",
    "UUID", "LocalDate", "LocalDateTime", "OffsetDateTime", "Instant",
    "BigDecimal",
    # Python builtins
    "None", "True", "False", "dict", "list", "set", "tuple", "str", "int",
    "float", "bool", "bytes", "object",
    # Generic utility
    "System", "Objects",
})

# Type-name suffixes we never follow as dependencies (language-agnostic)
_SKIP_SUFFIXES = (
    "DTO", "Dto", "Request", "Response",
    "Config", "Configuration", "Properties", "Settings",
    "Exception", "Error", "Constant", "Constants",
    "Util", "Utils", "Helper", "Helpers",
)

_CLASS_RE     = re.compile(r'(?:class|interface)\s+(\w+)')
_INTERFACE_RE = re.compile(r'\bpublic\s+interface\b')


# ── Agent ──────────────────────────────────────────────────────────────────────

class NavigatorAgent:
    """
    Discovers the call chain for an API endpoint using a two-phase approach:
      1. _assemble_chain() — Python tools, no LLM (import-graph traversal)
      2. _classify_chain() — single LLM call for role assignment
    """

    ROLE = TaskRole.BALANCED

    def __init__(self):
        self._provider: LLMProvider = get_provider()
        log.info(
            "NavigatorAgent ready",
            provider=self._provider.provider_name,
            model=self._provider.model_for_role(self.ROLE),
        )

    async def discover(
        self,
        entry_file: str,
        entry_method: str,
        entry_class: str,
        endpoint: str,
        http_method: str,
        repo_path: str,
        repo_name: str,
    ) -> list[NavigatorNode]:
        """
        Main entry point. Given the handler file + method, discover the full chain.
        """
        # ── Phase 1: deterministic import-graph assembly (no LLM) ─────────────
        assembled = self._assemble_chain(
            entry_file=entry_file,
            entry_method=entry_method,
            entry_class=entry_class,
            repo_path=repo_path,
            max_depth=3,
        )

        if not assembled:
            log.warning("NavigatorAgent: pre-assembly found nothing",
                        entry_file=entry_file, entry_method=entry_method)
            return []

        log.info(
            "NavigatorAgent: chain assembled",
            nodes=len(assembled),
            files=[Path(n["file_path"]).name for n in assembled],
        )

        # ── Phase 2: single LLM classification call ────────────────────────────
        nodes = await self._classify_chain(assembled, endpoint, http_method)

        # ── Post-process 1: inject full source from assembled (don't trust LLM) ─
        # The LLM only sees a summary for classification; the actual source comes
        # from our deterministic extraction above.
        source_by_file = {n["file_path"]: n["source"] for n in assembled}
        source_by_class = {n["class_name"]: n["source"] for n in assembled}
        for node in nodes:
            if not node.raw_source or len(node.raw_source) < 100:
                # Prefer assembled source over LLM-returned stub
                full_source = source_by_file.get(node.file_path) or \
                              source_by_class.get(node.class_name, "")
                if full_source:
                    node.raw_source = full_source

        # ── Post-process 2: verify paths, fill repo_name ──────────────────────
        verified: list[NavigatorNode] = []
        for node in nodes:
            p = Path(node.file_path)
            if not p.is_absolute():
                candidate = Path(repo_path) / node.file_path
                if candidate.exists():
                    node.file_path = str(candidate)
                else:
                    log.debug("NavigatorAgent: cannot resolve relative path, skipping",
                              path=node.file_path)
                    continue
            elif not p.exists():
                log.debug("NavigatorAgent: file does not exist, skipping",
                          path=node.file_path)
                continue

            node.repo_name = repo_name
            verified.append(node)

        log.info(
            "NavigatorAgent: discovery complete",
            total=len(nodes),
            verified=len(verified),
            roles=[n.role for n in verified],
        )
        return verified

    # ── Phase 1: import-graph chain assembly ───────────────────────────────────

    def _assemble_chain(
        self,
        entry_file: str,
        entry_method: str,
        entry_class: str,
        repo_path: str,
        max_depth: int = 3,
    ) -> list[dict]:
        """
        Walk the import graph using Java imports — zero LLM calls.

        Import-graph strategy (replaces brittle call-chain traversal):
          1. Start with the entry handler file (controller).
          2. Parse its Java imports; for each class in our package, find its file.
          3. Visit @Service / @Repository / @Component files — skip DTOs/config/errors.
          4. For @Repository interfaces: include the interface (shows @Query signatures)
             AND find + include the concrete implementation.
          5. Also scan @Autowired / constructor-injected fields as supplemental signal.
          6. Recurse up to max_depth.

        This reliably reaches Controller → Service → Repository even when:
          - Service method bodies are long and call helpers before the repo.
          - Repository is a Spring Data interface with no method bodies.
          - Lombok @RequiredArgsConstructor is used (no @Autowired annotations).
          - Methods delegate to private helpers before calling the repo.
        """
        from companybrain.agents.tools.code_tools import (
            extract_method, get_class_fields, find_file_by_name,
            find_implementations, list_methods, get_imports,
        )

        assembled: list[dict] = []
        visited_files: set[str] = set()   # normalised absolute paths

        # Detect project base package to filter internal vs external imports
        base_package = _detect_base_package(entry_file)
        log.debug("_assemble_chain: base package detected", package=base_package, entry=entry_file)

        # ── Helpers ────────────────────────────────────────────────────────────

        def _read(fp: str) -> str:
            try:
                return Path(fp).read_text(errors="ignore")
            except Exception:
                return ""

        def _cn(fp: str, content: str = "") -> str:
            text = (content or _read(fp))[:600]
            m = _CLASS_RE.search(text)
            return m.group(1) if m else Path(fp).stem

        def _is_interface(content: str) -> bool:
            return bool(_INTERFACE_RE.search(content[:1000]))

        def _best_source(fp: str, method_name: str, content: str) -> tuple[str, str]:
            """
            Return the richest possible source for a file:
              - For concrete classes: the specific handler method + full class header
              - For interfaces/repositories: the full file (all @Query/@Modifying sigs)
              - Fallback: first 3000 chars of the file
            """
            source = ""
            actual = method_name

            if method_name:
                source = extract_method(fp, method_name)

            if not source and method_name:
                # Try case-insensitive partial match
                all_methods = list_methods(fp)
                candidate = next(
                    (m["name"] for m in all_methods
                     if method_name.lower() in m["name"].lower()), None
                )
                if candidate:
                    source = extract_method(fp, candidate)
                    actual = candidate

            if not source or _is_interface(content):
                # Interface / no specific method: pass the FULL file content
                # through (captures all @Query annotations + method
                # signatures + jOOQ DSL chains).
                #
                # The hardcoded `cap = 6000` that used to live here was the
                # exact bug ADR-0045 was created to kill: every file logged
                # `source_len=6019` regardless of its real size, the
                # navigator's classifier saw a uniformly truncated view of
                # every class, and any method body past offset 6000 (e.g.
                # `getPayerCompetitors` in CompetitivenessPlanRepository.java
                # at line 584+) was invisible to downstream stages.
                #
                # ADR-0045 D2 says the chunker reads files directly from disk
                # via `file_path`, so `raw_source` no longer has to BE the
                # full file — but it can be, and the classifier benefits.
                # The total classification context is still bounded at
                # 12_000 chars by the cap below at the call site, so passing
                # the full body here is safe even for very large files.
                source = content
                actual = method_name or ""

            return source, actual

        def _dep_classes_from_file(fp: str, content: str) -> set[str]:
            """
            Collect candidate dependency class names from:
              (a) Java import statements that match our base package
              (b) @Autowired / final field declarations
            Returns simple class names (no package prefix).
            """
            dep_classes: set[str] = set()
            current_class = _cn(fp, content)

            # (a) Imports
            imports = get_imports(fp)
            for imp in imports:
                if base_package and not imp.startswith(base_package):
                    continue  # skip stdlib and third-party
                cls = imp.split(".")[-1]
                if _should_skip_class(cls, current_class):
                    continue
                dep_classes.add(cls)

            # (b) Field injection (handles Lombok @RequiredArgsConstructor)
            for field in get_class_fields(fp):
                raw_type = field["type"]
                # Strip generics: List<SomeRepo> → SomeRepo
                cls = re.sub(r'<.*>', '', raw_type).strip()
                if _should_skip_class(cls, current_class):
                    continue
                dep_classes.add(cls)

            return dep_classes

        def _should_skip_class(cls: str, current_class: str) -> bool:
            if not cls or cls == current_class:
                return True
            if cls in _UTILITY_OBJECTS:
                return True
            if any(cls.endswith(s) for s in _SKIP_SUFFIXES):
                return True
            # Skip single-letter generics (T, E, K, V)
            if len(cls) == 1:
                return True
            # Skip Java enum/constant classes: all-uppercase names like VIEW_BY, CONTRACTED_TYPE
            if cls.replace("_", "").isupper() and len(cls) > 1:
                return True
            return False

        def _find_real_file(dep_class: str) -> list[str]:
            """
            Find file(s) for a class, excluding test/generated/mock.
            Prefers EXACT stem matches (CompetitivenessService.java) over
            suffix matches (DefaultCompetitivenessService.java) — critical for
            hexagonal / ports-and-adapters architectures where the interface
            (port/in) lives alongside thin implementations.
            """
            files = find_file_by_name(dep_class, repo_path)
            real = [
                f for f in files
                if not any(s in Path(f).parts
                           for s in ("test", "generated", "mock", "Test"))
            ]
            candidates = real or files
            # Prefer exact stem match (ClassName.java vs PrefixClassName.java)
            exact = [f for f in candidates if Path(f).stem == dep_class]
            return exact if exact else candidates

        # ── Recursive visitor ──────────────────────────────────────────────────

        def visit(fp: str, method_name: str, class_name: str, depth: int):
            norm = str(Path(fp).resolve())
            if norm in visited_files or depth > max_depth or len(assembled) >= 15:
                return
            visited_files.add(norm)

            content = _read(fp)
            if not content:
                return

            cn = class_name or _cn(fp, content)
            source, actual_method = _best_source(fp, method_name, content)

            assembled.append({
                "file_path":   fp,
                "class_name":  cn,
                "method_name": actual_method,
                "source":      source,
                "depth":       depth,
            })

            log.debug(
                "_assemble_chain: visited",
                file=Path(fp).name,
                class_name=cn,
                depth=depth,
                source_len=len(source),
            )

            if depth >= max_depth:
                return

            # Collect dependency classes from imports + fields
            dep_classes = _dep_classes_from_file(fp, content)

            for dep_class in sorted(dep_classes):   # sorted for determinism
                if len(assembled) >= 15:
                    break

                dep_files = _find_real_file(dep_class)
                if not dep_files:
                    log.debug("_assemble_chain: dep file not found", dep_class=dep_class)
                    continue

                dep_fp = dep_files[0]
                dep_content = _read(dep_fp)

                if _is_interface(dep_content):
                    # Include the interface (shows @Query, method sigs)
                    visit(dep_fp, "", dep_class, depth + 1)
                    # Also find + include the concrete implementation
                    impls = find_implementations(dep_class, repo_path)
                    for impl_fp in impls[:1]:
                        impl_norm = str(Path(impl_fp).resolve())
                        if impl_norm not in visited_files:
                            visit(impl_fp, "", dep_class + "Impl", depth + 1)
                else:
                    visit(dep_fp, "", dep_class, depth + 1)

        visit(entry_file, entry_method, entry_class, 0)
        return assembled

    # ── Phase 2: single LLM classification ────────────────────────────────────

    async def _classify_chain(
        self,
        assembled: list[dict],
        endpoint: str,
        http_method: str,
    ) -> list[NavigatorNode]:
        """One LLM call: assembled code → NavigatorNode JSON."""
        code_blocks = []
        for node in assembled:
            # For classification we only need enough context to assign a role.
            # Cap at 2000 chars — enough to see class name, annotations, signatures.
            summary = node["source"]
            if len(summary) > 2000:
                summary = summary[:2000] + "\n// ... (truncated for classification)"
            code_blocks.append(
                f"=== Depth {node['depth']}: {node['class_name']}.{node['method_name']} ===\n"
                f"File: {node['file_path']}\n"
                f"{summary}\n"
            )

        code_context = "\n".join(code_blocks)

        # Hard cap on total classification context
        if len(code_context) > 12_000:
            code_context = code_context[:12_000] + "\n// ... (truncated)"

        user_msg = (
            f"Endpoint: {http_method} {endpoint}\n\n"
            f"Classify these {len(assembled)} code nodes:\n\n"
            f"{code_context}\n\n"
            f"Respond with ONLY the JSON object — no extra text."
        )

        messages = [
            ChatMessage(role="system", content=_CLASSIFY_SYSTEM),
            ChatMessage(role="user",   content=user_msg),
        ]

        log.debug(
            "NavigatorAgent: classification call",
            model=self._provider.model_for_role(self.ROLE),
            nodes=len(assembled),
            context_chars=len(code_context),
        )

        try:
            response = await self._provider.chat(
                messages=messages,
                role=self.ROLE,
                max_tokens=4096,
                temperature=0.0,
            )
            parsed = self.parse_output(response.content)
            if parsed:
                return parsed
            log.warning("NavigatorAgent: classification parse failed, using fallback",
                        preview=response.content[:200])
        except Exception as e:
            log.error("NavigatorAgent: classification call failed", error=str(e))

        return self._make_fallback_nodes(assembled)

    # ── Parsing & fallback ────────────────────────────────────────────────────

    def parse_output(self, text: str) -> list[NavigatorNode]:
        """Parse the LLM JSON output into NavigatorNode objects."""
        data = _parse_json(text)
        if not data:
            return []

        nodes_raw = data if isinstance(data, list) else data.get("nodes", [])
        nodes: list[NavigatorNode] = []

        for item in nodes_raw:
            try:
                nodes.append(NavigatorNode(
                    file_path=item["file_path"],
                    repo_name="",
                    class_name=item.get("class_name", ""),
                    method_name=item.get("method_name", ""),
                    role=item.get("role", "unknown"),
                    is_leaf=bool(item.get("is_leaf", False)),
                    discovery_reason=item.get("discovery_reason", ""),
                    hop_depth=int(item.get("hop_depth", 0)),
                    raw_source=item.get("raw_source", ""),
                ))
            except (KeyError, TypeError) as e:
                log.debug("NavigatorAgent: skipping malformed node",
                          error=str(e), item=item)

        return nodes

    def _make_fallback_nodes(self, assembled: list[dict]) -> list[NavigatorNode]:
        """Build basic nodes from assembled data when LLM classification fails."""
        _ROLE_HINTS = {
            "Controller": "controller", "Resource":   "controller",
            "Service":    "service",    "ServiceImpl":"service",
            "Repository": "repository", "Repo":       "repository",
            "Client":     "client",     "Adapter":    "client",
            "Gateway":    "client",
        }

        def _infer(class_name: str) -> str:
            for suffix, role in _ROLE_HINTS.items():
                if class_name.endswith(suffix):
                    return role
            return "unknown"

        return [
            NavigatorNode(
                file_path=n["file_path"],
                repo_name="",
                class_name=n["class_name"],
                method_name=n["method_name"],
                role=_infer(n["class_name"]),
                is_leaf=False,
                discovery_reason="Assembled via import-graph traversal (fallback classification)",
                hop_depth=n["depth"],
                raw_source=n["source"],
            )
            for n in assembled
        ]


# ── Module-level helpers ───────────────────────────────────────────────────────

def _detect_base_package(file_path: str) -> str:
    """
    Extract the project's base package from a Java file's package declaration.
    Returns the top 2–3 package segments, e.g. "com.companybrain".

    Used to distinguish internal imports (follow) from stdlib/third-party (skip).
    """
    try:
        content = Path(file_path).read_text(errors="ignore")[:500]
        m = re.search(r'^package\s+([\w.]+)\s*;', content, re.MULTILINE)
        if m:
            parts = m.group(1).split(".")
            # Return top 2 segments (com.companyname) to stay within the project
            return ".".join(parts[:2]) if len(parts) >= 2 else m.group(1)
    except Exception:
        pass
    return ""


def _parse_json(text: str) -> dict | list | None:
    """Strip markdown fences, extract and parse JSON. Returns None on failure."""
    import json
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return None
