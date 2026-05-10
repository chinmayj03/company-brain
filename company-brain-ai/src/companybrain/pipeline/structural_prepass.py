"""
Stage 0.5: Structural pre-pass.

Calls cb-api /extract to populate Neo4j structural nodes, then queries
Neo4j (via cb-api /fingerprints) for the structural hash of every traced
file and computes the fresh-vs-dirty split for downstream LLM stages.

Failure mode: if cb-api is unreachable or /extract fails, every traced
unit is marked dirty (full LLM path) and a non-fatal warning logs.
"""
from __future__ import annotations
import hashlib
import os
import re
from pathlib import Path
import httpx
import structlog

from companybrain.collectors.code_tracer import CodeUnit, FocalContext
from companybrain.pipeline.types import StructuralFingerprint, PrePassResult

CB_API_URL = os.getenv("CB_API_URL", "http://cb-api:8090")
CB_API_TIMEOUT = 300.0  # seconds — large repos take time
log = structlog.get_logger(__name__)

# ADR-0049 C6: module-level cache keyed by (repo_path, commit_sha).
# Skips the cb-api round-trip and fingerprint fetch entirely when the repo
# SHA hasn't moved since the last call in this process lifetime.
_PREPASS_CACHE: dict[tuple[str, str], "PrePassResult"] = {}


async def run_structural_prepass(
    *,
    repo_path: str,
    commit_sha: str,
    workspace_id: str,
    focal_context: FocalContext,
) -> PrePassResult:
    """
    1. POST cb-api /extract  (runs Bun extractor-worker registry)
    2. GET  cb-api /fingerprints  (returns Neo4j-side structural hashes)
    3. For each CodeUnit: compare local structural hash to Neo4j's;
       fresh if equal, dirty otherwise.

    ADR-0049 C6: results are cached by (repo_path, commit_sha) so repeated
    calls within the same process (e.g., re-running the same endpoint) skip
    the cb-api round-trip entirely.
    """
    cache_key = (repo_path, commit_sha)
    if cache_key in _PREPASS_CACHE:
        log.info("structural_prepass.cache_hit", repo=repo_path, sha=commit_sha[:8])
        return _PREPASS_CACHE[cache_key]

    result = PrePassResult()

    # Step 1: trigger structural extraction
    try:
        async with httpx.AsyncClient(timeout=CB_API_TIMEOUT) as client:
            extract_resp = await client.post(
                f"{CB_API_URL}/extract",
                json={"repoPath": repo_path, "scope": workspace_id, "commitSha": commit_sha},
            )
            extract_resp.raise_for_status()
    except Exception as exc:
        log.warning("Structural pre-pass: cb-api /extract failed (non-fatal)", error=str(exc))
        result.cb_api_status = f"failed:{exc}"
        result.dirty_units = list(focal_context.code_units)
        return result

    # Step 2: fetch fingerprints
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            fp_resp = await client.get(
                f"{CB_API_URL}/fingerprints",
                params={"scope": workspace_id, "commit": commit_sha},
            )
            fp_resp.raise_for_status()
            fingerprints: dict[str, StructuralFingerprint] = {
                row["file_path"]: StructuralFingerprint(
                    file_path=row["file_path"],
                    structural_hash=row["structural_hash"],
                    function_count=row["function_count"],
                    class_count=row["class_count"],
                    last_indexed_commit=row.get("last_indexed_commit", ""),
                )
                for row in fp_resp.json()["fingerprints"]
            }
    except Exception as exc:
        log.warning("Structural pre-pass: /fingerprints failed (non-fatal)", error=str(exc))
        result.cb_api_status = f"failed:{exc}"
        result.dirty_units = list(focal_context.code_units)
        return result

    # Step 3: split fresh vs dirty
    for unit in focal_context.code_units:
        rel_path = _to_repo_relative(unit.file_path, repo_path)
        local_hash = _local_structural_hash(unit.content)
        neo4j_fp = fingerprints.get(rel_path)
        if neo4j_fp and neo4j_fp.structural_hash == local_hash:
            result.fresh_units.append(unit)
        else:
            result.dirty_units.append(unit)

    log.info(
        "Structural pre-pass complete",
        total=len(focal_context.code_units),
        fresh=len(result.fresh_units),
        dirty=len(result.dirty_units),
        cb_api=result.cb_api_status,
    )
    # ADR-0049 C6: store for future calls with the same repo+SHA.
    if commit_sha:
        _PREPASS_CACHE[cache_key] = result
    return result


def _local_structural_hash(content: str) -> str:
    """
    Quick local fingerprint for comparison against Neo4j's stored hash.
    Implementation: sorted top-level def/class/function/interface/method names
    → sha256. Stable across whitespace/comment changes; unstable across renames
    or signature changes. Replace with structural/parser.py in a follow-up ADR.
    """
    names = sorted(set(re.findall(
        r"^\s*(?:def|class|function|interface|public\s+\w+\s+)\s*(\w+)",
        content,
        re.MULTILINE,
    )))
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def _to_repo_relative(absolute_path: str, repo_path: str) -> str:
    try:
        return str(Path(absolute_path).relative_to(repo_path))
    except ValueError:
        return absolute_path  # already relative
