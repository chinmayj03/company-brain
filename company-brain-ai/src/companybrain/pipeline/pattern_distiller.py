"""
PatternDistiller — active learning loop for extraction rules.

After the LLM adjudicates edges (Pass 2 relationship extraction),
the distiller records which edges were extracted with high confidence.
Over time, these patterns are distilled into regex/semgrep rules
that fire in tier 2 (pattern matching) before the LLM is called.

This implements the directive's active learning loop:
  Round 1: rules + heuristics → candidate edges with confidence
  Round 2: LLM adjudicates low-confidence cases
  Round 3: outcomes feed back as new rules / regex / semgrep patterns

Track: LLM call rate over time. It should decrease as patterns accumulate.

Storage: patterns are stored as JSON in Redis under key `cb:patterns:{workspace_id}`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Optional
import structlog

log = structlog.get_logger(__name__)


@dataclass
class ExtractedPattern:
    """A distilled pattern from high-confidence LLM-adjudicated edges."""
    pattern_type: str       # "CALLS" | "READS_COLUMN" | "WRITES_COLUMN" etc.
    from_name_pattern: str  # regex matching from_entity name
    to_name_pattern: str    # regex matching to_entity name
    confidence: float       # confidence of the distilled pattern
    evidence_count: int     # how many times this was observed
    last_seen: str          # ISO timestamp


class PatternDistiller:
    """
    Distills high-confidence LLM edges into reusable patterns.
    Patterns are applied before the LLM in future runs (tier 2).
    """

    PATTERN_KEY = "cb:patterns:{workspace_id}"
    MIN_CONFIDENCE = 0.9    # Only distill edges this confident or higher
    MIN_EVIDENCE   = 3      # Need at least N observations before distilling

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis_url = redis_url
        self._r = None

    def _redis(self):
        if self._r is None:
            try:
                import redis
                self._r = redis.from_url(self._redis_url, decode_responses=True)
            except Exception:
                pass
        return self._r

    async def record_edges(self, workspace_id: str, relationships: list) -> None:
        """
        Record high-confidence edges for pattern distillation.
        Called after relationship extraction completes.
        """
        r = self._redis()
        if r is None:
            return

        key = self.PATTERN_KEY.format(workspace_id=workspace_id)
        for rel in relationships:
            if getattr(rel, "confidence", 0) < self.MIN_CONFIDENCE:
                continue
            pattern_key = f"{rel.edge_type}:{rel.from_entity}:{rel.to_entity}"
            try:
                existing_raw = r.hget(key, pattern_key)
                if existing_raw:
                    existing = json.loads(existing_raw)
                    existing["evidence_count"] = existing.get("evidence_count", 1) + 1
                    existing["last_seen"] = _now_iso()
                    r.hset(key, pattern_key, json.dumps(existing))
                else:
                    r.hset(key, pattern_key, json.dumps({
                        "pattern_type": rel.edge_type,
                        "from_name_pattern": re.escape(rel.from_entity),
                        "to_name_pattern": re.escape(rel.to_entity),
                        "confidence": rel.confidence,
                        "evidence_count": 1,
                        "last_seen": _now_iso(),
                    }))
            except Exception as e:
                log.debug("Pattern record failed", error=str(e))

    def get_patterns(self, workspace_id: str) -> list[ExtractedPattern]:
        """Return distilled patterns with sufficient evidence."""
        r = self._redis()
        if r is None:
            return []
        key = self.PATTERN_KEY.format(workspace_id=workspace_id)
        try:
            raw_patterns = r.hgetall(key)
            patterns = []
            for raw in raw_patterns.values():
                p = json.loads(raw)
                if p.get("evidence_count", 0) >= self.MIN_EVIDENCE:
                    patterns.append(ExtractedPattern(**p))
            return patterns
        except Exception:
            return []

    def apply_patterns(
        self,
        workspace_id: str,
        entities: list,
    ) -> list:
        """
        Apply distilled patterns to entity list, returning pre-computed
        relationship candidates (avoiding LLM call for known patterns).

        Returns list of relationship-like dicts for high-confidence pattern matches.
        """
        patterns = self.get_patterns(workspace_id)
        if not patterns:
            return []

        entity_names = {e.name for e in entities}
        pre_computed = []

        for pat in patterns:
            from_regex = re.compile(pat.from_name_pattern, re.IGNORECASE)
            to_regex   = re.compile(pat.to_name_pattern,   re.IGNORECASE)

            matching_froms = [n for n in entity_names if from_regex.search(n)]
            matching_tos   = [n for n in entity_names if to_regex.search(n)]

            for from_e in matching_froms:
                for to_e in matching_tos:
                    if from_e != to_e:
                        pre_computed.append({
                            "from_entity": from_e,
                            "to_entity":   to_e,
                            "edge_type":   pat.pattern_type,
                            "confidence":  pat.confidence * 0.95,  # slight discount
                            "evidence":    f"distilled_pattern:{pat.evidence_count}_observations",
                            "source":      "pattern_distiller",
                        })

        if pre_computed:
            log.info(
                "Pattern distiller: pre-computed edges",
                count=len(pre_computed),
                workspace=workspace_id,
                patterns_applied=len(patterns),
            )
        return pre_computed


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
