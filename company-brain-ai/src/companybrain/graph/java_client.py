"""
JavaGraphClient — replaces GraphBuilder's direct Postgres writes.

Instead of writing entities/edges/contexts to Postgres from Python,
we POST the structured extraction results to the Java backend's internal
endpoint: POST /v1/internal/pipeline-result

This enforces a clean service boundary:
  - AI service owns: LLM inference, code tracing, git collection
  - Java backend owns: ALL graph persistence, job lifecycle, workspace isolation

Auth: X-Internal-Key header (shared secret between the two services).

IMPORTANT: all payload keys are camelCase to match Java's Jackson defaults.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

from companybrain.models.entities import (
    Artifact,
    ExtractedEntity,
    ExtractedRelationship,
    BusinessContext,
)
from companybrain.store.identity import (
    to_urn,
    workspace_slug_for,
    NODE_TYPE_TAXONOMY,
    DEFAULT_DOMAIN,
)

log = structlog.get_logger(__name__)

BACKEND_URL   = os.environ.get("BACKEND_URL", "http://localhost:8080")
INTERNAL_KEY  = os.environ.get("AI_INTERNAL_KEY", "dev-internal-key")
_TIMEOUT      = 30.0  # seconds


# ── Freshness check result types ──────────────────────────────────────────────

@dataclass
class ExistingEntity:
    """Minimal node projection returned by the freshness endpoint for a fresh artifact."""
    node_type: str
    name: str
    external_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class ArtifactFreshnessResult:
    """
    Result for one code unit after the freshness pre-flight check.

    fresh=True  → content hash unchanged, graph nodes exist.
                  AI service skips LLM extraction; existing_entities is populated
                  so relationship extraction / synthesis can reference these nodes.
    fresh=False → LLM extraction is required (new file, changed file, no nodes yet).
    """
    external_id: str
    fresh: bool
    existing_entities: list[ExistingEntity] = field(default_factory=list)


def sha256_content(content: str | None) -> str:
    """SHA-256 of the content, matching Java's ArtifactWriterService.sha256()."""
    data = (content or "").strip().encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _base_url_from(callback_url: Optional[str]) -> str:
    """
    Extract the scheme+host from a callback URL so we can derive sibling endpoints.
    e.g. 'http://localhost:8080/v1/internal/pipeline-result' → 'http://localhost:8080'
    Falls back to BACKEND_URL env var.
    """
    if callback_url:
        parsed = urlparse(callback_url)
        return f"{parsed.scheme}://{parsed.netloc}"
    return BACKEND_URL


