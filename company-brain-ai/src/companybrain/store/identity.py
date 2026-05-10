"""
URN identity for brain entities — ADR-0013: Canonical URN identity.

Format: urn:cb:{tenant}:{domain}:{repo}:{entity_type}:{qualified_name}

Round-trip-safe encoding for qualified_names that contain special characters
(e.g. HTTP paths like '/users/{id}') uses percent-encoding for ':' '/' '%'
and a recognisable plus-prefix for HTTP method+path entities.

Legacy shims:
  - from_legacy_postgres(): translate Postgres external_id to URN
  - from_legacy_neo4j(): translate old 'urn:cb:llm:...' to canonical URN
  - make_entity_id() / parse_entity_id() / to_external_id(): kept for callers
    that have not yet migrated; they delegate to the URN layer internally.

ADR-0043 (WS1.S3): the `monorepo` default was removed from every URN
constructor. Callers that cannot supply a real repo name will now receive
RepoUnknownForUrn instead of silently producing urn:…:monorepo:… URNs that
could never match a real node.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote, unquote


class RepoUnknownForUrn(ValueError):
    """Raised when a URN constructor is called without a concrete repo name.

    Callers must supply the real repo (e.g. 'network-iq-backend-java').
    Passing an empty string or omitting the argument is no longer allowed —
    it would silently produce urn:…:monorepo:… URNs that orphan every edge.
    """

URN_SCHEME = "urn:cb"
URN_SEPARATOR = ":"
DEFAULT_TENANT = "dev"
DEFAULT_DOMAIN = "code"
ALLOWED_ENTITY_TYPES = frozenset({
    "component",
    "screen",
    "api_contract",
    "data_model",
    "assumption",
    "business_context",
    "function_node",
})


# ── Canonical URN data class ──────────────────────────────────────────────────

@dataclass(frozen=True)
class URNParts:
    tenant: str
    domain: str
    repo: str
    entity_type: str
    qualified_name: str

    def to_urn(self) -> str:
        return URN_SEPARATOR.join([
            URN_SCHEME,
            self.tenant,
            self.domain,
            self.repo,
            self.entity_type,
            _encode(self.qualified_name),
        ])


# ── Primary API ───────────────────────────────────────────────────────────────

def to_urn(*, tenant: str, domain: str, repo: str,
           entity_type: str, qualified_name: str) -> str:
    """
    Build the canonical URN for a brain entity.

    Raises ValueError for unknown entity_type values.
    """
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(
            f"Unknown entity_type {entity_type!r}. "
            f"Allowed: {sorted(ALLOWED_ENTITY_TYPES)}"
        )
    return URNParts(
        tenant=tenant,
        domain=domain,
        repo=repo,
        entity_type=entity_type,
        qualified_name=qualified_name,
    ).to_urn()


def parse_urn(urn: str) -> URNParts:
    """
    Parse a canonical CB URN into its components.

    The qualified_name segment may itself contain encoded colons, so we
    split on ':' at most 6 times (yielding 7 segments) and treat everything
    from index 6 onward as the (encoded) qualified_name.
    """
    prefix = f"{URN_SCHEME}{URN_SEPARATOR}"
    if not urn.startswith(prefix):
        raise ValueError(f"Not a CB URN: {urn!r}")
    # Split into at most 7 parts: ['urn', 'cb', tenant, domain, repo, entity_type, qname_encoded]
    parts = urn.split(URN_SEPARATOR, 6)
    if len(parts) < 7:
        raise ValueError(f"Malformed URN (too few segments): {urn!r}")
    _, _, tenant, domain, repo, entity_type, qname_encoded = parts[:7]
    return URNParts(
        tenant=tenant,
        domain=domain,
        repo=repo,
        entity_type=entity_type,
        qualified_name=_decode(qname_encoded),
    )


# ── Migration helpers (legacy → URN) ─────────────────────────────────────────

def from_legacy_postgres(
    *,
    workspace_slug: str,
    node_type: str,
    legacy_external_id: str,
    repo: str,
) -> str:
    """
    Translate a Postgres external_id like 'backend/src/payment.ts::chargePayment'
    into a canonical URN.  node_type is mapped via NODE_TYPE_TAXONOMY.

    ``repo`` is now required (no default). Pass the real repository name so
    that the resulting URN matches the nodes stored for that repo.
    Raises RepoUnknownForUrn when repo is empty.
    """
    if not repo:
        raise RepoUnknownForUrn(
            f"from_legacy_postgres called without a repo for external_id {legacy_external_id!r}. "
            "Pass the real repo name — 'monorepo' is no longer accepted."
        )
    entity_type = NODE_TYPE_TAXONOMY.get(node_type, "component")
    # Take the last segment after '::' as the qualified name; fall back to the whole string.
    qname = legacy_external_id.split("::")[-1] if "::" in legacy_external_id else legacy_external_id
    return to_urn(
        tenant=workspace_slug,
        domain=DEFAULT_DOMAIN,
        repo=repo,
        entity_type=entity_type,
        qualified_name=qname,
    )


def from_legacy_neo4j(legacy_urn: str, *, repo: str) -> str:
    """
    Translate 'urn:cb:llm:{workspace}:{file_path}:{entity_name}'
    into the canonical URN format.

    ``repo`` is now required (no default). Raises RepoUnknownForUrn when empty.
    entity_type defaults to 'component'; refined later by the migration script.
    """
    if not repo:
        raise RepoUnknownForUrn(
            f"from_legacy_neo4j called without a repo for urn {legacy_urn!r}. "
            "Pass the real repo name — 'monorepo' is no longer accepted."
        )
    parts = legacy_urn.split(URN_SEPARATOR)
    if len(parts) < 6 or parts[:3] != ["urn", "cb", "llm"]:
        raise ValueError(f"Not a legacy Neo4j URN: {legacy_urn!r}")
    # parts: ['urn', 'cb', 'llm', workspace, file_path, entity_name, ...]
    workspace = parts[3]
    qname = parts[-1]   # entity_name is the last segment
    return to_urn(
        tenant=workspace,
        domain=DEFAULT_DOMAIN,
        repo=repo,
        entity_type="component",
        qualified_name=qname,
    )


# ── Workspace slug resolver ───────────────────────────────────────────────────

def workspace_slug_for(workspace_id: str) -> str:  # noqa: ARG001
    """
    Resolve workspace UUID → slug.

    In Stage 1 the slug is hardcoded via BRAIN_WORKSPACE_SLUG (default 'dev').
    ADR-0016 will wire a workspace registry so the slug is looked up by UUID.
    """
    return os.getenv("BRAIN_WORKSPACE_SLUG", DEFAULT_TENANT)


# ── node_type → entity_type taxonomy ─────────────────────────────────────────

NODE_TYPE_TAXONOMY: dict[str, str] = {
    # Existing free-form node_type values → 6-type harness taxonomy
    "ApiEndpoint":       "api_contract",
    "Function":          "component",
    "CodeFunction":      "component",
    "Class":             "component",
    "Service":           "component",
    "FrontendComponent": "component",
    "SchemaField":       "data_model",
    "DatabaseTable":     "data_model",
    "DatabaseColumn":    "data_model",
    "DatabaseQuery":     "data_model",
    "ExternalService":   "component",
    "ConfigKey":         "component",
    "SharedType":        "data_model",
}


# ── Encoding helpers ──────────────────────────────────────────────────────────

def _encode(qname: str) -> str:
    """
    Percent-encode characters that would break URN segment parsing.
    ':' must be encoded so parse_urn's split logic works correctly.
    '/' may appear in HTTP paths and is encoded for safety.
    """
    return quote(qname, safe="._-{}")


def _decode(encoded: str) -> str:
    return unquote(encoded)


# ── Legacy shims (callers that have not migrated yet) ─────────────────────────
# These keep the old make_entity_id / parse_entity_id / to_external_id API
# intact so existing code continues to work while ADR-0013 rolls out.

_LEGACY_SEP = "::"


def make_entity_id(repo: str, entity_type: str, qualified_name: str) -> str:
    """
    Legacy shim: build `{repo}::{entity_type}::{qualified_name}`.

    New code should call to_urn() directly.
    Kept for backward compatibility — callers import from this module.
    """
    return f"{repo}{_LEGACY_SEP}{entity_type}{_LEGACY_SEP}{qualified_name}"


def parse_entity_id(entity_id: str) -> tuple[str, str, str]:
    """
    Legacy shim: split `{repo}::{entity_type}::{qualified_name}`.
    Returns (repo, entity_type, qualified_name).
    """
    parts = entity_id.split(_LEGACY_SEP, 2)
    if len(parts) != 3:
        raise ValueError(
            f"Invalid entity_id {entity_id!r}. Expected format: repo::type::qname"
        )
    return parts[0], parts[1], parts[2]


def to_external_id(entity_id: str) -> str:
    """
    Legacy shim: return the Postgres-safe external_id from a canonical entity_id.
    Today these are identical; normalisation happens at the URN layer.
    """
    return entity_id
