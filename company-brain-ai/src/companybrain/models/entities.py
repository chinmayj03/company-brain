"""
Pydantic models for entities flowing through the LLM extraction pipeline.
These are internal pipeline models — not the DB models (those are in the Java backend).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RepoType(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    SHARED = "shared"


# ── ADR-005: Artifact model ────────────────────────────────────────────────────

@dataclass
class Artifact:
    """
    A content-addressed unit of knowledge emitted by any Collector.

    Every input that flows through the pipeline — source files, git commits,
    PRs, tickets, annotations, Slack threads — is represented as an Artifact
    before any LLM call.

    The dedup key on the Java side is (workspace_id, kind, external_id).
    ArtifactWriterService hashes the content and only emits a change event
    when the hash differs from the previously stored hash.

    See ADR-005: Artifact-Centric Knowledge Pipeline.
    """
    kind: str            # 'source_file' | 'commit' | 'pr' | 'annotation' |
                         # 'ticket' | 'slack_thread' | 'doc_page' | 'spec'

    external_id: str     # Stable, source-derived identifier within (workspace, kind).
                         # source_file → "repo/relative/path/to/File.java"
                         # commit      → "repoName::commitHash"
                         # pr          → "repoName::prNumber"
                         # ticket      → "system::ticketId"  (e.g. "jira::CB-1234")

    content: str         # Raw content string. Hashed by ArtifactWriterService.

    source_uri: Optional[str] = None     # Canonical back-link URL (browsable)
    author: Optional[str] = None         # Human or system author
    metadata: dict = field(default_factory=dict)  # Kind-specific extras


# ── Git Collection models ─────────────────────────────────────────────────────

@dataclass
class CommitEvent:
    """A single commit that touched a file relevant to the target API."""
    commit_hash: str
    timestamp: datetime
    author: str
    message: str
    repo: str
    repo_type: RepoType
    file_path: str
    github_repo_url: Optional[str] = None
    diff: Optional[str] = None
    pr_title: Optional[str] = None
    pr_body: Optional[str] = None
    pr_url: Optional[str] = None
    linked_tickets: list[str] = field(default_factory=list)
    ticket_summaries: list[str] = field(default_factory=list)


@dataclass
class CommitCluster:
    """
    A group of related commits from one or more repos.
    Grouping criteria: same PR, same ticket, or within 24 hours.
    """
    cluster_id: str                 # = first commit's hash
    approximate_date: datetime
    commits: list[CommitEvent]
    cluster_reason: str             # 'same_pr' | 'same_ticket' | 'time_proximity'

    @property
    def has_rich_pr(self) -> bool:
        return any(c.pr_body and len(c.pr_body) > 100 for c in self.commits)

    @property
    def combined_pr_bodies(self) -> list[str]:
        return [c.pr_body for c in self.commits if c.pr_body]


# ── LLM Extraction models ─────────────────────────────────────────────────────

@dataclass
class ExtractedEntity:
    """
    An entity extracted by LLM Pass 1 (entity extraction).
    Represents any named code artifact relevant to the target API.
    """
    entity_type: str       # Function | ApiEndpoint | SchemaField | DatabaseColumn | DatabaseQuery | etc.
    name: str
    file: str
    repo: str
    signature: str
    last_modified_commit: str
    confidence: float
    first_appeared_commit: Optional[str] = None
    # Compact body snippet used by RelationshipExtractor to find call sites
    # (e.g. shows `competitorsService.getPayerCompetitors()` in controller body)
    code_snippet: Optional[str] = None
    # For DatabaseQuery entities: the raw SQL/JPQL string
    query_text: Optional[str] = None

    # ADR-006 §29: Structural-hints enrichment fields.
    # Populated by EntityExtractor.extract_with_structural_hints() when the
    # structural index already provides entity identity — the LLM only adds
    # these semantic labels (cheaper than full-code extraction).
    structural_purpose:     Optional[str]       = None   # one-line business description
    structural_data_reads:  list[str]           = field(default_factory=list)  # table/service names read
    structural_data_writes: list[str]           = field(default_factory=list)  # table/service names written
    structural_risk_flags:  list[str]           = field(default_factory=list)  # e.g. ['payment', 'pii']
    structural_change_risk: Optional[str]       = None   # 'high' | 'medium' | 'low'

    # ADR-0052 P6: human-curation flags. ``pinned=True`` freezes the row
    # against rebuild overwrites; ``proposed=True`` hides the entity from
    # query responses unless the caller explicitly asks for proposed nodes.
    # Both default False so nothing changes for entities written before V15.
    pinned:   bool                              = False
    proposed: bool                              = False

    @property
    def external_id(self) -> str:
        """Stable identifier used as the node external_id in the graph."""
        return f"{self.repo}/{self.file}::{self.name}"


@dataclass
class ExtractedRelationship:
    """
    A dependency relationship extracted by LLM Pass 2.
    Maps directly to an edge in the dependency graph.
    """
    from_entity: str        # entity external_id
    from_type: str
    edge_type: str          # CALLS | READS_COLUMN | RENDERS_FIELD | CALLS_ENDPOINT | etc.
    to_entity: str          # entity external_id
    to_type: str
    confidence: float
    evidence: str           # Code snippet or reasoning that supports this relationship


@dataclass
class BusinessContext:
    """
    Business context synthesised by LLM Pass 3 for a single entity.
    Stored in the node_context table.

    Field groups:
      ── Core narrative ──
      purpose            — one-paragraph description of what this does and why.
      history_summary    — 1-2 sentence summary of how this evolved (commits/PRs).
      business_capability— the product capability this serves (e.g. "competitor pricing lookup").
      personas_affected  — list of user/operator roles impacted ("payer admin", "ops on-call").

      ── Behavior / contract ──
      invariants         — must-hold rules (input prerequisites, output guarantees).
      failure_modes      — known ways this can break and what the user observes.
      side_effects       — observable effects beyond the return value (logs, events, audit).
      idempotency        — "idempotent" | "non-idempotent" | "unknown".

      ── Risk / change management ──
      change_risk        — LOW | MEDIUM | HIGH
      change_risk_reason — why the rating
      blast_radius       — list of downstream entities affected by changes here.
      deprecation_status — "active" | "deprecated" | "experimental" | "internal-only".

      ── Data sensitivity / compliance ──
      data_sensitivity   — "public" | "internal" | "confidential" | "pii" | "phi" | "regulated".
      compliance_tags    — e.g. ["sox", "pci", "hipaa", "gdpr"].

      ── Performance / ops ──
      performance_notes  — N+1 risks, query cost, hot-path latency expectations.

      ── Provenance ──
      source_confidence  — high | medium | low
      owner_team         — owning team (from CODEOWNERS / annotations / heuristics).
      external_dependencies — third-party services / SDKs this leans on.
      related_concepts   — domain terms, similar entities, or canonical alternatives.
      gaps               — open questions this synthesis could not resolve.
    """
    entity_external_id: str
    purpose: str
    history_summary: str
    invariants: list[str]
    change_risk: str
    change_risk_reason: str
    source_confidence: str

    # ── Expanded fields (default-empty so old payloads still deserialise) ────
    business_capability: Optional[str] = None
    personas_affected: list[str] = field(default_factory=list)

    failure_modes: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    idempotency: Optional[str] = None

    blast_radius: list[str] = field(default_factory=list)
    deprecation_status: Optional[str] = None

    data_sensitivity: Optional[str] = None
    compliance_tags: list[str] = field(default_factory=list)

    performance_notes: Optional[str] = None

    owner_team: Optional[str] = None
    external_dependencies: list[str] = field(default_factory=list)
    related_concepts: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class PipelineGap:
    """
    An unexplained behaviour or annotation conflict detected by LLM Pass 4.
    Surfaced to the user as a follow-up question.
    """
    entity_external_id: str
    gap_type: str            # 'unexplained_behaviour' | 'annotation_vs_code' | 'missing_owner'
                             # | 'untested_critical_path' | 'data_contract_ambiguity'
    description: str
    suggested_question: Optional[str] = None
    severity: str = "medium"  # 'critical' | 'high' | 'medium'
    resolution_needed: bool = False


# ── API Request / Response models ─────────────────────────────────────────────

class PipelineStartRequest(BaseModel):
    """Request body for POST /pipeline/start"""
    endpoint_path: str = Field(..., example="/payments/charge")
    http_method: str = Field(default="POST", example="POST")
    branch: str = Field(default="main")
    repos: list[RepoConfig]
    workspace_id: str


class RepoConfig(BaseModel):
    """
    Repo can be specified as:
      - local_path only  → reads the already-cloned repo on disk (fully local, nothing leaves your machine)
      - url only         → treated as the git path (remote URL or local path)
      - both             → local_path used for git ops, url used for GitHub PR enrichment
    """
    url: Optional[str] = None       # GitHub/GitLab URL for PR enrichment (optional)
    type: RepoType = RepoType.BACKEND
    branch: str = "main"
    local_path: Optional[str] = None  # Absolute path to local clone — takes priority over url


class PipelineJobResponse(BaseModel):
    job_id: str
    status: str                 # 'queued' | 'running' | 'completed' | 'failed'
    progress: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None


class QueryRequest(BaseModel):
    """Request body for POST /query"""
    question: str = Field(..., example="What breaks if I rename amount_cents?")
    context_symbol: Optional[str] = None   # Symbol the cursor is on (from VS Code)
    file_path: Optional[str] = None        # Current file in editor
    workspace_id: str
    repo_path: Optional[str] = None        # Repo root containing .brain/; falls back to BRAIN_ROOT env var
    max_hops: int = Field(default=3, ge=1, le=5)


class LegacyQueryResponse(BaseModel):
    """Legacy flat response kept for any callers that haven't migrated.

    New code should import companybrain.models.query_response.QueryResponse.
    """
    answer: str
    sources: list[dict]
    affected_nodes: list[dict]
    confidence: str


# Alias kept for backward compatibility; routes/query.py uses the new typed model.
QueryResponse = LegacyQueryResponse


# ── ADR-0057 additions ────────────────────────────────────────────────────────
# Universal File Extraction: dataclasses emitted by the per-kind extractors in
# companybrain.extractors. These are pipeline-internal models — persistence into
# Neo4j is owned by a follow-up PR. See docs/adrs/ADR-0057-universal-file-extraction.md.

@dataclass
class Documentation:
    """A Markdown / AsciiDoc / RST document, treated as a structured artifact."""
    file: str
    repo: str
    title: str                           # first H1 heading or filename stem
    headings: list[str] = field(default_factory=list)   # all H1/H2 heading texts in order
    code_blocks: list[str] = field(default_factory=list)  # fenced code block contents
    raw_text: str = ""                   # full doc body, for downstream summarisation


@dataclass
class ConfigKey:
    """A single key/value pair from a config file (YAML/TOML/properties/.env)."""
    file: str
    repo: str
    path: str                            # dotted path e.g. "spring.datasource.url"
    value: str                           # stringified value
    semantic_tag: Optional[str] = None   # populated by semantic_tags.tag_config_path


@dataclass
class Dependency:
    """A build-manifest dependency entry (POM / npm / Cargo / pip / etc.)."""
    file: str
    repo: str
    name: str                            # full coordinate, e.g. "org.postgresql:postgresql" or "react"
    version: Optional[str] = None
    scope: Optional[str] = None          # "compile" | "test" | "runtime" | "dev" | None
    ecosystem: str = ""                  # "maven" | "npm" | "pip" | "cargo" | "go" | etc.


@dataclass
class BuildPlugin:
    """A build-tool plugin (Maven plugin, Gradle plugin, etc.)."""
    file: str
    repo: str
    name: str
    version: Optional[str] = None


@dataclass
class ContainerImage:
    """A FROM directive in a Dockerfile — base image used by a stage."""
    file: str
    repo: str
    name: str                            # e.g. "openjdk:17-jdk-slim"
    stage_alias: Optional[str] = None    # name from "FROM x AS stage"


@dataclass
class RuntimeStage:
    """A logical stage in a multi-stage Dockerfile (alias + commands)."""
    file: str
    repo: str
    name: str                            # stage alias or "stage_N"
    base_image: str
    exposed_ports: list[int] = field(default_factory=list)
    entrypoint: Optional[str] = None
    cmd: Optional[str] = None


@dataclass
class ServiceDefinition:
    """A service block in a docker-compose file."""
    file: str
    repo: str
    name: str                            # service key, e.g. "postgres"
    image: Optional[str] = None
    ports: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkflowJob:
    """A job in a CI workflow (GitHub Actions / GitLab / Jenkins / etc.)."""
    file: str
    repo: str
    name: str
    triggers: list[str] = field(default_factory=list)   # ["push", "pull_request", ...]
    runs_on: Optional[str] = None
    steps: list[str] = field(default_factory=list)      # step names or run-commands
    ci_system: str = ""                  # "github" | "gitlab" | "jenkins" | "circle" | ...


@dataclass
class BehavioralSpec:
    """A test method recast as a behavioural spec (GIVEN/WHEN/THEN)."""
    file: str
    repo: str
    specifies_method: str                # URN/external_id of the method under test
    given: str
    when: str
    then: str


@dataclass
class MethodDoc:
    """A Javadoc / docstring / JSDoc block attached to a method."""
    file: str
    repo: str
    method_urn: str
    summary: str
    params: dict[str, str] = field(default_factory=dict)   # name → description
    returns: Optional[str] = None
    throws: dict[str, str] = field(default_factory=dict)   # exception → description


@dataclass
class ExtractedBatch:
    """
    Per-file output of a universal extractor.

    Each list is heterogeneous-by-extractor — populate only the buckets the
    extractor produces (e.g. ConfigExtractor emits config_keys only).
    """
    file: str
    repo: str
    extractor_kind: str                  # "doc" | "config" | "manifest_xml" | ...
    documentation:    list[Documentation]    = field(default_factory=list)
    config_keys:      list[ConfigKey]        = field(default_factory=list)
    dependencies:     list[Dependency]       = field(default_factory=list)
    build_plugins:    list[BuildPlugin]      = field(default_factory=list)
    container_images: list[ContainerImage]   = field(default_factory=list)
    runtime_stages:   list[RuntimeStage]     = field(default_factory=list)
    service_defs:     list[ServiceDefinition] = field(default_factory=list)
    workflow_jobs:    list[WorkflowJob]      = field(default_factory=list)
    behavioral_specs: list[BehavioralSpec]   = field(default_factory=list)
    method_docs:      list[MethodDoc]        = field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return (
            len(self.documentation) + len(self.config_keys) + len(self.dependencies)
            + len(self.build_plugins) + len(self.container_images)
            + len(self.runtime_stages) + len(self.service_defs)
            + len(self.workflow_jobs) + len(self.behavioral_specs) + len(self.method_docs)
        )


# Edge type constants emitted alongside the new entities. Centralised here so
# downstream persistence layers can iterate them without string-typos.
EDGE_DOCUMENTS         = "DOCUMENTS"
EDGE_EXAMPLES          = "EXAMPLES"
EDGE_CONFIGURES        = "CONFIGURES"
EDGE_DEPENDS_ON_LIB    = "DEPENDS_ON_LIBRARY"
EDGE_BASED_ON          = "BASED_ON"
EDGE_EXPOSES_PORT      = "EXPOSES_PORT"
EDGE_RUNS_COMMAND      = "RUNS_COMMAND"
EDGE_DEPLOYS           = "DEPLOYS"
EDGE_LINKS_TO          = "LINKS_TO"
EDGE_RUNS_ON_PR        = "RUNS_ON_PR"
EDGE_RUNS_ON_PUSH      = "RUNS_ON_PUSH"
EDGE_SPECIFIES         = "SPECIFIES"

ADR_0057_EDGE_TYPES = frozenset({
    EDGE_DOCUMENTS, EDGE_EXAMPLES, EDGE_CONFIGURES, EDGE_DEPENDS_ON_LIB,
    EDGE_BASED_ON, EDGE_EXPOSES_PORT, EDGE_RUNS_COMMAND, EDGE_DEPLOYS,
    EDGE_LINKS_TO, EDGE_RUNS_ON_PR, EDGE_RUNS_ON_PUSH, EDGE_SPECIFIES,
})