class JavaGraphClient:
    """
    Posts pipeline results to the Java backend.
    The Java backend's PipelineService applies them to the graph DB.
    """

    def __init__(
        self,
        workspace_id: str,
        job_id: str,
        callback_url: Optional[str] = None,
        callback_key: Optional[str] = None,
    ):
        self.workspace_id = workspace_id
        self.job_id = job_id
        self._key = callback_key or INTERNAL_KEY

        base = _base_url_from(callback_url)
        # Use the explicit callback_url if provided; otherwise derive from base
        self._result_url   = callback_url or f"{base}/v1/internal/pipeline-result"
        self._progress_url = f"{base}/v1/internal/pipeline-progress"

    async def check_freshness(
        self,
        code_units: list,   # list[CodeUnit] — avoid circular import, typed as list
    ) -> dict[str, ArtifactFreshnessResult]:
        """
        Pre-flight freshness check for a set of code units BEFORE any LLM calls.

        Hashes each unit's content and asks Java which are fresh (hash unchanged,
        graph nodes already present).  Returns a dict keyed by the unit's
        canonical artifact external_id ("repo/file_path").

        Usage in orchestrator:
            freshness = await graph_client.check_freshness(focal_context.code_units)
            fresh_units = [u for u in units if freshness[key(u)].fresh]
            dirty_units = [u for u in units if not freshness[key(u)].fresh]

        Non-fatal: if the Java backend is unreachable (first run, dev mode, etc.)
        we return all units as dirty so the pipeline continues normally.
        """
        if not code_units:
            return {}

        # Build request payload — one item per unique code unit
        checks = []
        unit_key_map: dict[str, object] = {}   # externalId → CodeUnit
        for unit in code_units:
            ext_id = f"{unit.repo_name}/{unit.file_path}"
            content_hash = sha256_content(unit.content)
            checks.append({
                "kind":        "source_file",
                "externalId":  ext_id,
                "contentHash": content_hash,
            })
            unit_key_map[ext_id] = unit

        payload = {
            "workspaceId": self.workspace_id,
            "artifacts":   checks,
        }

        try:
            freshness_url = self._result_url.replace(
                "/v1/internal/pipeline-result",
                "/v1/internal/artifacts/check-freshness",
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    freshness_url,
                    json=payload,
                    headers={"X-Internal-Key": self._key},
                )
                resp.raise_for_status()
                data = resp.json()

        except Exception as exc:
            log.warning(
                "Freshness check failed — treating all units as dirty (non-fatal)",
                error=str(exc),
                units=len(code_units),
            )
            # Fail open: return all dirty so pipeline runs normally
            return {
                f"{u.repo_name}/{u.file_path}": ArtifactFreshnessResult(
                    external_id=f"{u.repo_name}/{u.file_path}",
                    fresh=False,
                )
                for u in code_units
            }

        # Parse response
        results: dict[str, ArtifactFreshnessResult] = {}
        for item in data.get("results", []):
            ext_id = item["externalId"]
            fresh  = item.get("fresh", False)
            existing_entities = [
                ExistingEntity(
                    node_type=e["nodeType"],
                    name=e["name"],
                    external_id=e["externalId"],
                    metadata=e.get("metadata", {}),
                )
                for e in item.get("existingEntities", [])
            ]
            results[ext_id] = ArtifactFreshnessResult(
                external_id=ext_id,
                fresh=fresh,
                existing_entities=existing_entities,
            )

        # Fill in any units the backend didn't return (treat as dirty)
        for unit in code_units:
            ext_id = f"{unit.repo_name}/{unit.file_path}"
            if ext_id not in results:
                results[ext_id] = ArtifactFreshnessResult(external_id=ext_id, fresh=False)

        fresh_count = sum(1 for r in results.values() if r.fresh)
        log.info(
            "Freshness check complete",
            total=len(results),
            fresh=fresh_count,
            dirty=len(results) - fresh_count,
        )
        return results

    async def flush(
        self,
        entities: list[ExtractedEntity],
        relationships: list[ExtractedRelationship],
        contexts: dict[str, BusinessContext],
        pipeline_meta: dict | None = None,
        status: str = "completed",
        error_message: str | None = None,
        artifacts: list[Artifact] | None = None,
        intent_contexts: dict | None = None,
    ) -> None:
        """
        Post all pipeline results to Java in one call.
        Called by the orchestrator at Stage 5 (graph population).

        All keys are camelCase to match Java's Jackson ObjectMapper defaults.
        """
        meta = pipeline_meta or {}
        artifacts = artifacts or []

        # Build artifact provenance map: entity external_id → [artifact external_ids]
        # Every entity is linked to every source_file artifact whose path matches
        # its file field.  This gives coarse-grained but always-present provenance.
        artifact_links: dict[str, list[str]] = {}
        source_file_artifacts = [a for a in artifacts if a.kind == "source_file"]
        for entity in entities:
            entity_file = entity.file  # e.g. "repo/src/main/java/Foo.java"
            matching = [
                a.external_id for a in source_file_artifacts
                if entity_file and a.external_id.endswith(entity_file)
            ]
            if matching:
                artifact_links[entity.external_id] = matching

        payload = {
            # ── Job identity (camelCase — matches PipelineResultRequest Java DTO) ──
            "jobId":         self.job_id,
            "workspaceId":   self.workspace_id,
            "status":        status,
            "errorMessage":  error_message,

            # ── Pipeline diagnostics ──
            "codeUnitsFound":  meta.get("code_units_found", 0),
            "gitCommitsFound": meta.get("git_commits_found", 0),
            "filesTraced":     meta.get("files_traced", []),
            "stagesSummary":   meta.get("stages_summary", []),
            "progressLogs":    meta.get("progress_logs", []),

            # ── Extraction results ──
            "entities":      [_entity_to_dict(e, self.workspace_id)   for e in entities],
            "relationships": [_rel_to_dict(r)       for r in relationships],
            "contexts":      [_ctx_to_dict(eid, c)  for eid, c in contexts.items()],

            # ── ADR-003: Intent contexts (FunctionContext per entity) ──
            # Maps entity external_id → FunctionContext dict from IntentSynthesizer.
            # Java merges these into node JSONB metadata under "functionContext".
            "intentContexts": intent_contexts or meta.get("intent_contexts", {}),

            # ── ADR-005: Artifact provenance ──
            "artifacts":     [_artifact_to_dict(a) for a in artifacts],
            "artifactLinks": artifact_links,
        }

        log.info(
            "Posting pipeline result to Java backend",
            url=self._result_url,
            job_id=self.job_id,
            status=status,
            entities=len(entities),
            relationships=len(relationships),
            contexts=len(contexts),
        )

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                self._result_url,
                json=payload,
                headers={"X-Internal-Key": self._key},
            )
            resp.raise_for_status()
            log.info(
                "Java backend accepted pipeline result",
                job_id=self.job_id,
                status_code=resp.status_code,
            )

    async def push_progress(self, logs: list[dict]) -> None:
        """
        Push live log entries to Java so the frontend can poll them.
        Non-fatal if it fails — don't let a Redis/DB hiccup kill the pipeline.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    self._progress_url,
                    # camelCase — matches ProgressUpdate record in PipelineController
                    json={"jobId": self.job_id, "logs": logs},
                    headers={"X-Internal-Key": self._key},
                )
        except Exception as e:
            log.debug("Progress push failed (non-fatal)", error=str(e))

    async def mark_failed(self, error: str, logs: list[dict]) -> None:
        """Post a failure result so the job is marked failed in the DB."""
        await self.flush(
            entities=[], relationships=[], contexts={},
            pipeline_meta={"progress_logs": logs},
            status="failed",
            error_message=error,
            artifacts=[],
        )


# ── Serialisers — all keys camelCase ─────────────────────────────────────────

def _artifact_to_dict(a: Artifact) -> dict:
    """Serialise an Artifact to the ArtifactDto shape expected by Java."""
    return {
        "kind":       a.kind,
        "externalId": a.external_id,
        "content":    a.content[:16000] if a.content else "",  # hard cap — large files go to S3 later
        "sourceUri":  a.source_uri,
        "author":     a.author,
        "metadata":   a.metadata or {},
    }



def _entity_to_dict(e: ExtractedEntity, workspace_id: str = "") -> dict:
    tenant = workspace_slug_for(workspace_id)
    repo   = e.repo or "monorepo"
    etype  = NODE_TYPE_TAXONOMY.get(e.entity_type, "component")
    try:
        urn = to_urn(
            tenant=tenant, domain=DEFAULT_DOMAIN, repo=repo,
            entity_type=etype, qualified_name=e.name,
        )
    except ValueError:
        urn = f"urn:cb:{tenant}:{DEFAULT_DOMAIN}:{repo}:component:{e.name}"

    d = {
        "urn":                 urn,
        "entityType":          e.entity_type,
        "name":                e.name,
        "file":                e.file,
        "repo":                e.repo,
        "signature":           e.signature,
        "confidence":          e.confidence,
        "firstAppearedCommit": e.first_appeared_commit,
        "lastModifiedCommit":  e.last_modified_commit,
    }
    # Include query_text in metadata for DatabaseQuery entities
    # so the frontend / Ask AI can display the raw SQL/JPQL
    if e.query_text:
        d["queryText"] = e.query_text
    return d


def _rel_to_dict(r: ExtractedRelationship) -> dict:
    return {
        "fromEntity": r.from_entity,
        "fromType":   r.from_type,
        "edgeType":   r.edge_type,
        "toEntity":   r.to_entity,
        "toType":     r.to_type,
        "confidence": r.confidence,
        "evidence":   r.evidence,
    }


def _ctx_to_dict(external_id: str, c: BusinessContext) -> dict:
    return {
        "entityExternalId":    external_id,
        "purpose":             c.purpose,
        "historySummary":      c.history_summary,
        "invariants":          c.invariants or [],
        "changeRisk":          c.change_risk,
        "changeRiskReason":    c.change_risk_reason,
        "sourceConfidence":    c.source_confidence,
        "ownerTeam":           c.owner_team,
        "externalDependencies": getattr(c, "external_dependencies", []) or [],
        "gaps":                getattr(c, "gaps", []) or [],
    }
