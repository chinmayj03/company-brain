"""
Pydantic models for entities flowing through the LLM extraction pipeline.
These are internal pipeline models — not the DB models (those are in the Java backend).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

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

    # ── ADR-0056 additions ────────────────────────────────────────────────
    # Populated by VerifierLoop between Stage 2.5 and Stage 3. The default
    # "skipped" means the verifier never ran (skipped via env flag or pre-V16
    # payload), so old reads remain visible. /query excludes
    # ``verified in {"hallucinated", "conflicting"}`` unless include_unverified
    # is set on the request.
    verified:        Literal["confirmed", "fuzzy", "hallucinated",
                             "conflicting", "skipped"] = "skipped"
    verifier_mode:   Optional[Literal["deterministic", "subagent",
                                      "self_correction"]] = None
    verifier_notes:  str                                  = ""

    # ── ADR-0059 additions ────────────────────────────────────────────────
    # Populated by Pass T1 (TemporalPass) from git blame data. None when the
    # pass hasn't run for this entity (e.g. file is not under git, blame
    # disabled via env flag, or the entity's source location couldn't be
    # resolved).
    temporal: Optional["TemporalOwnership"] = None

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

    # ── ADR-0060 additions ──────────────────────────────────────────────────
    # Schema version. v1 = original 21-field shape. v2 = adds the typed
    # engineering-rigour fields below. Old payloads without this field
    # deserialise as 1 by default so v1↔v2 can co-exist during migration.
    schema_version: int = 1

    # Answers "is it safe to retry?" SELECT-only methods → True. Any
    # INSERT/UPDATE/DELETE → False. None = couldn't determine from body.
    is_idempotent: Optional[bool] = None

    # Per-parameter null contract. Keys are parameter names; values are one
    # of {"checked", "throws", "tolerates", "unchecked"}.
    #   checked   = explicit if-null branch handles the null
    #   throws    = if-null path throws (NPE, IllegalArgumentException, etc.)
    #   tolerates = passed through to a callee that handles null
    #   unchecked = NPE risk; no null handling at this level
    null_handling: dict[str, str] = field(default_factory=dict)

    # Extracted from @Transactional or equivalent. One of
    # {"read_only", "read_write", "no_transaction"} or None when no tx.
    transaction_mode: Optional[str] = None

    # Codebase-convention violations: literal-instead-of-constant,
    # potential_n_plus_1, broad_exception_catch, etc.
    anti_patterns: list[str] = field(default_factory=list)

    # Free-form annotations: "uses LATERAL because unnest references outer
    # column", "materialised join to avoid two-table scan", etc.
    engineering_notes: list[str] = field(default_factory=list)

    # Rough complexity class. One of {"O(1)", "O(log n)", "O(n)",
    # "O(n log n)", "O(n²)", "unbounded"} or None when ambiguous.
    performance_class: Optional[str] = None

    # Auth posture. One of {"public", "authenticated", "authorised",
    # "internal_only", "admin_only"} or None when unknown.
    security_class: Optional[str] = None


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
    # ADR-0056: opt into surfacing entities the verifier flagged as hallucinated
    # or conflicting. Defaults False so /query callers see only verified sources.
    include_unverified: bool = Field(default=False)
    # ADR-0061 E5: when the previous response returned a ClarificationOption set,
    # the client re-issues the same question with ``interpret`` carrying the
    # chosen option id. The route uses it to inject an interpretation hint into
    # the user message and skip the ambiguity detector.
    interpret: Optional[str] = Field(default=None)


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


# ── ADR-0055 additions ─────────────────────────────────────────────────────────
# Cross-file cross-cutting extraction pass (Stage 2.5). Emits Pattern,
# SharedInvariant, and DomainEntity entities plus new edge types that wire
# concrete code entities to the inferred cross-cutting facts.

# Edge type constants. These are also appended to companybrain.edges.taxonomy
# (the canonical SOT). Mirrored here so call-sites that build edges through
# the entity-model module pick the same string.
EDGE_IMPLEMENTS_PATTERN     = "IMPLEMENTS_PATTERN"
EDGE_VIOLATES_PATTERN       = "VIOLATES_PATTERN"
EDGE_SHARES_INVARIANT       = "SHARES_INVARIANT"
EDGE_REPRESENTS             = "REPRESENTS"
EDGE_HAS_IMPLICIT_CONTRACT  = "HAS_IMPLICIT_CONTRACT"


@dataclass
class Pattern:
    """
    A repeating idiom or convention spanning multiple call sites.

    Emitted by SP-1 (idiom_detector) for deterministic patterns and
    optionally by SP-3 (invariant_inferrer) when the LLM names a pattern
    explicitly. ``instance_count`` is the number of distinct entities that
    implement the pattern; the corresponding IMPLEMENTS_PATTERN edges carry
    the membership.
    """
    entity_type: str = "Pattern"
    name: str = ""
    description: str = ""
    instance_count: int = 0
    confidence: float = 0.0
    inferred_from: str = "deterministic"   # "deterministic" | "llm"
    instance_urns: list[str] = field(default_factory=list)

    @property
    def external_id(self) -> str:
        """Stable identifier used as the node external_id in the graph."""
        return f"pattern::{self.name}"


@dataclass
class SharedInvariant:
    """
    A statement that holds across a window of related methods, not within
    one. Example: "all reads of plan_info filter is_current=true".
    """
    entity_type: str = "SharedInvariant"
    name: str = ""
    statement: str = ""
    affected_method_urns: list[str] = field(default_factory=list)
    evidence_method_urns: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def external_id(self) -> str:
        return f"invariant::{self.name}"


@dataclass
class DomainEntity:
    """
    A business/domain concept inferred from naming patterns across many
    classes — e.g. "Payer" inferred from PayerInfo, PayerPlan, BasePayer,
    payer_id. Anchored to a handful of representative classes via REPRESENTS
    edges.
    """
    entity_type: str = "DomainEntity"
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    anchor_class_urns: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def external_id(self) -> str:
        return f"domain::{self.name}"


@dataclass
class ImplicitContract:
    """
    Pre- and post-conditions a method seems to assume from its callers.
    Attached to a method's BusinessContext rather than stored separately;
    we keep the dataclass to give SP-4 a typed return value.
    """
    method_external_id: str = ""
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    confidence: float = 0.0


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
    # ADR-0061 E7: vision-extracted diagrams from docs/**/*.{png,svg}.
    diagrams:         list["Diagram"]        = field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return (
            len(self.documentation) + len(self.config_keys) + len(self.dependencies)
            + len(self.build_plugins) + len(self.container_images)
            + len(self.runtime_stages) + len(self.service_defs)
            + len(self.workflow_jobs) + len(self.behavioral_specs) + len(self.method_docs)
            + len(self.diagrams)
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


# ── ADR-0058 additions ────────────────────────────────────────────────────────
# Generated-Code & Schema-Format Awareness: typed entities for SQL DDL,
# generated jOOQ Tables.java bindings, OpenAPI specs, Protobuf and GraphQL.
# These are pipeline-internal dataclasses; persistence into Neo4j is owned by
# a follow-up PR (same staging pattern as ADR-0057). See
# docs/adrs/ADR-0058-generated-code-and-schema-awareness.md.

# ── S1: SQL DDL ─────────
@dataclass
class DatabaseTable:
    entity_type: str = "DatabaseTable"
    name: str = ""
    schema: str = "public"
    source_file: str = ""
    line_range: tuple[int, int] = (0, 0)
    primary_key_columns: list[str] = field(default_factory=list)
    is_partitioned: bool = False
    partition_strategy: Optional[str] = None  # "RANGE" | "LIST" | "HASH" | None
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"table::{self.schema}.{self.name}"


@dataclass
class DatabaseColumn:
    entity_type: str = "DatabaseColumn"
    name: str = ""
    table_urn: str = ""
    type: str = ""             # raw type string e.g. "text", "text[]", "varchar(64)", "jsonb"
    nullable: bool = True
    default_value: Optional[str] = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    fk_references: Optional[str] = None   # "schema.table.column" when FK
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"column::{self.table_urn}.{self.name}"

    @property
    def is_array(self) -> bool:
        return self.type.rstrip().endswith("]")


@dataclass
class DatabaseIndex:
    entity_type: str = "DatabaseIndex"
    name: str = ""
    table_urn: str = ""
    columns: list[str] = field(default_factory=list)
    is_unique: bool = False
    where_clause: Optional[str] = None    # populated for partial indexes
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"index::{self.name}"


@dataclass
class MigrationFile:
    """A migration file (e.g. Flyway V1__baseline.sql / Liquibase changeset)."""
    entity_type: str = "MigrationFile"
    file: str = ""
    repo: str = ""
    version: str = ""           # "V1", "V12_3", "R__seed", or "" if not Flyway-named
    creates: list[str] = field(default_factory=list)  # table external_ids
    alters: list[str] = field(default_factory=list)   # table external_ids

    @property
    def external_id(self) -> str:
        return f"migration::{self.file}"


# ── S2: jOOQ Tables.java ─────────
@dataclass
class JooqTableBinding:
    """Maps a generated jOOQ class constant to a DatabaseTable."""
    entity_type: str = "JooqTableBinding"
    jooq_class: str = ""             # fully-qualified class, e.g. "com.example.db.Tables"
    java_constant: str = ""          # e.g. "PLAN_INFO"
    db_table_urn: str = ""           # "table::public.plan_info" — resolved by schema_resolver
    db_table_name: str = ""          # raw DB name as referenced in Tables.java
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"jooq_table::{self.jooq_class}.{self.java_constant}"


@dataclass
class JooqFieldBinding:
    """Maps a jOOQ field constant (TABLE.FIELD) to a DatabaseColumn."""
    entity_type: str = "JooqFieldBinding"
    jooq_constant: str = ""          # e.g. "PLAN_INFO.PAYER_PLAN_ID"
    db_column_urn: str = ""          # resolved column URN
    db_column_name: str = ""         # raw column name from Tables.java
    db_type: str = ""                # jOOQ-emitted SQLDataType — e.g. "VARCHAR(64)"
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"jooq_field::{self.jooq_constant}"


# ── S3: OpenAPI ─────────
@dataclass
class OpenAPIOperation:
    entity_type: str = "OpenAPIOperation"
    operation_id: str = ""
    method: str = ""                 # uppercase HTTP verb
    path: str = ""
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    request_schema_ref: Optional[str] = None
    response_schemas: dict[int, str] = field(default_factory=dict)
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        # operation_id may be empty in older specs; fall back to method+path.
        oid = self.operation_id or f"{self.method.upper()}_{self.path}"
        return f"openapi::{oid}"


@dataclass
class OpenAPISchema:
    entity_type: str = "OpenAPISchema"
    name: str = ""
    type: str = ""                   # "object" | "array" | "string" | ...
    properties: dict[str, dict] = field(default_factory=dict)  # field_name → {type, format, ...}
    required: list[str] = field(default_factory=list)
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"openapi_schema::{self.name}"


# ── S4: Protobuf ─────────
@dataclass
class ProtoMessage:
    entity_type: str = "ProtoMessage"
    name: str = ""
    package: str = ""
    fields: list[dict] = field(default_factory=list)   # [{name, type, number, repeated}]
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"proto_message::{self.package}.{self.name}" if self.package else f"proto_message::{self.name}"


@dataclass
class ProtoService:
    entity_type: str = "ProtoService"
    name: str = ""
    package: str = ""
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"proto_service::{self.package}.{self.name}" if self.package else f"proto_service::{self.name}"


@dataclass
class ProtoRpc:
    entity_type: str = "ProtoRpc"
    name: str = ""
    service_urn: str = ""
    request_type: str = ""
    response_type: str = ""
    client_streaming: bool = False
    server_streaming: bool = False
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"proto_rpc::{self.service_urn}.{self.name}"


# ── S5: GraphQL ─────────
@dataclass
class GraphQLType:
    entity_type: str = "GraphQLType"
    name: str = ""
    kind: str = ""                   # "OBJECT" | "INTERFACE" | "UNION" | "ENUM" | "SCALAR" | "INPUT_OBJECT"
    fields: list[dict] = field(default_factory=list)   # [{name, type, args}]
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"gql_type::{self.name}"


@dataclass
class GraphQLField:
    entity_type: str = "GraphQLField"
    name: str = ""
    parent_type_urn: str = ""
    type: str = ""                   # GraphQL type spelling, e.g. "[User!]!"
    args: list[dict] = field(default_factory=list)
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"gql_field::{self.parent_type_urn}.{self.name}"


@dataclass
class GraphQLQuery:
    """Top-level Query / Mutation / Subscription field — a callable operation."""
    entity_type: str = "GraphQLQuery"
    name: str = ""
    operation: str = "query"         # "query" | "mutation" | "subscription"
    return_type: str = ""
    args: list[dict] = field(default_factory=list)
    source_file: str = ""
    repo: str = ""

    @property
    def external_id(self) -> str:
        return f"gql_op::{self.operation}::{self.name}"


# Edge type constants emitted by the schema extractors / resolver.
EDGE_MIGRATION_CREATES   = "MIGRATION_CREATES"
EDGE_MIGRATION_ALTERS    = "MIGRATION_ALTERS"
EDGE_INDEXES             = "INDEXES"
EDGE_FOREIGN_KEY         = "FOREIGN_KEY"
EDGE_BINDS_TO_TABLE      = "BINDS_TO_TABLE"
EDGE_BINDS_TO_COLUMN     = "BINDS_TO_COLUMN"
EDGE_DOCUMENTS_OPENAPI   = "DOCUMENTS"          # reuses the ADR-0057 constant intentionally
EDGE_SCHEMA_REQUEST      = "SCHEMA_REQUEST"
EDGE_SCHEMA_RESPONSE     = "SCHEMA_RESPONSE"
EDGE_IMPLEMENTS_RPC      = "IMPLEMENTS_RPC"
EDGE_RESOLVES            = "RESOLVES"
EDGE_READS_COLUMN        = "READS_COLUMN"

ADR_0058_EDGE_TYPES = frozenset({
    EDGE_MIGRATION_CREATES, EDGE_MIGRATION_ALTERS, EDGE_INDEXES,
    EDGE_FOREIGN_KEY, EDGE_BINDS_TO_TABLE, EDGE_BINDS_TO_COLUMN,
    EDGE_DOCUMENTS_OPENAPI, EDGE_SCHEMA_REQUEST, EDGE_SCHEMA_RESPONSE,
    EDGE_IMPLEMENTS_RPC, EDGE_RESOLVES, EDGE_READS_COLUMN,
})


@dataclass
class SchemaEdge:
    """Lightweight edge record emitted by the schema extractors / resolver.

    Kept simple (not an ExtractedRelationship) because these edges connect
    schema entities that don't yet have ``ExtractedEntity`` wrappers.
    """
    edge_type: str
    from_urn: str
    to_urn: str
    evidence: str = ""
    confidence: float = 1.0


@dataclass
class SchemaExtractedBatch:
    """Extension of the ExtractedBatch concept for the ADR-0058 entity types.

    Kept as a sibling of ExtractedBatch so the existing ``entity_count`` and
    ADR-0057 buckets keep their shape. Universal-extraction routes ADR-0058
    extractors to emit this; the orchestrator surfaces totals in telemetry.
    """
    file: str = ""
    repo: str = ""
    extractor_kind: str = ""

    tables:           list[DatabaseTable]      = field(default_factory=list)
    columns:          list[DatabaseColumn]     = field(default_factory=list)
    indexes:          list[DatabaseIndex]      = field(default_factory=list)
    migrations:       list[MigrationFile]      = field(default_factory=list)
    jooq_tables:      list[JooqTableBinding]   = field(default_factory=list)
    jooq_fields:      list[JooqFieldBinding]   = field(default_factory=list)
    openapi_ops:      list[OpenAPIOperation]   = field(default_factory=list)
    openapi_schemas:  list[OpenAPISchema]      = field(default_factory=list)
    proto_messages:   list[ProtoMessage]       = field(default_factory=list)
    proto_services:   list[ProtoService]       = field(default_factory=list)
    proto_rpcs:       list[ProtoRpc]           = field(default_factory=list)
    gql_types:        list[GraphQLType]        = field(default_factory=list)
    gql_fields:       list[GraphQLField]       = field(default_factory=list)
    gql_ops:          list[GraphQLQuery]       = field(default_factory=list)
    edges:            list[SchemaEdge]         = field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return (
            len(self.tables) + len(self.columns) + len(self.indexes)
            + len(self.migrations) + len(self.jooq_tables) + len(self.jooq_fields)
            + len(self.openapi_ops) + len(self.openapi_schemas)
            + len(self.proto_messages) + len(self.proto_services) + len(self.proto_rpcs)
            + len(self.gql_types) + len(self.gql_fields) + len(self.gql_ops)
        )

    def to_extracted_batch(self) -> ExtractedBatch:
        """Wrap as an ADR-0057 ExtractedBatch so the existing universal-extraction
        pipeline can carry the file + repo + kind metadata uniformly. The
        ADR-0058 buckets are accessed via ``getattr(batch, '_schema_batch', ...)``
        attached by the schema dispatcher; see schema_resolver."""
        return ExtractedBatch(file=self.file, repo=self.repo, extractor_kind=self.extractor_kind)


# ── ADR-0059 additions ────────────────────────────────────────────────────────
# Temporal ownership + domain inference passes. Pass T1 derives per-entity
# ownership/age/churn facts from git blame; Pass T2 runs one LLM call per repo
# to infer DomainEntity rows (shape shared with ADR-0055); Pass T2b derives an
# OnboardingPath per DomainEntity by picking representative anchor classes.

@dataclass
class TemporalOwnership:
    """
    Per-entity ownership / age / churn summary derived from git blame.

    Attached to an ExtractedEntity via ``entity.temporal``. Computed by Pass T1
    (``pipeline/temporal_pass.py``) from blame data produced by
    ``pipeline/git_blame_aggregator.py``.

    Field semantics:
      primary_author   — the email/name with the most blame lines.
      co_authors       — ``(author, line_count)`` sorted descending; primary
                         author is index 0 in this list.
      bus_factor       — count of authors holding >= 10% of the line share.
      age_days         — days between the first commit touching the entity and
                         ``last_touched_at``.
      last_touched_at  — datetime of the most recent commit on the range.
      last_touched_by  — author of that most recent commit.
      churn_30d        — distinct commits on the range within the last 30 days.
      churn_90d        — distinct commits on the range within the last 90 days.
    """
    primary_author: str = ""
    co_authors: list[tuple[str, int]] = field(default_factory=list)
    bus_factor: int = 0
    age_days: int = 0
    last_touched_at: Optional[datetime] = None
    last_touched_by: str = ""
    churn_30d: int = 0
    churn_90d: int = 0


@dataclass
class RiskAlert:
    """
    A risk surface derived from TemporalOwnership data.

    Emitted by ``pipeline/risk_alert_detector.py`` after Pass T1 has populated
    ``entity.temporal`` for every entity that could be blamed. Three kinds:

      - ``bus_factor_one``   single-point-of-failure: primary_author > 70% of
                             lines and the runner-up has < 10%.
      - ``high_churn``       instability / active redesign: churn_30d > 5.
      - ``stale_owner_left`` knowledge departure risk: the last toucher has
                             not committed anywhere in the repo in 90 days.

    Severity bucketing is heuristic (LOW/MED/HIGH); callers display the
    ``message`` field verbatim.
    """
    entity_type: str = "RiskAlert"
    kind: Literal["bus_factor_one", "high_churn", "stale_owner_left"] = "bus_factor_one"
    affected_entity_urn: str = ""
    severity: Literal["LOW", "MED", "HIGH"] = "MED"
    message: str = ""

    @property
    def external_id(self) -> str:
        return f"alert::{self.kind}::{self.affected_entity_urn}"


@dataclass
class OnboardingPath:
    """
    A curated reading order for a DomainEntity — the answer to "what should
    a new hire read first to understand X?"

    Built by ``pipeline/onboarding_path_builder.py`` from the DomainEntity
    rows produced by Pass T2 plus the anchor classes' file paths. We rank
    classes by structural role (Controller → Service → Repository) so the
    output reads top-of-stack first.
    """
    entity_type: str = "OnboardingPath"
    domain_name: str = ""
    domain_urn: str = ""
    anchor_class_urns: list[str] = field(default_factory=list)
    rationale: str = ""           # 1-line description of why these were picked

    @property
    def external_id(self) -> str:
        return f"onboarding::{self.domain_name}"


# Edge type constants for ADR-0059 (graph wiring of alerts + onboarding paths).
EDGE_AFFECTS         = "AFFECTS"           # RiskAlert -> entity it warns about
EDGE_GUIDES          = "GUIDES"            # OnboardingPath -> DomainEntity it guides
EDGE_READ_FIRST      = "READ_FIRST"        # OnboardingPath -> first anchor class

ADR_0059_EDGE_TYPES = frozenset({
    EDGE_AFFECTS, EDGE_GUIDES, EDGE_READ_FIRST,
})


# ── ADR-0061 additions ────────────────────────────────────────────────────────
# Iterative exploration + the remaining Claude-Code patterns. Adds entity types
# for diagrams (E7) and clarification options (E5), plus the SimilarTo edge
# constant used by cross-repo similarity surfacing (E6).
#
# See docs/adrs/ADR-0061-iterative-exploration-and-additional-claude-code-patterns.md.

@dataclass
class DiagramComponent:
    """A box / actor / system labelled in a diagram. Free-form name + role hint."""
    name: str
    role: Optional[str] = None        # e.g. "service", "database", "queue", "client"


@dataclass
class DiagramEdge:
    """A labelled arrow between two DiagramComponents."""
    source: str                       # component name (matches a DiagramComponent.name)
    target: str
    label: Optional[str] = None       # e.g. "publishes", "writes to"


@dataclass
class Diagram:
    """
    A vision-extracted summary of an image in ``docs/`` — the answer to "show
    the architecture". The extractor's job is to enumerate boxes + arrows; the
    domain mapping happens via ``REPRESENTS`` edges into DomainEntity rows.

    ``components`` and ``edges`` are deliberately loose (free-form strings) so
    we don't have to teach the vision model the brain's URN scheme.
    """
    repo: str
    file_path: str                    # relative to repo root, e.g. "docs/architecture.png"
    title: str = ""
    description: str = ""
    components: list[DiagramComponent] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    qualified_name: str = ""          # set at write time to "<repo>.<rel-no-ext>"

    @property
    def external_id(self) -> str:
        return f"diagram::{self.repo}::{self.file_path}"


@dataclass
class ClarificationOption:
    """
    One interpretation of an ambiguous /query question (E5). Returned to the
    UI so the user can pick a path instead of getting a wrong answer.

    ``id`` is a stable token the caller passes back on the retry as the
    ``interpret`` field on QueryRequest. The legacy free-form text query is
    re-issued with the chosen interpretation injected into the user message.
    """
    id: str                           # "json_key" | "db_column" | "both" | …
    description: str                  # one-line plain English for chip rendering


# Edge type constants for ADR-0061. EDGE_REPRESENTS is reused from ADR-0055
# (already defined above for DomainEntity wiring) — we simply route Diagram
# rows through the same edge type instead of inventing a parallel constant.
EDGE_SIMILAR_TO     = "SIMILAR_TO"        # Pattern -> Pattern across workspaces (E6)

ADR_0061_EDGE_TYPES = frozenset({
    EDGE_SIMILAR_TO, EDGE_REPRESENTS,
})
