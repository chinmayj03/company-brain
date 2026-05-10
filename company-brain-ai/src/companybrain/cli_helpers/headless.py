"""Headless / JSON-output runner for `brain index` (ADR-0052 P5).

Wraps the existing index command to produce a single structured JSON payload
suitable for ``brain extract --headless --json | jq .``. Returns
``(payload, exit_code)``:

  * ``exit_code == 0`` → success; brain extraction completed.
  * ``exit_code == 1`` → extraction error (one or more endpoints failed).
  * ``exit_code == 2`` → drift detected (placeholder for P6 — flagged in
                         ``payload['drift']`` once available).

The payload always contains ``telemetry`` so consumers can pipe-parse without
having to special-case missing keys.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from companybrain.cli_helpers.endpoint_discovery import discover_endpoints
from companybrain.cli_helpers.repo_walker import walk_repo
from companybrain.collectors.code_tracer import CodeUnit, FocalContext
from companybrain.models.entities import (
    PipelineStartRequest,
    RepoConfig,
    RepoType,
)
from companybrain.pipeline.orchestrator import run_pipeline
from companybrain.pipeline.structural_prepass import run_structural_prepass

log = structlog.get_logger(__name__)


async def run_index_headless(
    *,
    repo_path: Path,
    branch: str,
    workspace_id: str,
    endpoints: str | None,
    repo_name: str,
    dry_run: bool,
) -> tuple[dict[str, Any], int]:
    """Run the index workflow and return ``(json_payload, exit_code)``."""
    started = time.monotonic()

    try:
        fc = FocalContext(
            endpoint="*",
            method="REPO",
            code_units=[
                CodeUnit(file_path=str(p), repo_name=repo_name, role="unknown",
                         class_name=p.stem, content=p.read_text(errors="replace"),
                         language=_lang_for(p))
                for p in walk_repo(repo_path)
            ],
        )
        prepass = await run_structural_prepass(
            repo_path=str(repo_path),
            commit_sha=_git_head(repo_path),
            workspace_id=workspace_id,
            focal_context=fc,
        )
    except Exception as exc:  # noqa: BLE001 — surface as JSON
        return _failure_payload(
            phase="structural_prepass",
            error=f"{type(exc).__name__}: {exc}",
            started=started,
        )

    if endpoints:
        endpoint_list = [tuple(s.strip().split(maxsplit=1)) for s in endpoints.split(",")]
    else:
        endpoint_list = discover_endpoints(repo_path)

    summary_lines = [
        f"prepass_fresh={len(prepass.fresh_units)}",
        f"prepass_dirty={len(prepass.dirty_units)}",
        f"endpoints_to_extract={len(endpoint_list)}",
    ]

    if dry_run:
        return {
            "ok":          True,
            "dry_run":     True,
            "repo_path":   str(repo_path),
            "workspace_id": workspace_id,
            "summary":     " ".join(summary_lines),
            "telemetry": {
                "prepass_fresh": len(prepass.fresh_units),
                "prepass_dirty": len(prepass.dirty_units),
                "endpoints":     [
                    {"method": m, "path": p} for m, p in endpoint_list
                ],
                "wall_time_seconds": round(time.monotonic() - started, 3),
            },
        }, 0

    extracted: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total_cost_usd = 0.0
    for method, path in endpoint_list:
        request = PipelineStartRequest(
            endpoint_path=path,
            http_method=method,
            branch=branch,
            workspace_id=workspace_id,
            repos=[RepoConfig(local_path=str(repo_path), type=RepoType.backend,
                              branch=branch, name=repo_name)],
        )
        try:
            result = await run_pipeline(request)
            telem = getattr(result, "telemetry", {}) or {}
            cost = float(telem.get("cost", {}).get("total_cost_usd", 0.0))
            total_cost_usd += cost
            extracted.append({
                "method":     method,
                "path":       path,
                "status":     getattr(result, "status", "unknown"),
                "telemetry":  telem,
                "cost_usd":   round(cost, 6),
            })
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "method": method, "path": path,
                "error":  f"{type(exc).__name__}: {exc}",
            })

    summary_lines.append(f"failures={len(failures)}")
    summary_lines.append(f"total_cost_usd=${total_cost_usd:.4f}")
    payload: dict[str, Any] = {
        "ok":           not failures,
        "dry_run":      False,
        "repo_path":    str(repo_path),
        "workspace_id": workspace_id,
        "summary":      " ".join(summary_lines),
        "telemetry": {
            "prepass_fresh":     len(prepass.fresh_units),
            "prepass_dirty":     len(prepass.dirty_units),
            "endpoints_total":   len(endpoint_list),
            "endpoints_ok":      len(extracted) - len(failures),
            "endpoints_failed":  len(failures),
            "extracted":         extracted,
            "failures":          failures,
            "total_cost_usd":    round(total_cost_usd, 6),
            "wall_time_seconds": round(time.monotonic() - started, 3),
        },
    }
    return payload, (1 if failures else 0)


# ── helpers ────────────────────────────────────────────────────────────────


def _failure_payload(*, phase: str, error: str, started: float) -> tuple[dict[str, Any], int]:
    return {
        "ok":     False,
        "phase":  phase,
        "error":  error,
        "telemetry": {
            "wall_time_seconds": round(time.monotonic() - started, 3),
        },
    }, 1


def _lang_for(p: Path) -> str:
    return {
        ".java": "java", ".kt": "kotlin", ".py": "python",
        ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".go": "go", ".rb": "ruby", ".cs": "csharp",
    }.get(p.suffix, "unknown")


def _git_head(repo: Path) -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:  # noqa: BLE001
        return "HEAD"


__all__ = ["run_index_headless"]
