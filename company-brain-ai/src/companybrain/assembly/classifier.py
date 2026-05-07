"""Task-type classifier — regex first, LLM fallback for ambiguity."""
from __future__ import annotations
import re
from companybrain.assembly.types import TaskType


# More-specific patterns are checked first so they shadow broader ones.
# ONBOARD before READ (both match "explain"); AUDIT before WRITE (both may match task verbs).
_PATTERNS = [
    (TaskType.ONBOARD, re.compile(r"\b(explain the whole|overview|how does the (system|codebase)|onboard|tour)\b", re.I)),
    (TaskType.AUDIT,   re.compile(r"\b(review|check|is .* safe|what uses|who calls|impact|blast radius)\b", re.I)),
    (TaskType.DEBUG,   re.compile(r"\b(error|bug|failing|why is|broken|crash|exception|stack trace)\b", re.I)),
    (TaskType.WRITE,   re.compile(r"\b(change|modify|add|refactor|implement|fix|update|extend)\b", re.I)),
    (TaskType.READ,    re.compile(r"\b(what does|explain|how does|describe|summari[sz]e|what is)\b", re.I)),
]


def classify(task: str) -> TaskType:
    """Return the highest-priority match. Default = READ."""
    for tt, pat in _PATTERNS:
        if pat.search(task):
            return tt
    return TaskType.READ


# Retrieval parameters per task type — see harness §6.3
TASK_PARAMS = {
    TaskType.READ:    {"t1_top_n": 5,  "t2_top_k": 2, "hops": 1, "direction": "both",       "mmr_lambda": 0.6},
    TaskType.WRITE:   {"t1_top_n": 8,  "t2_top_k": 4, "hops": 2, "direction": "upstream",   "mmr_lambda": 0.75},
    TaskType.DEBUG:   {"t1_top_n": 8,  "t2_top_k": 4, "hops": 2, "direction": "both",       "mmr_lambda": 0.5},
    TaskType.AUDIT:   {"t1_top_n": 10, "t2_top_k": 0, "hops": 3, "direction": "upstream",   "mmr_lambda": 0.7},
    TaskType.ONBOARD: {"t1_top_n": 12, "t2_top_k": 0, "hops": 0, "direction": "downstream", "mmr_lambda": 0.4},
}
