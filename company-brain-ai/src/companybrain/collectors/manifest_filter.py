"""Three-layer hierarchical filter for huge monorepos (ADR-0050 M4).

Layer 1 (deterministic): group files by top-2 path segments, score by
        endpoint-keyword overlap + presence of @RestController/@Service.
Layer 2 (deterministic): within top packages, run hybrid search +
        drop pure DTOs (zero method bodies in AST).
Layer 3 (one LLM call): SpecialistAgent receives the surviving ≤20 files.

No truncation possible; layer 3's input is bounded by construction.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import structlog

log = structlog.get_logger(__name__)

_CONTROLLER_ANNOTATIONS = re.compile(
    r"@(RestController|Controller|RequestMapping|GetMapping|PostMapping|"
    r"PutMapping|DeleteMapping|Router|app\.route|router\.(get|post|put|delete))",
    re.IGNORECASE,
)
_SERVICE_ANNOTATIONS = re.compile(
    r"@(Service|Component|Provider|Injectable)",
    re.IGNORECASE,
)
_METHOD_BODY = re.compile(
    r"(def |public |private |protected |async )[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(",
)


class CandidateFile(NamedTuple):
    path: str
    role: str
    size_kb: int
    package_score: float
    bm25_score: float


async def build_filtered_manifest(
    repo_path: Path,
    endpoint: str,
    method: str,
    max_packages: int = 5,
    max_files: int = 20,
) -> list[CandidateFile]:
    """Build a bounded manifest of candidate files for SpecialistAgent.

    Returns at most `max_files` CandidateFile objects.
    Never calls an LLM — fully deterministic.
    """
    # Layer 1: score packages
    packages = _score_packages(repo_path, endpoint)[:max_packages]
    path_prefixes = [p[0] for p in packages]

    log.debug(
        "manifest_filter.layer1",
        repo=str(repo_path),
        endpoint=endpoint,
        top_packages=path_prefixes,
    )

    # Layer 2: bounded hybrid search within selected packages + DTO drop
    try:
        from companybrain.collectors.code_tracer import _get_hybrid_searcher
        searcher = _get_hybrid_searcher()
        raw = await searcher.search(
            query=f"{endpoint} {method}",
            repo_name=repo_path.name,
            repo_path=repo_path,
            top_k=max_files * 2,
            path_prefixes=path_prefixes,
        )
        surviving = [c for c in raw if not _is_pure_dto(Path(c.path))]
    except Exception as exc:
        # Hybrid search unavailable — do a cheap filesystem scan instead.
        log.warning("manifest_filter.hybrid_search_failed", error=str(exc))
        surviving = _filesystem_scan(repo_path, endpoint, path_prefixes, max_files * 2)

    candidates: list[CandidateFile] = []
    for c in surviving[:max_files]:
        path_str = getattr(c, "path", str(c)) if not isinstance(c, str) else c
        size_kb = 0
        try:
            size_kb = Path(path_str).stat().st_size // 1024
        except Exception:
            pass
        role = _infer_role(path_str)
        candidates.append(CandidateFile(
            path=path_str,
            role=role,
            size_kb=size_kb,
            package_score=getattr(c, "score", 0.0),
            bm25_score=getattr(c, "bm25_score", 0.0),
        ))

    log.info(
        "manifest_filter.done",
        total=len(candidates),
        endpoint=endpoint,
    )
    return candidates


# ── Layer 1 helpers ────────────────────────────────────────────────────────────

def _score_packages(repo_path: Path, endpoint: str) -> list[tuple[str, float]]:
    """Score top-2-segment path groups by keyword overlap with endpoint."""
    endpoint_tokens = set(re.split(r"[/_\-.]", endpoint.lower()))
    endpoint_tokens.discard("")

    groups: dict[str, float] = {}

    # Walk source roots (src/main/java, src/, app/, lib/)
    for src_root in _find_source_roots(repo_path):
        for f in src_root.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(src_root)
            parts = rel.parts
            key = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]

            if key not in groups:
                groups[key] = 0.0

            # Score by filename overlap with endpoint tokens
            name_tokens = set(re.split(r"[/_\-.]", f.stem.lower()))
            overlap = len(endpoint_tokens & name_tokens)
            groups[key] += overlap * 0.5

            # Bonus for controller/service annotations in first 1KB
            try:
                snippet = f.read_text(errors="ignore")[:1024]
                if _CONTROLLER_ANNOTATIONS.search(snippet):
                    groups[key] += 2.0
                if _SERVICE_ANNOTATIONS.search(snippet):
                    groups[key] += 1.0
            except Exception:
                pass

    return sorted(groups.items(), key=lambda x: x[1], reverse=True)


def _is_pure_dto(path: Path) -> bool:
    """Return True if file has no method bodies — likely a pure DTO / value object."""
    try:
        content = path.read_text(errors="ignore")
        return not bool(_METHOD_BODY.search(content))
    except Exception:
        return False


def _infer_role(path: str) -> str:
    """Infer controller / service / repository / model from path stem."""
    stem = Path(path).stem.lower()
    if any(s in stem for s in ("controller", "resource", "handler", "endpoint", "router")):
        return "controller"
    if any(s in stem for s in ("service", "manager", "engine", "processor")):
        return "service"
    if any(s in stem for s in ("repository", "dao", "store", "persistence", "repo")):
        return "repository"
    return "model"


def _find_source_roots(repo_path: Path) -> list[Path]:
    candidates = [
        repo_path / "src" / "main" / "java",
        repo_path / "src" / "main" / "kotlin",
        repo_path / "src",
        repo_path / "app",
        repo_path / "lib",
        repo_path,
    ]
    return [p for p in candidates if p.is_dir()]


def _filesystem_scan(
    repo_path: Path,
    endpoint: str,
    path_prefixes: list[str],
    max_files: int,
) -> list:
    """Cheap filesystem fallback when hybrid search is unavailable."""
    tokens = set(re.split(r"[/_\-.]", endpoint.lower()))
    tokens.discard("")
    results = []
    for src_root in _find_source_roots(repo_path):
        for f in src_root.rglob("*.java"):
            if len(results) >= max_files:
                break
            name_tokens = set(re.split(r"[/_\-.]", f.stem.lower()))
            if tokens & name_tokens:
                results.append(type("_R", (), {"path": str(f), "score": 0.5})())
    return results
