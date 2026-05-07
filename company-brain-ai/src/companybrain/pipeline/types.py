from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StructuralFingerprint:
    """One file's structural shape, as known to Neo4j.

    structural_hash is hash(sorted(qualified_names)) — a fingerprint that is
    stable across formatting changes but unstable across renames or signature
    changes.
    """
    file_path: str          # relative to repo root
    structural_hash: str    # sha256 of sorted (kind, qname, signature) tuples
    function_count: int
    class_count: int
    last_indexed_commit: str


@dataclass
class PrePassResult:
    """What the structural pre-pass found.

    fresh_units    — code units whose structural hash matches Neo4j → skip Stage 1.
    dirty_units    — code units that need LLM extraction in Stage 1.
    cb_api_status  — "ok" | "skipped" | "failed:<reason>"
    """
    fresh_units: list = field(default_factory=list)
    dirty_units: list = field(default_factory=list)
    cb_api_status: str = "ok"
