"""
ADR-0060 — v1 → v2 BusinessContext migration.

Two-mode upgrade for entries written under the old (21-field) schema:

  • scan         — list v1 entries on disk, no writes.
  • inplace      — bump schema_version on each v1 entry to 2 and default the
                   seven new typed fields. Idempotent. Does NOT call the LLM.
                   Cheap (~milliseconds per repo) and lossless w.r.t. existing
                   data, but the new fields stay null until a real re-synth.

For a full re-synth that actually populates the v2 fields, the operator runs
`brain enrich --workspace-id <ws>` afterwards. That path already exists and
uses `ContextSynthesizer.synthesise_all_v2` once enabled at the call-site.

Entry point:
    upgrade_business_context(brain_root, mode="scan"|"inplace") -> UpgradeReport
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class UpgradeReport:
    brain_root: Path
    mode: Literal["scan", "inplace"]
    scanned_files: int = 0
    v1_files: int = 0
    v2_files: int = 0
    migrated_files: int = 0
    skipped_files: list[str] = field(default_factory=list)  # parse errors etc.

    def summary(self) -> str:
        return (
            f"upgrade-business-context [{self.mode}] root={self.brain_root} "
            f"scanned={self.scanned_files} v1={self.v1_files} v2={self.v2_files} "
            f"migrated={self.migrated_files} skipped={len(self.skipped_files)}"
        )


# Default v2 field shape applied on in-place migration. Mirrors the dataclass
# defaults so the JSON-on-disk matches what BusinessContext(**data) would yield
# on a v2 read path.
_V2_FIELD_DEFAULTS: dict = {
    "schema_version": 2,
    "is_idempotent": None,
    "null_handling": {},
    "transaction_mode": None,
    "anti_patterns": [],
    "engineering_notes": [],
    "performance_class": None,
    "security_class": None,
}


def _payload_schema_version(payload: dict) -> int:
    """Read schema_version from a brain entity payload, defaulting to 1."""
    bc = payload.get("metadata", {}).get("business_context")
    if not isinstance(bc, dict):
        return 0  # no business_context block at all
    return int(bc.get("schema_version", 1))


def _migrate_payload_inplace(payload: dict) -> bool:
    """Mutate payload to add v2 default fields if it's v1. Return True if changed."""
    bc = payload.get("metadata", {}).get("business_context")
    if not isinstance(bc, dict):
        return False
    if int(bc.get("schema_version", 1)) >= 2:
        return False
    for k, v in _V2_FIELD_DEFAULTS.items():
        bc.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    bc["schema_version"] = 2
    return True


def upgrade_business_context(
    brain_root: Path,
    *,
    mode: Literal["scan", "inplace"] = "scan",
) -> UpgradeReport:
    """Walk `.brain/` and report on (optionally migrate) v1 BusinessContext entries.

    The walker inspects every JSON file under brain_root recursively — older
    .brain/ layouts put BusinessContext blobs inline under
    `metadata.business_context` on each entity file, which is what we operate
    on here. Files with no `business_context` key are simply skipped.
    """
    brain_root = Path(brain_root)
    report = UpgradeReport(brain_root=brain_root, mode=mode)
    if not brain_root.exists():
        return report

    for path in sorted(brain_root.rglob("*.json")):
        if path.name in {"index.json", "manifest.json"}:
            continue
        report.scanned_files += 1
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            report.skipped_files.append(str(path))
            continue
        if not isinstance(payload, dict):
            continue
        version = _payload_schema_version(payload)
        if version == 0:
            continue  # no BC block on this entity
        if version >= 2:
            report.v2_files += 1
            continue
        report.v1_files += 1
        if mode == "inplace" and _migrate_payload_inplace(payload):
            path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            report.migrated_files += 1
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI shim. `python -m companybrain.cli_helpers.upgrade_business_context ...`"""
    import argparse

    parser = argparse.ArgumentParser(description="Upgrade .brain/ BusinessContext from v1 to v2")
    parser.add_argument("brain_root", type=Path, help="Path to the .brain/ directory")
    parser.add_argument(
        "--mode",
        choices=("scan", "inplace"),
        default="scan",
        help="scan = dry-run report; inplace = mutate JSON files in-place",
    )
    args = parser.parse_args(argv)
    report = upgrade_business_context(args.brain_root, mode=args.mode)
    print(report.summary())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
