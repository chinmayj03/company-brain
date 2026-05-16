"""Read-only brain browser routes for the local ADR-0071 demo UI.

These endpoints project the existing .brain JSON source of truth into a small
HTTP shape the frontend can render. They do not write or mutate brain data.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _demo_repo_root() -> Path:
    configured = (
        os.environ.get("BRAIN_DEMO_REPO_PATH")
        or os.environ.get("TARGET_REPO")
        or os.environ.get("BRAIN_REPO_PATH")
        or "/Users/chinmayjadhav/Documents/network-iq-backend-java"
    )
    return Path(configured).expanduser()


def _brain_root(repo_root: Path | None = None) -> Path:
    root = repo_root or _demo_repo_root()
    return root / ".brain"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _index(brain_root: Path) -> dict[str, str]:
    index_path = brain_root / "index.json"
    if not index_path.exists():
        return {}
    return _load_json(index_path)


def _entity_files(brain_root: Path) -> list[Path]:
    paths: list[Path] = []
    for child in brain_root.iterdir() if brain_root.exists() else []:
        if child.name.startswith(".") or not child.is_dir():
            continue
        paths.extend(sorted(child.glob("*.json")))
    return paths


def _read_entities(brain_root: Path) -> list[dict[str, Any]]:
    entities = []
    for path in _entity_files(brain_root):
        data = _load_json(path)
        if data.get("id"):
            data["_brain_path"] = str(path)
            entities.append(data)
    return entities


def _summary_for(entity: dict[str, Any]) -> str:
    return (
        entity.get("t1_summary")
        or entity.get("t1_token")
        or entity.get("t0_token")
        or entity.get("metadata", {}).get("purpose")
        or ""
    )


def _citation_for(entity: dict[str, Any]) -> dict[str, Any]:
    metadata = entity.get("metadata") or {}
    return {
        "urn": entity.get("id", ""),
        "name": entity.get("qualified_name") or entity.get("id", ""),
        "file": entity.get("file") or metadata.get("file") or "",
        "line": metadata.get("line") or metadata.get("start_line"),
        "why_relevant": _summary_for(entity)[:280],
        "confidence": metadata.get("confidence", 0.9),
    }


def _project_entity(entity: dict[str, Any]) -> dict[str, Any]:
    metadata = entity.get("metadata") or {}
    relationships = entity.get("relationships") or []
    return {
        "urn": entity.get("id", ""),
        "name": entity.get("qualified_name") or entity.get("id", ""),
        "type": entity.get("entity_type", "unknown"),
        "repo_id": entity.get("repo", ""),
        "file": entity.get("file", ""),
        "summary": _summary_for(entity),
        "role": metadata.get("signature") or metadata.get("role") or entity.get("entity_type", ""),
        "risk": metadata.get("change_risk") or metadata.get("risk") or "UNKNOWN",
        "last_updated": entity.get("last_updated", ""),
        "metadata": metadata,
        "edges": relationships,
        "citations": [_citation_for(entity)],
    }


@router.get("/repos")
async def list_repos() -> dict[str, Any]:
    repo_root = _demo_repo_root()
    brain_root = _brain_root(repo_root)
    entities = _read_entities(brain_root)
    counts = Counter(e.get("entity_type", "unknown") for e in entities)
    manifest = _load_json(brain_root / "manifest.json")
    repo_id = repo_root.name
    return {
        "repos": [
            {
                "id": repo_id,
                "name": repo_id,
                "path": str(repo_root),
                "status": "extracted" if entities else "empty",
                "entity_count": len(entities),
                "edge_count": sum(len(e.get("relationships") or []) for e in entities),
                "entity_types": dict(counts),
                "last_extracted": manifest.get("last_commit_at") or manifest.get("last_updated") or None,
            }
        ]
    }


@router.get("/repos/{repo_id}/brain/summary")
async def repo_brain_summary(repo_id: str) -> dict[str, Any]:
    repos = await list_repos()
    repo = next((r for r in repos["repos"] if r["id"] == repo_id), None)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")
    return repo


@router.get("/entities")
async def list_entities(
    q: str | None = Query(default=None),
    type: str | None = Query(default=None),
    repo_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    entities = [_project_entity(e) for e in _read_entities(_brain_root())]
    if repo_id:
        entities = [e for e in entities if e["repo_id"] == repo_id]
    if type and type != "all":
        entities = [e for e in entities if e["type"] == type]
    if q:
        needle = q.lower()
        entities = [
            e for e in entities
            if needle in e["name"].lower()
            or needle in e["summary"].lower()
            or needle in e["file"].lower()
        ]
    entities.sort(key=lambda e: (e["type"], e["name"]))
    start = (page - 1) * page_size
    return {
        "items": entities[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(entities),
        "types": sorted({e["type"] for e in entities}),
    }


@router.get("/entities/{urn:path}")
async def entity_detail(urn: str) -> dict[str, Any]:
    decoded = unquote(urn)
    brain_root = _brain_root()
    idx = _index(brain_root)
    rel_path = idx.get(decoded)
    entity: dict[str, Any] | None = None
    if rel_path:
        path = brain_root / rel_path
        if path.exists():
            entity = _load_json(path)
    if entity is None:
        entity = next((e for e in _read_entities(brain_root) if e.get("id") == decoded), None)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")

    projected = _project_entity(entity)
    edges = projected["edges"]
    related: list[dict[str, Any]] = []
    if edges:
        targets = {
            edge.get("target_id") or edge.get("to_entity") or edge.get("to")
            for edge in edges
        }
        for candidate in _read_entities(brain_root):
            if candidate.get("id") in targets:
                related.append(_project_entity(candidate))
    projected["related_entities"] = related[:20]
    return projected


@router.get("/drift/snapshot/latest")
async def latest_drift_snapshot() -> dict[str, Any]:
    entities = [_project_entity(e) for e in _read_entities(_brain_root())]
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        if entity["risk"] in {"HIGH", "MEDIUM"}:
            domain = (entity["type"] or "unknown").replace("_", " ")
            by_domain[domain].append(entity)

    domains = []
    items = []
    for domain, grouped in sorted(by_domain.items()):
        high_count = sum(1 for e in grouped if e["risk"] == "HIGH")
        severity = "high" if high_count else "medium"
        domains.append({"domain": domain, "severity": severity, "count": len(grouped)})
        for entity in grouped[:5]:
            items.append({
                "id": f"mock-drift::{entity['urn']}",
                "domain": domain,
                "severity": severity,
                "title": f"{entity['name']} has elevated change risk",
                "state": "open",
                "entity_urn": entity["urn"],
                "history": [
                    {"at": entity["last_updated"], "event": "Risk surfaced from .brain metadata"}
                ],
            })

    return {
        "mock": True,
        "as_of": None,
        "domains": domains[:8],
        "items": items[:25],
    }
