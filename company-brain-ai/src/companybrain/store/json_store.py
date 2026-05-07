"""
.brain/ source-of-truth implementation.

File layout (per repo):
  .brain/
  ├── index.json                          ← entity_id → relative path
  ├── manifest.json                       ← run history, last_run_id, last_commit
  ├── component/<qname>.json
  ├── api_contract/<sanitised_qname>.json
  ├── data_model/<qname>.json
  ├── assumption/<qname>.json
  ├── business_context/<qname>.json
  ├── function_node/<qname>.json
  └── .l2-cache/<branch>.json             ← reserved for ADR-0014
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import structlog

from companybrain.store.base import BrainStore, BrainEntity
from companybrain.store.identity import parse_urn

log = structlog.get_logger(__name__)

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _qname_to_filename(qname: str) -> str:
    """Sanitise a qualified name into a safe filename. Preserves enough for humans."""
    s = _SLUG.sub("_", qname)
    return s[:200]  # filesystem-safe length


class JsonFileBrainStore(BrainStore):
    """Writes one JSON file per entity under .brain/{type}/{qname}.json."""

    def __init__(self, brain_root: Path):
        self.root = Path(brain_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._manifest_path = self.root / "manifest.json"
        self._lock = asyncio.Lock()

    # ── BrainStore implementation ────────────────────────────────────────────

    async def write(self, entity: BrainEntity, *, run_id: str, workspace_id: str) -> None:
        async with self._lock:
            if entity.id.startswith("urn:cb:"):
                entity_file = self._entity_path_from_id(entity.id)
            else:
                entity_file = self._entity_path(entity.entity_type, entity.qualified_name)
            entity_file.parent.mkdir(parents=True, exist_ok=True)
            entity_file.write_text(json.dumps(entity.to_dict(), indent=2, sort_keys=True))
            self._update_index(entity.id, entity_file.relative_to(self.root))
            log.debug("brain.json.write", entity_id=entity.id, path=str(entity_file))

    async def read(self, entity_id: str) -> Optional[BrainEntity]:
        idx = self._load_index()
        rel = idx.get(entity_id)
        if not rel:
            return None
        path = self.root / rel
        if not path.exists():
            return None
        return BrainEntity.from_dict(json.loads(path.read_text()))

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        existing = await self.read(entity_id)
        return existing is not None and existing.version_hash == version_hash

    async def list_ids(self) -> AsyncIterator[str]:
        for entity_id in self._load_index().keys():
            yield entity_id

    async def commit_run(self, run_id: str) -> None:
        manifest = self._load_manifest()
        manifest["last_run_id"] = run_id
        manifest["last_commit_at"] = datetime.utcnow().isoformat() + "Z"
        manifest.setdefault("runs", []).append({
            "run_id": run_id, "at": manifest["last_commit_at"],
        })
        self._manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        log.info("brain.json.commit_run", run_id=run_id, root=str(self.root))

    # ── Internals ────────────────────────────────────────────────────────────

    def _entity_path(self, entity_type: str, qname: str) -> Path:
        return self.root / entity_type / f"{_qname_to_filename(qname)}.json"

    def _entity_path_from_id(self, entity_id: str) -> Path:
        """
        Derive the filesystem path from an entity id.

        If the id is a canonical URN (urn:cb:...), parse it to extract
        entity_type and qualified_name so the file lands under the correct
        type subdirectory.  Falls back to the legacy entity_type / qname
        convention from BrainEntity fields.
        """
        if entity_id.startswith("urn:cb:"):
            try:
                parts = parse_urn(entity_id)
                return self._entity_path(parts.entity_type, parts.qualified_name)
            except ValueError:
                pass
        # Legacy fallback: id is repo::type::qname
        segments = entity_id.split("::", 2)
        if len(segments) == 3:
            return self._entity_path(segments[1], segments[2])
        return self._entity_path("component", entity_id)

    def _load_index(self) -> dict:
        if not self._index_path.exists():
            return {}
        return json.loads(self._index_path.read_text())

    def _update_index(self, entity_id: str, rel_path: Path) -> None:
        idx = self._load_index()
        idx[entity_id] = str(rel_path)
        self._index_path.write_text(json.dumps(idx, indent=2, sort_keys=True))

    def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}
        return json.loads(self._manifest_path.read_text())
