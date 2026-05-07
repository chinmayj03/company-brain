"""
GitCollector — Stage 1 of the context builder pipeline.

Given an API endpoint path and a list of repositories, finds every
code artifact related to that endpoint and builds a CommitTimeline.

See PIPELINE-api-context-builder.md Section 3 for full design rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import structlog
from git import Repo, InvalidGitRepositoryError
from github import Github, GithubException

from companybrain.config import settings
from companybrain.models.entities import Artifact, CommitEvent, CommitCluster, RepoType

log = structlog.get_logger(__name__)


# ── Search patterns per repo type ────────────────────────────────────────────

BACKEND_PATTERNS = [
    # Route definitions (Express, Spring, FastAPI, Flask, gin)
    r'["\']({endpoint})["\']',
    r'@(Get|Post|Put|Delete|Patch)\(["\']({path})["\']',
    r'router\.(get|post|put|delete|patch)\(["\']({path})["\']',
    r'app\.(get|post|put|delete|patch)\(["\']({path})["\']',
    r'@RequestMapping.*["\']({path})["\']',
]

FRONTEND_PATTERNS = [
    # HTTP client calls
    r'axios\.(get|post|put|delete|patch)\(["\']({path})["\']',
    r'fetch\(["\']({path})["\']',
    r'api\.(get|post)\(["\']({path})["\']',
    r'["\']({path})["\']',          # Bare string reference
    # Generated API client methods (heuristic: endpoint path as constant)
    r'ENDPOINT.*=.*["\']({path})["\']',
]

SHARED_PATTERNS = [
    # Type names inferred from endpoint path segment
    r'interface\s+({TypeName})',
    r'type\s+({TypeName})\s*=',
    r'class\s+({TypeName})',
]


@dataclass
class CollectorConfig:
    endpoint_path: str          # e.g. "/payments/charge"
    http_method: str            # e.g. "POST"
    branch: str = "main"
    max_commits: int = 200      # Cap per repo to avoid runaway
    cluster_window_hours: int = 24


class GitCollector:
    """
    Collects git history for a given API endpoint across multiple repositories.

    Usage:
        collector = GitCollector(config, repos=[
            {"path": "/path/to/backend", "type": "backend", "github_url": "..."},
            {"path": "/path/to/frontend", "type": "frontend", "github_url": "..."},
        ])
        timeline = await collector.collect()
    """

    def __init__(self, config: CollectorConfig, repos: list[dict]):
        self.config = config
        self.repos = repos
        self._gh = Github(settings.github_token) if settings.github_token else None

    async def collect(self) -> list[CommitCluster]:
        """
        Main entry point. Returns an ordered list of CommitClusters.
        Each cluster groups related commits from multiple repos.
        """
        all_events: list[CommitEvent] = []

        for repo_info in self.repos:
            try:
                events = self._collect_from_repo(repo_info)
                all_events.extend(events)
                log.info(
                    "Collected commits",
                    repo=repo_info["path"],
                    count=len(events),
                    endpoint=self.config.endpoint_path,
                )
            except InvalidGitRepositoryError:
                log.warning("Not a git repository", path=repo_info["path"])
            except Exception as e:
                log.error("Failed to collect from repo", repo=repo_info["path"], error=str(e))

        # Enrich with GitHub PR/ticket data
        all_events = await self._enrich_with_github(all_events)

        # Merge into clusters
        clusters = self._cluster_by_time_and_pr(all_events)

        log.info(
            "Collection complete",
            endpoint=self.config.endpoint_path,
            total_events=len(all_events),
            clusters=len(clusters),
        )
        return clusters

    @staticmethod
    def _resolve_branch(repo, requested: str) -> str:
        """
        Return the best available branch name.
        If the requested branch doesn't exist, falls back to the active branch
        or the first of [main, master, develop, HEAD].
        """
        try:
            # Check the requested branch exists
            repo.commit(requested)
            return requested
        except Exception:
            pass

        # Try active branch first (most likely to be right)
        try:
            return repo.active_branch.name
        except TypeError:
            pass  # detached HEAD

        for fallback in ["main", "master", "develop", "HEAD"]:
            try:
                repo.commit(fallback)
                log.info(
                    "Branch not found — using fallback",
                    requested=requested,
                    fallback=fallback,
                    repo=str(repo.working_dir),
                )
                return fallback
            except Exception:
                continue

        return requested  # give up and let git fail with a clear error

    def _collect_from_repo(self, repo_info: dict) -> list[CommitEvent]:
        """Find relevant files and walk their git history."""
        repo_path = Path(repo_info["path"])
        repo_type = RepoType(repo_info["type"])
        repo = Repo(repo_path)

        # Per-repo branch takes priority over the global CollectorConfig branch.
        # If neither exists in the repo, _resolve_branch auto-detects.
        requested_branch = repo_info.get("branch") or self.config.branch
        branch = self._resolve_branch(repo, requested_branch)

        relevant_files = self._find_relevant_files(repo_path, repo_type)
        if not relevant_files:
            log.info("No relevant files found", repo=str(repo_path), endpoint=self.config.endpoint_path)
            return []

        events: list[CommitEvent] = []
        seen_hashes: set[str] = set()

        for file_path in relevant_files:
            for commit in repo.iter_commits(
                branch,
                paths=str(file_path.relative_to(repo_path)),
                max_count=self.config.max_commits,
            ):
                if commit.hexsha in seen_hashes:
                    continue
                seen_hashes.add(commit.hexsha)

                diff = self._get_file_diff(repo, commit, file_path, repo_path)

                events.append(CommitEvent(
                    commit_hash=commit.hexsha,
                    timestamp=datetime.fromtimestamp(commit.committed_date),
                    author=commit.author.email,
                    message=commit.message.strip(),
                    repo=str(repo_path.name),
                    repo_type=repo_type,
                    file_path=str(file_path.relative_to(repo_path)),
                    github_repo_url=repo_info.get("github_url"),
                    diff=diff,
                ))

        return sorted(events, key=lambda e: e.timestamp)

    # Path prefixes that are too generic to be useful as search terms
    _GENERIC_SEGMENTS = frozenset({
        "api", "v1", "v2", "v3", "v4", "v5",
        "rest", "service", "services",
        "public", "private", "internal",
    })

    def _get_search_terms(self) -> list[str]:
        """
        Derive a ranked list of search terms from the endpoint path.

        For a Spring Boot endpoint like:
            /api/v1/mcheck/niq/competitiveness/summary/competitors/payer
        where the class has @RequestMapping("/api/v1/mcheck/niq/competitiveness")
        and the method has @GetMapping("/summary/competitors/payer"),
        we need to try each trailing sub-path so both annotations are found.

        Returns search terms ordered from most-specific (full path) to least-specific.
        """
        endpoint = self.config.endpoint_path          # /api/v1/mcheck/niq/comp…/payer
        raw = endpoint.lstrip("/")                     # api/v1/mcheck/niq/comp…/payer
        parts = [p for p in raw.split("/") if p]      # ['api', 'v1', 'mcheck', …]

        # Filter out generic API-versioning segments to get the meaningful ones
        meaningful = [p for p in parts if p.lower() not in self._GENERIC_SEGMENTS]
        # e.g. ['mcheck', 'niq', 'competitiveness', 'summary', 'competitors', 'payer']

        terms: list[str] = []

        # 1. Full original path — catches exact single-annotation matches
        terms.append(endpoint)

        # 2. All trailing sub-paths of the meaningful segments (with and without leading /)
        #    This covers Spring Boot's split @RequestMapping + @GetMapping pattern:
        #    - /competitiveness/summary/competitors/payer  → combined controller+method path
        #    - /summary/competitors/payer                 → method-level @GetMapping
        #    - /competitors/payer                         → sub-path variant
        #    - /payer                                     → bare leaf segment
        for i in range(len(meaningful)):
            sub = "/".join(meaningful[i:])
            with_slash = "/" + sub
            if with_slash not in terms:
                terms.append(with_slash)
            if sub not in terms:
                terms.append(sub)

        # 3. Individual meaningful segments that are long enough to be distinctive
        #    (short segments like "niq" are too likely to produce false positives)
        for seg in meaningful:
            if len(seg) > 4 and seg not in terms:
                terms.append(seg)

        log.debug(
            "Search terms derived",
            endpoint=endpoint,
            term_count=len(terms),
            terms=terms[:6],  # log first 6 to keep it readable
        )
        return terms

    def _find_relevant_files(self, repo_path: Path, repo_type: RepoType) -> list[Path]:
        """
        Search the repo for files containing references to the target endpoint.

        Uses both the full endpoint path AND trailing sub-paths so that frameworks
        like Spring Boot (which split routes across class + method annotations) are
        handled correctly.

        Returns file paths sorted by total pattern hit count (most relevant first).
        """
        base_patterns = BACKEND_PATTERNS if repo_type == RepoType.BACKEND else FRONTEND_PATTERNS
        search_terms = self._get_search_terms()

        compiled: list[re.Pattern] = []
        for term in search_terms:
            type_name = "".join(
                p.capitalize()
                for p in term.strip("/").replace("/", "_").split("_")
                if p
            )
            for pat in base_patterns:
                try:
                    concrete = pat.format(
                        endpoint=re.escape(term),
                        path=re.escape(term),
                        TypeName=type_name,
                    )
                    compiled.append(re.compile(concrete, re.IGNORECASE))
                except re.error:
                    continue

        extensions = {".ts", ".tsx", ".js", ".jsx", ".java", ".py", ".go"}
        matches: dict[Path, int] = {}

        for ext in extensions:
            for file_path in repo_path.rglob(f"*{ext}"):
                # Skip dependency/build/generated directories
                if any(skip in file_path.parts for skip in [
                    "node_modules", ".git", "dist", "build", "generated",
                    ".venv", "venv", "env", ".env",
                    "target",           # Maven/Gradle build output
                    "__pycache__",
                    "site-packages",    # pip installs inside repo venvs
                    ".tox", ".mypy_cache", ".ruff_cache",
                    "vendor",           # Go / Ruby vendored deps
                ]):
                    continue

                try:
                    content = file_path.read_text(errors="ignore")
                    hit_count = sum(len(p.findall(content)) for p in compiled)
                    if hit_count > 0:
                        matches[file_path] = hit_count
                except OSError:
                    continue

        found = sorted(matches, key=matches.get, reverse=True)[:20]  # top 20 files

        if not found:
            log.info(
                "No relevant files found",
                repo=str(repo_path),
                endpoint=self.config.endpoint_path,
                terms_tried=len(search_terms),
                search_terms=search_terms[:5],
            )
        else:
            log.info(
                "Relevant files found",
                repo=str(repo_path),
                endpoint=self.config.endpoint_path,
                file_count=len(found),
                top_file=str(found[0].relative_to(repo_path)) if found else None,
            )

        return found

    def _get_file_diff(self, repo: Repo, commit, file_path: Path, repo_path: Path) -> Optional[str]:
        """Extract the unified diff for a specific file at a specific commit."""
        try:
            rel_path = str(file_path.relative_to(repo_path))
            if not commit.parents:
                # First commit — show full file content as addition
                blob = commit.tree[rel_path]
                lines = blob.data_stream.read().decode(errors="ignore").splitlines()
                return "\n".join(f"+{line}" for line in lines[:100])  # cap at 100 lines

            parent = commit.parents[0]
            diffs = parent.diff(commit, paths=rel_path, create_patch=True)
            for diff in diffs:
                if diff.b_path == rel_path or diff.a_path == rel_path:
                    patch = diff.diff.decode(errors="ignore")
                    # Cap at 200 lines to avoid huge diffs overwhelming the LLM
                    lines = patch.splitlines()
                    if len(lines) > 200:
                        return "\n".join(lines[:200]) + "\n... (truncated)"
                    return patch
        except (KeyError, UnicodeDecodeError):
            pass
        return None

    async def _enrich_with_github(self, events: list[CommitEvent]) -> list[CommitEvent]:
        """
        Fetch PR titles, bodies, and linked ticket IDs from GitHub for each commit.
        Falls back gracefully if GitHub token is not configured.
        """
        if not self._gh:
            return events

        # Group by github_repo_url to batch GitHub API calls
        by_repo: dict[str, list[CommitEvent]] = {}
        for event in events:
            if event.github_repo_url:
                by_repo.setdefault(event.github_repo_url, []).append(event)

        for repo_url, repo_events in by_repo.items():
            try:
                repo_name = self._extract_repo_name(repo_url)
                gh_repo = self._gh.get_repo(repo_name)

                for event in repo_events:
                    pulls = gh_repo.get_commit(event.commit_hash).get_pulls()
                    pr = next(iter(pulls), None)
                    if pr:
                        event.pr_title = pr.title
                        event.pr_body = pr.body or ""
                        event.pr_url = pr.html_url
                        event.linked_tickets = self._extract_ticket_ids(pr.title + " " + (pr.body or ""))
            except GithubException as e:
                log.warning("GitHub API error", repo=repo_url, error=str(e))

        return events

    def _cluster_by_time_and_pr(self, events: list[CommitEvent]) -> list[CommitCluster]:
        """
        Group events from multiple repos into clusters representing
        logically related changes (same PR, same ticket, or within 24 hours).
        """
        if not events:
            return []

        clusters: list[CommitCluster] = []
        window = timedelta(hours=self.config.cluster_window_hours)
        current_cluster_commits: list[CommitEvent] = [events[0]]

        for event in events[1:]:
            last = current_cluster_commits[-1]
            same_pr = (event.pr_url and event.pr_url == last.pr_url)
            same_ticket = bool(
                event.linked_tickets and last.linked_tickets and
                set(event.linked_tickets) & set(last.linked_tickets)
            )
            time_close = abs(event.timestamp - last.timestamp) <= window

            if same_pr or same_ticket or time_close:
                current_cluster_commits.append(event)
            else:
                clusters.append(self._make_cluster(current_cluster_commits))
                current_cluster_commits = [event]

        clusters.append(self._make_cluster(current_cluster_commits))
        return clusters

    def _make_cluster(self, commits: list[CommitEvent]) -> CommitCluster:
        reason = "time_proximity"
        if len({c.pr_url for c in commits if c.pr_url}) == 1:
            reason = "same_pr"
        elif commits[0].linked_tickets:
            reason = "same_ticket"

        return CommitCluster(
            cluster_id=commits[0].commit_hash,
            approximate_date=commits[0].timestamp,
            commits=commits,
            cluster_reason=reason,
        )

    @staticmethod
    def _extract_repo_name(url: str) -> str:
        """Extract 'owner/repo' from a GitHub URL."""
        match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        return match.group(1) if match else url

    @staticmethod
    def _extract_ticket_ids(text: str) -> list[str]:
        """Extract Jira/Linear/GitHub issue references from PR text."""
        patterns = [
            r"\b([A-Z]+-\d+)\b",          # Jira: ENG-123
            r"\b(LIN-\d+)\b",             # Linear
            r"#(\d+)",                     # GitHub issue
        ]
        ids = []
        for pat in patterns:
            ids.extend(re.findall(pat, text))
        return list(set(ids))

    # ── ADR-005: Collector protocol implementation ────────────────────────────

    #: This collector emits source_file, commit, and pr artifacts.
    kind = "source_file"

    async def collect_as_artifacts(self, workspace_id=None, since=None) -> list[Artifact]:
        """
        Re-use the existing collect() result and project it into Artifact objects.

        Emits three kinds:
          source_file — one per unique (repo, file_path) found in the commit events
          commit      — one per unique commit (hash + message + diff)
          pr          — one per unique PR (pr_url, title + body)

        The existing collect() method is called first so GitHub enrichment runs.
        We then walk the resulting clusters to build artifacts.

        This method satisfies the Collector protocol from collectors/base.py.
        """
        clusters = await self.collect()

        seen_files:   set[str] = set()
        seen_commits: set[str] = set()
        seen_prs:     set[str] = set()
        artifacts: list[Artifact] = []

        for cluster in clusters:
            for event in cluster.commits:
                # ── commit artifact ──────────────────────────────────────────
                commit_ext_id = f"{event.repo}::{event.commit_hash}"
                if commit_ext_id not in seen_commits:
                    seen_commits.add(commit_ext_id)
                    content = f"{event.message}\n\n{event.diff or ''}"
                    artifacts.append(Artifact(
                        kind="commit",
                        external_id=commit_ext_id,
                        content=content[:8000],   # cap at 8k chars
                        source_uri=None,
                        author=event.author,
                        metadata={
                            "repo":      event.repo,
                            "hash":      event.commit_hash,
                            "timestamp": event.timestamp.isoformat(),
                            "file_path": event.file_path,
                        },
                    ))

                # ── source_file artifact (one per unique repo+file) ──────────
                file_ext_id = f"{event.repo}/{event.file_path}"
                if file_ext_id not in seen_files:
                    seen_files.add(file_ext_id)
                    # Try to read the current file content
                    file_content = self._read_file_content(event)
                    artifacts.append(Artifact(
                        kind="source_file",
                        external_id=file_ext_id,
                        content=file_content,
                        source_uri=event.github_repo_url,
                        author=None,
                        metadata={
                            "repo":      event.repo,
                            "file_path": event.file_path,
                            "repo_type": event.repo_type.value,
                        },
                    ))

                # ── pr artifact ──────────────────────────────────────────────
                if event.pr_url and event.pr_url not in seen_prs:
                    seen_prs.add(event.pr_url)
                    pr_content = f"{event.pr_title or ''}\n\n{event.pr_body or ''}"
                    artifacts.append(Artifact(
                        kind="pr",
                        external_id=f"{event.repo}::{event.pr_url}",
                        content=pr_content[:4000],
                        source_uri=event.pr_url,
                        author=event.author,
                        metadata={
                            "repo":     event.repo,
                            "pr_title": event.pr_title,
                        },
                    ))

        log.info(
            "Artifacts collected",
            source_files=len(seen_files),
            commits=len(seen_commits),
            prs=len(seen_prs),
            endpoint=self.config.endpoint_path,
        )
        return artifacts

    def _read_file_content(self, event: CommitEvent) -> str:
        """
        Attempt to read the current (HEAD) content of a file from the local repo.
        Falls back to the diff snippet if the file is not readable.
        """
        for repo_info in self.repos:
            if Path(repo_info["path"]).name == event.repo:
                full_path = Path(repo_info["path"]) / event.file_path
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    return content[:16000]  # cap at 16k chars per source file
                except OSError:
                    pass
        # Fallback: use diff snippet
        return event.diff or ""
