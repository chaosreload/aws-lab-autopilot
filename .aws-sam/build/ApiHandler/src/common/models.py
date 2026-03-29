"""Shared data models for the Hands-on Workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class TaskState(StrEnum):
    QUEUED = "queued"
    RESEARCHING = "researching"
    RESEARCH_DONE = "research_done"
    EXECUTING_EXPLORE = "executing_explore"
    EXECUTING_VERIFY = "executing_verify"
    EXECUTE_DONE = "execute_done"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    CANCELLED = "cancelled"


class ReworkType(StrEnum):
    REDESIGN = "redesign"
    RETEST_ALL = "retest_all"
    RETEST_SPECIFIC = "retest_specific"


class Complexity(StrEnum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


class Verdict(StrEnum):
    GO = "go"
    SKIP = "skip"


@dataclass
class TestItem:
    id: str
    name: str
    priority: str  # P0 | P1 | P2


@dataclass
class ResearchResult:
    verdict: Verdict
    complexity: Complexity
    estimated_cost: float
    notes_path: str
    test_matrix: list[TestItem] = field(default_factory=list)
    iam_policy: dict = field(default_factory=dict)
    services: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    test_id: str
    name: str
    status: str  # pass | fail | skip
    duration: Optional[str] = None


@dataclass
class ExecuteResult:
    test_results: dict[str, str]
    final_iam_policy: dict
    permissions_added: list[str] = field(default_factory=list)
    pitfalls: list[dict] = field(default_factory=list)
    performance_data: dict = field(default_factory=dict)
    evidence_path: str = ""
    cost_actual: float = 0.0


@dataclass
class PublishResult:
    calibration: dict = field(default_factory=dict)
    article_path: str = ""
    published_url: str = ""
    quality_passed: bool = False
    rework_needed: Optional[bool] = None
    rework_type: Optional[ReworkType] = None
    reason: Optional[str] = None
