"""
companybrain.query — ADR-0061 P1: iterative exploration + self-verification.

Public surface (importable without fastapi installed):
  ExplorationLoop   — wraps a query in an iterative retrieve-then-answer cycle
  SelfVerifier      — fast LLM check: every claim must be backed by a citation
  FollowupGenerator — surfaces disambiguation questions when confidence is low

Callers needing orchestrate_query import it directly from
  companybrain.query.orchestrator  (avoids pulling fastapi at package import time)
"""

from companybrain.query.exploration_loop import AnswerResult, ExplorationLoop
from companybrain.query.followup_generator import FollowupGenerator
from companybrain.query.self_verifier import SelfVerifier, VerifierResult

__all__ = [
    "AnswerResult",
    "ExplorationLoop",
    "FollowupGenerator",
    "SelfVerifier",
    "VerifierResult",
]
