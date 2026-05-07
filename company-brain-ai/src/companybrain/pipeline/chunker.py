"""
Chunker — splits CommitClusters into LLM-sized chunks.

Strategy:
  - Recent 6 months: verbatim diffs (higher token budget)
  - 6-18 months: summarised diffs (first 500 chars of diff)
  - >18 months: commit message + PR title only

Annotated clusters always get verbatim treatment regardless of age.
Target: ~60,000 tokens per chunk (leaves room for system prompt + output).
Each token ≈ 4 chars (rough estimate, avoids tiktoken dependency for now).
"""
from __future__ import annotations
from datetime import datetime, timedelta
from companybrain.models.entities import CommitCluster

MAX_CHARS_PER_CHUNK = 200_000  # ~50k tokens at 4 chars/token


class Chunker:
    def chunk_clusters(self, clusters: list[CommitCluster], api_snapshot: dict) -> list[dict]:
        """
        Returns a list of chunk dicts, each containing:
          - commits: list of serialised commit dicts
          - api_snapshot: the current API state
        """
        now = datetime.now()
        six_months_ago = now - timedelta(days=180)
        eighteen_months_ago = now - timedelta(days=540)

        chunks = []
        current_chunk_commits = []
        current_chunk_size = 0
        snapshot_chars = len(str(api_snapshot))

        for cluster in clusters:
            for commit in cluster.commits:
                if commit.timestamp > six_months_ago:
                    diff_text = commit.diff or ""
                elif commit.timestamp > eighteen_months_ago:
                    diff_text = (commit.diff or "")[:500]
                else:
                    diff_text = ""

                serialised = {
                    "commit_hash": commit.commit_hash,
                    "timestamp": commit.timestamp.isoformat(),
                    "message": commit.message,
                    "repo": commit.repo,
                    "pr_title": commit.pr_title,
                    "pr_body": (commit.pr_body or "")[:800],
                    "diff": diff_text,
                    "has_annotation": False,  # enriched later
                }
                size = len(str(serialised))
                if current_chunk_commits and (current_chunk_size + size + snapshot_chars > MAX_CHARS_PER_CHUNK):
                    chunks.append({"commits": current_chunk_commits, "api_snapshot": api_snapshot})
                    current_chunk_commits = []
                    current_chunk_size = 0
                current_chunk_commits.append(serialised)
                current_chunk_size += size

        if current_chunk_commits:
            chunks.append({"commits": current_chunk_commits, "api_snapshot": api_snapshot})

        return chunks if chunks else [{"commits": [], "api_snapshot": api_snapshot}]
