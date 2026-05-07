"""Shared types for the smart-zone assembler."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    READ      = "READ"
    WRITE     = "WRITE"
    DEBUG     = "DEBUG"
    AUDIT     = "AUDIT"
    ONBOARD   = "ONBOARD"


@dataclass
class TokenBudget:
    total: int = 6000
    t0_summaries: int = 1200
    t1_detail: int = 3600
    business_context: int = 600
    blast_radius: int = 600

    @classmethod
    def conservative(cls):
        return cls(total=4000, t0_summaries=800, t1_detail=2400,
                   business_context=400, blast_radius=400)

    @classmethod
    def deep(cls):
        return cls(total=12000, t0_summaries=1500, t1_detail=7500,
                   business_context=2000, blast_radius=1000)


@dataclass
class SmartZonePayload:
    task: str
    task_type: TaskType
    t0: list[dict] = field(default_factory=list)         # [{urn, t0_token}]
    t1: list[dict] = field(default_factory=list)         # [{urn, t1_token, ...}]
    t2: list[dict] = field(default_factory=list)         # [{urn, full_entity_json}]
    business_context: list[dict] = field(default_factory=list)
    blast_radius: dict = field(default_factory=dict)     # {urn: [neighbour_urns]}
    tokens_used: int = 0
    tokens_budget: int = 6000
    rendered: str = ""
