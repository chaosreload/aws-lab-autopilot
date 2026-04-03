"""Data models for aws-lab-autopilot pipeline.

Covers Research / Execute / Publish agent I/O plus shared enums.
Aligned with: openspec/specs/agent-io.md + phase2-gap-analysis.md (Gap 4).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    GO = "go"
    SKIP = "skip"


class TaskState(str, Enum):
    QUEUED = "queued"
    RESEARCHING = "researching"
    EXECUTING = "executing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    API_CALL = "api_call"
    INFRASTRUCTURE = "infrastructure"
    MIXED = "mixed"


class ReworkType(str, Enum):
    REDESIGN = "redesign"
    RETEST_ALL = "retest_all"
    RETEST_SPECIFIC = "retest_specific"


# ---------------------------------------------------------------------------
# Research Agent models
# ---------------------------------------------------------------------------

class TestItem(BaseModel):
    id: str
    name: str
    description: str = ""
    priority: str = "P0"
    api_hints: dict = Field(default_factory=dict)
    # Gap 4 fields
    type: str = "api_call"  # api_call | infrastructure | data_validation | cdc
    prerequisites: list[str] = Field(default_factory=list)
    infrastructure_hints: dict = Field(default_factory=dict)
    validation_criteria: dict = Field(default_factory=dict)


class ResearchResult(BaseModel):
    verdict: Verdict = Verdict.SKIP
    notes_path: str = ""
    test_matrix: list[TestItem] = Field(default_factory=list)
    iam_policy: dict = Field(default_factory=dict)
    services: list[str] = Field(default_factory=list)
    # Gap 4 / Gap 6: task type classification
    task_type: TaskType = TaskType.API_CALL
    task_type_reason: str = ""
    estimated_execution_minutes: int = 0


# ---------------------------------------------------------------------------
# Execute Agent models
# ---------------------------------------------------------------------------

class Pitfall(BaseModel):
    desc: str
    verified: bool = False


class ExecuteResult(BaseModel):
    test_results: dict[str, str] = Field(default_factory=dict)
    final_iam_policy: dict = Field(default_factory=dict)
    permissions_added: list[str] = Field(default_factory=list)
    pitfalls: list[Pitfall] = Field(default_factory=list)
    cost_actual: float = 0.0


# ---------------------------------------------------------------------------
# Publish Agent models
# ---------------------------------------------------------------------------

class Calibration(BaseModel):
    verified: int = 0
    corrected: int = 0
    undocumented: int = 0


class PublishResult(BaseModel):
    quality_passed: bool = False
    article_path: str = ""
    preview_url: str = ""
    published_url: Optional[str] = None
    calibration: Calibration = Field(default_factory=Calibration)
    # Rework fields — omitted when no rework (Step Functions IsPresent semantics)
    rework_needed: Optional[bool] = Field(default=None, exclude=True)
    rework_type: Optional[ReworkType] = None
    reason: Optional[str] = None

    def to_sfn_output(self) -> dict:
        """Serialize for Step Functions, including rework_needed only when True."""
        data = self.model_dump(exclude_none=True)
        if self.rework_needed:
            data["rework_needed"] = True
        return data
