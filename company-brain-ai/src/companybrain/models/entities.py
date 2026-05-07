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
    """
    entity_external_id: str
    purpose: str
    history_summary: str
    invariants: list[str]
    change_risk: str         # LOW | MEDIUM | HIGH
    change_risk_reason: str
    source_confidence: str   # high | medium | low
    owner_team: Optional[str] = None
    external_dependencies: list[str] = field(default_factory=list)
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
    max_hops: int = Field(default=3, ge=1, le=5)


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    affected_nodes: list[dict]
    confidence: str
