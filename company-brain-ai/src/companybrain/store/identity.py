"""
Identity helpers — placeholder for ADR-0013 (Canonical URN identity).

Today, entity IDs follow the format: `{repo}::{entity_type}::{qualified_name}`.
ADR-0013 will promote this to a full URN scheme. This module is a stable
shim so callers import from here and ADR-0013 only needs to touch this file.
"""
from __future__ import annotations

import re

_SEP = "::"

# Characters that are safe in every filesystem and URL context.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._/-]")


def make_entity_id(repo: str, entity_type: str, qualified_name: str) -> str:
    """
    Build the canonical entity ID used as the JSON store key and Postgres external_id.

    Format: `{repo}::{entity_type}::{qualified_name}`

    ADR-0013 will replace this with a full URN; callers must import from here.
    """
    return f"{repo}{_SEP}{entity_type}{_SEP}{qualified_name}"


def parse_entity_id(entity_id: str) -> tuple[str, str, str]:
    """
    Split `{repo}::{entity_type}::{qualified_name}` into its three parts.
    Returns (repo, entity_type, qualified_name).
    Raises ValueError if the format is invalid.
    """
    parts = entity_id.split(_SEP, 2)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid entity_id {entity_id!r}. Expected format: repo::type::qname"
        )
    return parts[0], parts[1], parts[2]


def to_external_id(entity_id: str) -> str:
    """
    Return the Postgres-safe external_id from a canonical entity_id.
    Today these are identical; ADR-0013 may add a normalisation step.
    """
    return entity_id
