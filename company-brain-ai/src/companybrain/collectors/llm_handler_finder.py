"""
llm_handler_finder.py — Framework-agnostic entry-point discovery via LLM.

Replaces regex annotation matching with a two-step approach:

  Step 1 (cheap, deterministic):
    Scan the repo for files likely to contain HTTP handlers.
    Uses file-name heuristics (Controller, Router, Handler, routes.*, views.*)
    combined with endpoint keyword presence — no framework assumptions.

  Step 2 (LLM, fast model):
    Send stripped signatures from the top candidates to the LLM and ask:
    "Which file and method handles POST /api/v1/...?"

    Only signatures are sent (not full bodies), so this costs ~500–800 tokens
    regardless of file size.  The FAST model (llama-3.1-8b-instant on Groq)
    handles this reliably.

Works with:  Spring Boot, NestJS, Express, FastAPI, Flask, Django, Go chi/gin,
             Rails, Laravel, Phoenix, or any custom routing scheme.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import structlog

from companybrain.llm import get_provider, TaskRole, ChatMessage

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle",
    ".venv", "venv", "env", "site-packages",
    ".tox", ".mypy_cache", "vendor",
})

_CODE_EXTS = {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".php"}

# File name fragments that strongly suggest an HTTP entry-point file.
_HANDLER_NAME_FRAGMENTS = re.compile(
    r"(controller|router|route|handler|resource|endpoint|view|api|rest|web|gateway|facade)",
    re.IGNORECASE,
)

# Test file patterns — these get a score penalty so real handlers rank first.
_TEST_FILE_RE = re.compile(
    r"(Test|Tests|Spec|IT|Mock|Stub|Fake|Fixture|IntegrationTest)$",
    re.IGNORECASE,
)

# Directory names that signal production handler code (bonus score).
_HANDLER_PATH_DIRS = frozenset({
    "controller", "controllers", "handler", "handlers",
    "router", "routers", "route", "routes",
    "resource", "resources", "view", "views",
    "web", "api", "rest", "endpoint", "endpoints",
    "adapter", "adapters",
})

# Max lines to extract from each candidate file for the LLM prompt.
_MAX_SIGNATURE_LINES = 60

# How many candidate files to send to the LLM (token budget: ~800 tokens total).
_MAX_CANDIDATES = 8


# ── Public API ────────────────────────────────────────────────────────────────

async def find_entry_handler_llm(
    repo_path: Path,
    endpoint: str,
    http_method: str = "GET",
) -> Optional[dict]:
    """
    Identify the HTTP handler file + method for `endpoint` using an LLM.

    Returns a dict::

        {
            "file": "<absolute path>",
            "class": "<ClassName or ''>",
            "method": "<methodName or ''>",
            "confidence": 0.0–1.0,
        }

    or ``None`` if no handler is found with sufficient confidence.
    """
    candidates = _find_candidates(repo_path, endpoint)
    if not candidates:
        log.info("LLMHandlerFinder: no candidate files found", endpoint=endpoint)
        return None

    log.info(
        "LLMHandlerFinder: scanning candidates",
        count=len(candidates),
        top=[str(c.relative_to(repo_path)) for c in candidates[:4]],
        endpoint=endpoint,
    )

    file_blocks = _build_signature_blocks(candidates, repo_path)
    if not file_blocks:
        return None

    result = await _ask_llm(file_blocks, endpoint, http_method)

    if result and result.get("file") and result.get("confidence", 0) >= 0.5:
        # Resolve to absolute path
        raw_file = result["file"]
        abs_path = Path(raw_file) if Path(raw_file).is_absolute() else repo_path / raw_file
        if not abs_path.exists():
            # Try matching by filename stem across candidates
            stem = Path(raw_file).stem
            for c in candidates:
                if c.stem == stem:
                    abs_path = c
                    break
            else:
                log.warning("LLMHandlerFinder: resolved file not found", file=raw_file)
                return None

        result["file"] = str(abs_path)
        log.info(
            "LLMHandlerFinder: handler identified",
            file=str(abs_path.relative_to(repo_path)),
            method=result.get("method", ""),
            confidence=result.get("confidence"),
        )
        return result

    log.info("LLMHandlerFinder: no handler identified with sufficient confidence", endpoint=endpoint)
    return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_candidates(repo_path: Path, endpoint: str) -> list[Path]:
    """
    Return up to _MAX_CANDIDATES files most likely to contain the handler.

    Scoring:
      +4  file stem matches _HANDLER_NAME_FRAGMENTS AND a parent dir is a handler layer
      +3  file stem matches _HANDLER_NAME_FRAGMENTS
      +2  a parent directory name is a known handler layer (controller/, routes/, etc.)
      +2  endpoint keyword segment appears in the file path
      +1  endpoint keyword segment appears in file content
      -3  file is a test/spec/mock file (suffix pattern)
      -2  file is under test/, tests/, __tests__/ directory

    Only files with score > 0 after penalties are considered.
    """
    segments = [
        s.lower() for s in endpoint.split("/")
        if s and not re.match(r"^(api|v\d+|rest|public|private|internal|mcheck)$", s, re.I)
        and len(s) > 2
    ]

    scored: list[tuple[int, Path]] = []

    for f in repo_path.rglob("*"):
        if f.suffix not in _CODE_EXTS:
            continue
        if any(skip in f.parts for skip in _SKIP_DIRS):
            continue

        score = 0
        parts_lower = [p.lower() for p in f.parts]

        # ── Penalties (applied first to short-circuit clearly wrong files) ──
        if _TEST_FILE_RE.search(f.stem):
            score -= 3
        if any(p in ("test", "tests", "__tests__", "spec", "specs", "mock", "mocks") for p in parts_lower):
            score -= 2

        # ── Handler file-name signal ──
        stem_is_handler = bool(_HANDLER_NAME_FRAGMENTS.search(f.stem))
        parent_is_handler_dir = any(p in _HANDLER_PATH_DIRS for p in parts_lower)

        if stem_is_handler and parent_is_handler_dir:
            score += 4   # e.g. controller/CompetitivenessController.java
        elif stem_is_handler:
            score += 3
        elif parent_is_handler_dir:
            score += 2

        # ── Endpoint keyword in file path ──
        path_lower = str(f).lower()
        matched_segs = sum(1 for seg in segments if seg in path_lower)
        score += min(matched_segs, 2) * 2   # up to +4 for path matches

        # Short-circuit: don't read content if already disqualified
        if score <= 0:
            continue

        # ── Endpoint keyword in file content ──
        try:
            content_lower = f.read_text(errors="ignore").lower()
            if any(seg in content_lower for seg in segments):
                score += 1
        except OSError:
            pass

        if score > 0:
            scored.append((score, f))

    # Sort by score desc, then path depth asc (shallower = less likely to be a helper)
    scored.sort(key=lambda t: (-t[0], len(t[1].parts)))
    return [f for _, f in scored[:_MAX_CANDIDATES]]


def _build_signature_blocks(candidates: list[Path], repo_path: Path) -> list[str]:
    """
    For each candidate, extract just the class/method signatures (not bodies).
    This keeps the LLM prompt compact — ~100–200 tokens per file.
    """
    blocks: list[str] = []
    for f in candidates:
        try:
            content = f.read_text(errors="ignore")
        except OSError:
            continue

        signatures = _extract_signatures(content, f.suffix)
        if not signatures.strip():
            continue

        rel = str(f.relative_to(repo_path))
        blocks.append(f"### {rel}\n```\n{signatures}\n```")

    return blocks


def _extract_signatures(content: str, suffix: str) -> str:
    """
    Extract class declarations, method/function signatures, and route annotations.
    Skips method bodies to keep token count low.

    Strategy: keep lines that look like declarations, annotations, decorators,
    or route registrations. Drop pure implementation lines.
    """
    lines = content.splitlines()
    kept: list[str] = []
    brace_depth = 0

    # Patterns that indicate a "declaration" line worth keeping
    decl_re = re.compile(
        r"""
        @\w+                                    # annotation / decorator
        | ^\s*(public|private|protected|static|async|export|def|func|fn)\s  # visibility/def
        | ^\s*(class|interface|enum|record|struct|type)\s                    # type decl
        | ^\s*(router|app|Route|r)\.(get|post|put|delete|patch|use)\s*\(    # Express/Go/Flask routing
        | \bRequestMapping\b|\bGetMapping\b|\bPostMapping\b                  # Spring (seen in annotation lines)
        | ^\s*route\(|^\s*path\(                                             # generic route DSL
        """,
        re.VERBOSE | re.IGNORECASE,
    )

    for line in lines[:200]:  # never read more than 200 lines
        stripped = line.strip()

        # Track brace depth to skip deep implementation
        brace_depth += stripped.count("{") - stripped.count("}")
        brace_depth = max(0, brace_depth)

        # Always keep shallow lines (depth 0–2 = class level + method sigs)
        if brace_depth <= 2 and (decl_re.search(line) or not stripped or stripped.startswith("//")):
            kept.append(line)
            if len(kept) >= _MAX_SIGNATURE_LINES:
                break

    return "\n".join(kept)


async def _ask_llm(file_blocks: list[str], endpoint: str, http_method: str) -> Optional[dict]:
    """Ask the fast LLM to identify which file/method handles the endpoint."""
    provider = get_provider()

    system = (
        "You are a code analyst. Given source file signatures, identify which file "
        "and method is the PRIMARY HTTP entry handler for the given endpoint. "
        "Return ONLY valid JSON — no explanation, no markdown."
    )

    user = f"""{http_method} {endpoint}

Candidate files:

{chr(10).join(file_blocks)}

Which file and method/function directly handles this HTTP endpoint?

Return JSON:
{{"file": "<relative file path>", "class": "<ClassName or empty string>", "method": "<methodName or empty string>", "confidence": <0.0-1.0>}}

If none of these files handles this endpoint, return {{"file": null, "confidence": 0}}."""

    try:
        raw = await provider.chat_json(
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            role=TaskRole.FAST,
            max_tokens=256,
        )
        return json.loads(raw)
    except Exception as e:
        log.warning("LLMHandlerFinder: LLM call failed", error=str(e))
        return None
