"""
FileWalker — smart file enumerator for extraction passes.

Implements the directive's pre-extraction filters:
  • Respect .gitignore (and .cbignore for project-specific overrides)
  • Detect generated files via header markers and filename patterns
  • File size cap: skip > 500 KB, flag > 100 KB for review
  • Skip: node_modules, vendor, dist, build, .git, __pycache__, target/
  • Language detection from extension
  • Index lockfiles as ExternalDependency metadata only (no body extraction)

Usage::
    walker = FileWalker(repo_root=Path("/path/to/repo"))
    for file_info in walker.walk():
        if file_info.should_extract:
            # send to extraction pipeline
            pass
        elif file_info.is_lockfile:
            # index as ExternalDependency only
            pass

Frugality tier: Tier 1 (deterministic, zero LLM cost).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional
import structlog

log = structlog.get_logger(__name__)

# ── Skip directories ───────────────────────────────────────────────────────────
# These are never walked, regardless of .gitignore.
SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", "target",           # compiled outputs
    "vendor",                             # Go / Ruby / PHP vendored deps
    ".venv", "venv", "env", ".env",       # Python virtualenvs
    "coverage", ".coverage",
    ".idea", ".vscode", ".eclipse",
    "generated", "gen", "auto-generated",
    ".next", ".nuxt", ".svelte-kit",      # framework build outputs
    "storybook-static",
    "migrations",                          # DB migrations: index separately
})

# ── File size thresholds ───────────────────────────────────────────────────────
MAX_FILE_BYTES    = 500_000   # 500 KB: skip entirely
REVIEW_FILE_BYTES = 100_000   # 100 KB: flag for review but still extract

# ── Lockfile patterns — index as ExternalDependency, skip body extraction ─────
LOCKFILE_NAMES: frozenset[str] = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Gemfile.lock", "Pipfile.lock", "poetry.lock", "uv.lock",
    "go.sum", "Cargo.lock", "composer.lock", "packages.lock.json",
    "gradle.lockfile",
})

# ── Extractable extensions ─────────────────────────────────────────────────────
EXTRACTABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".java", ".kt", ".scala",             # JVM
    ".py",                                # Python
    ".ts", ".tsx", ".js", ".jsx", ".mts", # TypeScript / JavaScript
    ".go",                                # Go
    ".cs",                                # C#
    ".rb",                                # Ruby
    ".swift",                             # Swift
    ".rs",                                # Rust
    ".sql",                               # SQL migrations
    ".graphql", ".gql",                   # GraphQL schemas
    ".proto",                             # Protocol Buffers
    ".yaml", ".yml",                      # OpenAPI, k8s, config
    ".json",                              # OpenAPI, package.json
    ".toml",                              # Cargo.toml, pyproject.toml
})

# ── ADR-0057 additions ────────────────────────────────────────────────────────
# Universal File Extraction expands the walker beyond code to docs, configs,
# infra, CI, and manifests. The router in companybrain.extractors.dispatch is
# the ground truth — this set is a coarse pre-filter so we don't stat every
# binary in the repo.

EXTENSIONLESS_EXTRACTABLE_NAMES: frozenset[str] = frozenset({
    "Dockerfile", "Makefile", "GNUmakefile", "Procfile", "Jenkinsfile",
    "go.mod",
})

UNIVERSAL_EXTRA_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".markdown", ".adoc", ".asciidoc", ".rst", ".txt",
    ".properties", ".env",
    ".xml",     # POM / general XML configs
    ".tf",      # Terraform (shallow; deep extraction owned by ADR-0058)
    # ── ADR-0061 additions ──────────────────────────────────────────────
    # Diagram-image files become Diagram entities via diagram_extractor.py.
    # The dispatcher's ``supports()`` constrains them to docs/**/ so a
    # top-level logo.png doesn't accidentally trigger a vision call.
    ".png", ".svg",
})

# Set used by the walker after ADR-0057. Code-only callers still consume
# EXTRACTABLE_EXTENSIONS; universal callers consume this combined view.
UNIVERSAL_EXTRACTABLE_EXTENSIONS: frozenset[str] = (
    EXTRACTABLE_EXTENSIONS | UNIVERSAL_EXTRA_EXTENSIONS
)

# ── Generated file detection ──────────────────────────────────────────────────
# Files matching any of these patterns are classified as GeneratedFile.
# They are indexed with entity_type=GeneratedFile but body extraction is skipped.

_GENERATED_NAME_PATTERNS: list[re.Pattern] = [
    re.compile(r'\.generated\.(ts|js|java|cs|go|py)$', re.IGNORECASE),
    re.compile(r'\.gen\.(ts|js|java|go)$', re.IGNORECASE),
    re.compile(r'_generated\.(ts|js|java|go|py)$', re.IGNORECASE),
    re.compile(r'^schema\.graphql$', re.IGNORECASE),    # auto-generated schemas
    re.compile(r'\.pb\.go$'),                            # protobuf Go
    re.compile(r'_pb2\.py$'),                            # protobuf Python
    re.compile(r'\.pb\.ts$'),                            # protobuf TypeScript
    re.compile(r'grpc\.pb\.go$'),
    re.compile(r'openapi\.json$', re.IGNORECASE),        # generated OpenAPI specs
    re.compile(r'swagger\.json$', re.IGNORECASE),
    re.compile(r'\.d\.ts$'),                             # TypeScript declaration files
]

_GENERATED_HEADER_MARKERS: list[bytes] = [
    b"// Code generated",
    b"// DO NOT EDIT",
    b"// This file is auto-generated",
    b"/* Auto-generated",
    b"/* Generated by",
    b"# This file is auto-generated",
    b"# Code generated",
    b"# DO NOT EDIT",
    b"* This file was automatically generated",
    b"@javax.annotation.Generated",
    b"@jakarta.annotation.Generated",
    b"@Generated(",
]

_GENERATED_HEADER_READ_BYTES = 512   # Only read first 512 bytes to check


@dataclass
class FileInfo:
    """Metadata about a discovered file, including extraction eligibility."""
    path: Path
    relative_path: str          # relative to repo_root, forward slashes
    size_bytes: int
    language: str               # "java" | "python" | "typescript" | etc.
    is_generated: bool = False
    is_lockfile: bool = False
    is_oversized: bool = False  # > MAX_FILE_BYTES
    is_large: bool = False      # > REVIEW_FILE_BYTES (flag but still extract)
    skip_reason: str = ""       # non-empty if should_extract is False
    # ADR-0057: which universal extractor (if any) handles this file.
    # "code" for source-code units handled by the existing chunker;
    # "doc"/"config"/"manifest"/"infra"/"ci"/"javadoc"/"test_spec" for the
    # universal extractors; "" when no extractor claims the file.
    extractor_kind: str = ""

    @property
    def should_extract(self) -> bool:
        return not self.is_generated and not self.is_lockfile and not self.is_oversized

    @property
    def extraction_tier(self) -> str:
        """Tier label for telemetry."""
        if self.is_generated:  return "generated"
        if self.is_lockfile:   return "lockfile"
        if self.is_oversized:  return "oversized"
        if self.is_large:      return "large"
        return "normal"


class FileWalker:
    """
    Enumerates files in a repository for extraction.

    Order of filters (cheapest first):
    1. Directory skip list (SKIP_DIRS)
    2. .gitignore + .cbignore rules
    3. Extension filter (EXTRACTABLE_EXTENSIONS)
    4. Lockfile detection (filename exact match)
    5. Size check (stat call)
    6. Generated file detection (name pattern, then header peek)
    """

    def __init__(
        self,
        repo_root: Path,
        extra_skip_dirs: Optional[set[str]] = None,
        respect_gitignore: bool = True,
    ):
        self.repo_root = repo_root.resolve()
        self._extra_skip_dirs = extra_skip_dirs or set()
        self._gitignore_matcher = None

        if respect_gitignore:
            self._gitignore_matcher = self._load_gitignore(repo_root)

    def walk(self) -> Iterator[FileInfo]:
        """
        Yield FileInfo for every candidate file in the repo.
        Includes generated files and lockfiles (caller checks should_extract).
        Excludes files in SKIP_DIRS or matched by .gitignore.
        """
        skip_dirs = SKIP_DIRS | self._extra_skip_dirs
        walked = 0
        skipped_dir = 0
        skipped_ignore = 0

        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue

            # 1. Skip directory check (fast — O(depth))
            rel = path.relative_to(self.repo_root)
            if any(part in skip_dirs for part in rel.parts[:-1]):
                skipped_dir += 1
                continue

            # 2. .gitignore check
            if self._gitignore_matcher and self._is_gitignored(path, rel):
                skipped_ignore += 1
                continue

            # 3. Extension filter — only process known source file types
            if path.suffix.lower() not in EXTRACTABLE_EXTENSIONS:
                continue

            # 4. Build FileInfo
            info = self._classify(path, rel)
            walked += 1
            yield info

        log.info(
            "FileWalker complete",
            repo=str(self.repo_root),
            walked=walked,
            skipped_dir=skipped_dir,
            skipped_gitignore=skipped_ignore,
        )

    def _classify(self, path: Path, rel: Path) -> FileInfo:
        """Classify a single file into a FileInfo."""
        rel_str = str(rel).replace("\\", "/")
        size = path.stat().st_size
        lang = _detect_language(path)

        is_lockfile  = path.name in LOCKFILE_NAMES
        is_oversized = size > MAX_FILE_BYTES
        is_large     = size > REVIEW_FILE_BYTES
        is_generated = False
        skip_reason  = ""

        if is_lockfile:
            skip_reason = "lockfile"
        elif is_oversized:
            skip_reason = f"oversized ({size // 1024}KB > {MAX_FILE_BYTES // 1024}KB)"
            log.debug("Skipping oversized file", path=rel_str, size_kb=size // 1024)
        else:
            # Generated detection — name first (cheap), then header peek (slightly more expensive)
            is_generated = _is_generated_by_name(path.name) or _is_generated_by_header(path)
            if is_generated:
                skip_reason = "generated"

        return FileInfo(
            path=path,
            relative_path=rel_str,
            size_bytes=size,
            language=lang,
            is_generated=is_generated,
            is_lockfile=is_lockfile,
            is_oversized=is_oversized,
            is_large=is_large and not is_oversized,
            skip_reason=skip_reason,
        )

    def walk_extractable(self) -> Iterator[FileInfo]:
        """Convenience: yield only files where should_extract is True."""
        for info in self.walk():
            if info.should_extract:
                yield info

    def walk_universal(self) -> Iterator[FileInfo]:
        """
        ADR-0057: yield every file claimed by any extractor in the
        ``companybrain.extractors`` dispatch, including docs, configs,
        infra, CI, and manifests. Code files are also included with
        ``extractor_kind`` set to ``"code"`` so callers can route them
        to the existing chunker.

        Filters (cheapest first):
          1. Directory skip list
          2. .gitignore + .cbignore
          3. Pre-filter: extension in UNIVERSAL_EXTRACTABLE_EXTENSIONS
             OR name in EXTENSIONLESS_EXTRACTABLE_NAMES
             OR file lives under .github/workflows
          4. Classify via FileInfo, then ask the extractor dispatch what
             kind of file this is. Files not claimed by any extractor are
             skipped.
        """
        from companybrain.extractors.dispatch import extractor_kind_for

        skip_dirs = SKIP_DIRS | self._extra_skip_dirs
        walked = 0

        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue

            rel = path.relative_to(self.repo_root)
            if any(part in skip_dirs for part in rel.parts[:-1]):
                continue
            if self._gitignore_matcher and self._is_gitignored(path, rel):
                continue

            # Pre-filter
            suffix = path.suffix.lower()
            name = path.name
            parts_lower = [p.lower() for p in rel.parts]
            in_workflows = ".github" in parts_lower and "workflows" in parts_lower
            if (
                suffix not in UNIVERSAL_EXTRACTABLE_EXTENSIONS
                and name not in EXTENSIONLESS_EXTRACTABLE_NAMES
                and not name.startswith("Dockerfile.")
                and not name.startswith("docker-compose")
                and not in_workflows
                and not name.startswith(".env")
            ):
                continue

            info = self._classify(path, rel)

            # Primary routing: source-code files (excluding ambiguous data
            # formats like .yml/.json/.toml/.xml which the dispatch claims as
            # config/manifest) are always "code" — the existing chunker handles
            # them. Javadoc / test_spec extraction is a SECONDARY pass that
            # runs alongside code extraction, so it's not the primary kind here.
            if suffix in EXTRACTABLE_EXTENSIONS and suffix not in (
                ".yml", ".yaml", ".json", ".toml", ".xml"
            ):
                kind = "code"
            else:
                kind = extractor_kind_for(path) or ""

            if not kind:
                continue

            info.extractor_kind = kind
            walked += 1
            yield info

        log.info("FileWalker (universal) complete", repo=str(self.repo_root), walked=walked)

    def walk_by_language(self, language: str) -> Iterator[FileInfo]:
        """Yield only extractable files for a given language."""
        for info in self.walk_extractable():
            if info.language == language:
                yield info

    def stats(self) -> dict:
        """
        Walk the repo and return summary statistics without yielding FileInfos.
        Useful for pre-flight checks.
        """
        counts: dict[str, int] = {
            "total": 0, "extractable": 0, "generated": 0,
            "lockfile": 0, "oversized": 0, "large": 0,
        }
        by_language: dict[str, int] = {}

        for info in self.walk():
            counts["total"] += 1
            counts[info.extraction_tier] = counts.get(info.extraction_tier, 0) + 1
            if info.should_extract:
                counts["extractable"] += 1
                by_language[info.language] = by_language.get(info.language, 0) + 1

        return {"counts": counts, "by_language": by_language}

    # ── gitignore loading ─────────────────────────────────────────────────────

    def _load_gitignore(self, repo_root: Path):
        """Load .gitignore and .cbignore rules. Returns matcher or None."""
        try:
            import gitignore_parser  # type: ignore
            rules_files = []
            for name in (".gitignore", ".cbignore"):
                p = repo_root / name
                if p.exists():
                    rules_files.append(p)
            if not rules_files:
                return None
            # Merge all rules files into one matcher
            matchers = [gitignore_parser.parse_gitignore(f) for f in rules_files]
            def combined(path_str: str) -> bool:
                return any(m(path_str) for m in matchers)
            return combined
        except ImportError:
            log.debug("gitignore-parser not installed — .gitignore not respected")
            return None
        except Exception as e:
            log.debug("Failed to load .gitignore", error=str(e))
            return None

    def _is_gitignored(self, path: Path, rel: Path) -> bool:
        if self._gitignore_matcher is None:
            return False
        try:
            return self._gitignore_matcher(str(path))
        except Exception:
            return False


# ── Module-level helpers ───────────────────────────────────────────────────────

def _detect_language(path: Path) -> str:
    return {
        ".java": "java", ".kt": "kotlin", ".scala": "scala",
        ".py": "python",
        ".ts": "typescript", ".tsx": "typescript",
        ".mts": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".go": "go",
        ".cs": "csharp",
        ".rb": "ruby",
        ".swift": "swift",
        ".rs": "rust",
        ".sql": "sql",
        ".graphql": "graphql", ".gql": "graphql",
        ".proto": "protobuf",
        ".yaml": "yaml", ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
    }.get(path.suffix.lower(), "other")


def _is_generated_by_name(filename: str) -> bool:
    """Fast name-based generated file check (no I/O)."""
    return any(pat.search(filename) for pat in _GENERATED_NAME_PATTERNS)


def _is_generated_by_header(path: Path) -> bool:
    """
    Peek at the first 512 bytes of a file for generated-file header markers.
    Only called when name-based check didn't match.
    """
    try:
        with path.open("rb") as f:
            header = f.read(_GENERATED_HEADER_READ_BYTES)
        return any(marker in header for marker in _GENERATED_HEADER_MARKERS)
    except Exception:
        return False
