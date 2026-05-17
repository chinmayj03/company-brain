"""
Verbalizer — A1.4 Verbalized Confidence.

Converts a confidence scalar (0.0–1.0) plus raw signal values into a
human-readable label + rationale string.

Label thresholds:
  high   ≥ 0.80
  medium   0.55 – 0.79
  low    < 0.55
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from companybrain.confidence.signals import ConfidenceSignals

# Bucket boundaries — exported so tests and config can reference them.
HIGH_THRESHOLD: float = 0.80
MEDIUM_THRESHOLD: float = 0.55


@dataclass
class VerbalizedConfidence:
    """Output of Verbalizer.verbalize()."""
    label: str     # "high" | "medium" | "low"
    rationale: str # generated sentence referencing actual signal values


class Verbalizer:
    """Converts a scalar + signals into a verbal confidence object."""

    def verbalize(
        self, scalar: float, signals: "ConfidenceSignals"
    ) -> VerbalizedConfidence:
        label = self._scalar_to_label(scalar)
        rationale = self._build_rationale(scalar, label, signals)
        return VerbalizedConfidence(label=label, rationale=rationale)

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _scalar_to_label(scalar: float) -> str:
        if scalar >= HIGH_THRESHOLD:
            return "high"
        if scalar >= MEDIUM_THRESHOLD:
            return "medium"
        return "low"

    @staticmethod
    def _build_rationale(
        scalar: float,
        label: str,
        signals: "ConfidenceSignals",
    ) -> str:
        """
        Build a rationale sentence that references the actual signal values.

        The sentence is intentionally non-templated: each clause is only
        included when its signal value is meaningfully informative (i.e., not
        at the stub default).  This avoids confusing "entities last updated N
        days ago" text when N is just the stub value.
        """
        parts: list[str] = []

        # ── Entity coverage ───────────────────────────────────────────────
        n_entities = signals.entity_match_count
        if n_entities >= 5:
            parts.append(f"{n_entities} entities cited (strong coverage)")
        elif n_entities >= 2:
            parts.append(f"{n_entities} entities cited")
        elif n_entities == 1:
            parts.append("1 entity cited")
        else:
            parts.append("no entities cited")

        # ── Source diversity ──────────────────────────────────────────────
        sd = signals.source_diversity
        if sd >= 0.9:
            parts.append("context from many distinct files")
        elif sd >= 0.5:
            n_files = max(2, round(sd * 10))
            parts.append(f"context drawn from ~{n_files} files")
        else:
            parts.append("context concentrated in few files")

        # ── Retrieval quality ─────────────────────────────────────────────
        rs = signals.retrieval_score
        if rs >= 0.8:
            parts.append(f"strong retrieval score ({rs:.2f})")
        elif rs >= 0.5:
            parts.append(f"moderate retrieval score ({rs:.2f})")
        elif rs > 0.0:
            parts.append(f"weak retrieval score ({rs:.2f})")
        else:
            parts.append("no retrieval score available")

        # ── Verifier ─────────────────────────────────────────────────────
        va = signals.verifier_agreement
        if va >= 1.0:
            parts.append("verifier loop confirmed answer")
        elif va <= 0.0:
            parts.append("verifier loop found issues")
        else:
            parts.append("verifier loop not run")

        # ── Chain length ──────────────────────────────────────────────────
        cl = signals.chain_length
        if cl >= 3:
            parts.append(f"{cl}-hop call chain traced")
        elif cl == 1 or cl == 2:
            parts.append(f"{cl}-hop chain traced")
        # else: omit — zero hops is the common case for concept queries

        # ── Freshness (only mention when not stubbed at 0.5) ─────────────
        fs = signals.freshness_score
        if fs != 0.5:  # non-stub value
            if fs >= 0.9:
                parts.append("entities extracted recently")
            elif fs >= 0.5:
                age_days = round((1.0 - fs) * 90)
                parts.append(f"entities last updated ~{age_days} days ago")
            else:
                age_days = round((1.0 - fs) * 90)
                parts.append(f"entities may be stale (~{age_days} days old)")

        # ── Scalar summary ────────────────────────────────────────────────
        scalar_pct = round(scalar * 100)
        header = f"Overall confidence {label} ({scalar_pct}%): "

        return header + "; ".join(parts) + "."
