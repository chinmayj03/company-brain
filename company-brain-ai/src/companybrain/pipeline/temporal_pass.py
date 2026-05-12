"""
ADR-0059 Pass T1 — Temporal Ownership Pass.

For each Method / Class / ApiEndpoint entity, read the file's git blame and
aggregate it into a ``TemporalOwnership`` attached to ``entity.temporal``.

The pass is deterministic — no LLM calls. It runs after Stage 3 (Business
Context synthesis) and BEFORE Stage 5 (graph storage), so downstream consumers
can include ownership / churn data in the persisted node metadata.

Behaviour guarantees:
  - empty input              → empty result, no-op (does not touch the entity list).
  - missing repo root        → entity is returned unchanged (``temporal`` stays None).
  - blame failure for a file → entity unchanged, warning logged once per file.
  - never raises             → orchestrator wraps anyway, but defensive code here.

The pass is intentionally cheap: per-file blames are cached by the aggregator
so re-running for sibling methods inside the same file costs O(1) after the
first call.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import structlog

from companybrain.models.entities import ExtractedEntity, TemporalOwnership
# We import the aggregator MODULE (not the bound functions) so tests can
# monkey-patch ``git_blame_aggregator.blame_file`` and have the override
# take effect inside the pass.
from companybrain.pipeline import git_blame_aggregator as _blame

log = structlog.get_logger(__name__)

# Entity types eligible for temporal annotation. Keeping the list explicit
# avoids accidentally blaming derived ``ExtractedEntity`` rows that don't have
# a real file location (Pattern, SharedInvariant, DomainEntity from ADR-0055).
_TEMPORAL_ELIGIBLE_TYPES = frozenset({
    "Function", "Method", "Class", "InterfaceClass", "DTO",
    "ApiEndpoint", "Endpoint", "Controller", "Service", "Repository",
})

# Threshold for counting a contributor as a "co-author" in the bus-factor
# calculation. Mirrors the heuristic in ADR-0059 §Pass T1.
_BUS_FACTOR_LINE_SHARE = 0.10


RepoResolver = Callable[[str], Optional[Path]]
"""``RepoResolver(repo_name) -> Path | None``. Returns the on-disk repo root
that contains the entity, or ``None`` when the repo wasn't cloned locally."""


@dataclass
class TemporalPassStats:
    entities_seen:     int = 0
    entities_eligible: int = 0
    entities_blamed:   int = 0
    entities_skipped:  int = 0
    files_seen:        set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "entities_seen":     self.entities_seen,
            "entities_eligible": self.entities_eligible,
            "entities_blamed":   self.entities_blamed,
            "entities_skipped":  self.entities_skipped,
            "unique_files":      len(self.files_seen),
        }


async def run_temporal_pass(
    entities: Iterable[ExtractedEntity],
    *,
    repo_resolver: RepoResolver,
    now: Optional[datetime] = None,
) -> tuple[list[ExtractedEntity], TemporalPassStats]:
    """Annotate every eligible entity with ``TemporalOwnership`` in place.

    Returns ``(entities_list, stats)``. The entity objects are mutated — the
    return is the same list to give callers a single binding to thread
    through subsequent stages.
    """
    entities_list = list(entities)
    stats = TemporalPassStats()
    if not entities_list:
        return entities_list, stats

    now = now or datetime.now(tz=timezone.utc)
    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)

    # Cache resolved repo roots so we don't ask the resolver once per entity.
    repo_root_cache: dict[str, Optional[Path]] = {}

    for entity in entities_list:
        stats.entities_seen += 1
        if entity.entity_type not in _TEMPORAL_ELIGIBLE_TYPES:
            continue
        if not entity.file or not entity.repo:
            stats.entities_skipped += 1
            continue

        stats.entities_eligible += 1

        if entity.repo not in repo_root_cache:
            try:
                repo_root_cache[entity.repo] = repo_resolver(entity.repo)
            except Exception as exc:
                log.debug("temporal_pass.repo_resolver failed",
                          repo=entity.repo, error=str(exc))
                repo_root_cache[entity.repo] = None
        repo_root = repo_root_cache[entity.repo]
        if repo_root is None:
            stats.entities_skipped += 1
            continue

        rel_path = _relative_path(entity.file, repo_root)
        stats.files_seen.add(f"{entity.repo}/{rel_path}")

        lines = _blame.blame_file(repo_root, rel_path)
        commits = _blame.file_commits(repo_root, rel_path)
        if not lines and not commits:
            stats.entities_skipped += 1
            continue

        entity.temporal = _aggregate(
            lines=lines, commits=commits,
            cutoff_30=cutoff_30, cutoff_90=cutoff_90, now=now,
        )
        stats.entities_blamed += 1

    log.info("temporal_pass.run_temporal_pass complete", **stats.as_dict())
    return entities_list, stats


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _aggregate(
    *,
    lines: list,
    commits: list,
    cutoff_30: datetime,
    cutoff_90: datetime,
    now: datetime,
) -> TemporalOwnership:
    """Turn a list of ``BlameLine`` + ``CommitTouch`` rows into a
    ``TemporalOwnership``. Pure function — no I/O."""
    out = TemporalOwnership()

    if lines:
        author_counter: Counter[str] = Counter()
        for ln in lines:
            key = ln.author or ln.author_mail
            if key:
                author_counter[key] += 1
        if author_counter:
            ranked = author_counter.most_common()
            out.primary_author = ranked[0][0]
            out.co_authors = [(a, c) for a, c in ranked]
            total = sum(c for _, c in ranked)
            if total > 0:
                out.bus_factor = sum(
                    1 for _, c in ranked if c / total >= _BUS_FACTOR_LINE_SHARE
                )

    if commits:
        newest = commits[0]
        oldest = commits[-1]
        out.last_touched_at = newest.timestamp
        out.last_touched_by = newest.author or newest.author_mail
        age = (now - oldest.timestamp).days
        out.age_days = max(age, 0)
        out.churn_30d = sum(1 for c in commits if c.timestamp >= cutoff_30)
        out.churn_90d = sum(1 for c in commits if c.timestamp >= cutoff_90)

    # Fall back to blame-derived last_touched_at when we have no commit log
    # (subprocess git log can fail on shallow clones in CI environments).
    if out.last_touched_at is None and lines:
        latest = max((ln.commit_time for ln in lines), default=None)
        if latest is not None:
            out.last_touched_at = latest
            # blame timestamps don't carry the author of the latest commit
            # reliably (the latest line may not be the latest commit), so
            # leave last_touched_by empty rather than guess.
    return out


def _relative_path(file_field: str, repo_root: Path) -> str:
    """Best-effort relative path from the entity's ``file`` field.

    The pipeline stores file paths a few different ways depending on which
    extractor produced the entity (relative to repo root, absolute, or
    repo-prefixed). We normalise here so the blame aggregator only ever sees
    a path that's relative to ``repo_root``.
    """
    if not file_field:
        return ""
    p = Path(file_field)
    # Already relative — trust it.
    if not p.is_absolute():
        return file_field
    try:
        return str(p.relative_to(repo_root))
    except ValueError:
        # Different roots — fall back to the basename so the aggregator at
        # least has a chance via -- pathspec.
        return p.name
