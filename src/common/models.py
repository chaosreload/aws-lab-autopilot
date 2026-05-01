"""Data models for aws-lab-autopilot pipeline.

Covers Research / Execute / Publish agent I/O plus shared enums.
Aligned with: openspec/specs/agent-io.md + phase2-gap-analysis.md (Gap 4)
    + openspec/changes/phase2-stage1-test-environment/specs/stage1-test-environment.md
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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


class VpcPreference(str, Enum):
    NONE = "none"                      # 测试全程无 VPC（纯 API 调用）
    DEFAULT_VPC = "default_vpc"        # 使用 account 默认 VPC
    LAB_VPC_REQUIRED = "lab_vpc_required"  # 必须由 Execute 创建 lab VPC


# ---------------------------------------------------------------------------
# TestEnvironment building blocks (Stage 1)
# ---------------------------------------------------------------------------

REGION_WHITELIST = frozenset({"us-east-1", "us-west-2", "ap-southeast-1"})

MANDATORY_TAG_KEYS = frozenset({
    "autopilot:task_id",
    "autopilot:stage",
    "autopilot:owner",
})


class Prerequisite(BaseModel):
    """A verifiable precondition before Execute runs test_matrix.

    type is restricted in Stage 1 to the three below (spec §7); Agent
    MAY emit others but Execute will skip-validate and warn.
    """

    type: str                                          # bedrock_model_access | service_quota | bucket_exists | ...
    description: str = ""                              # 一行可读说明
    params: dict = Field(default_factory=dict)         # type-specific payload


class CleanupPolicy(BaseModel):
    """Cleanup rules consumed by Execute Agent + Gap 9 EventBridge orphan scanner."""

    ttl_hours: float                                   # 资源最长存活时间
    on_failure: str = "terminate_all"                  # terminate_all | preserve_for_debug | ask_human
    orphan_scan: bool = True                           # 参与 Gap 9 orphan scanner


class TestEnvironment(BaseModel):
    """Full description of the test environment produced by Research Agent.

    Stage 1 core output; consumed by Execute Agent (Stage 2) to enforce
    region, account, tag, budget, cleanup, and prerequisite guarantees.
    """

    region: str                                        # e.g. us-east-1
    region_reason: str                                 # 一行决策说明（必须引用证据）
    account_id: str                                    # 来自 task.aws_identity.Account
    vpc_preference: VpcPreference = VpcPreference.NONE
    tag_strategy: dict[str, str] = Field(default_factory=dict)
    budget_limit_usd: float = 0.0                      # 静态估算；Execute 超额必停
    cleanup_policy: CleanupPolicy
    prerequisites: list[Prerequisite] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators (spec §2, §4, §5, §6, §9)
    # ------------------------------------------------------------------

    @field_validator("region")
    @classmethod
    def _region_must_be_whitelisted(cls, v: str) -> str:
        if v not in REGION_WHITELIST:
            raise ValueError(
                f"region '{v}' not in whitelist {sorted(REGION_WHITELIST)}; "
                "post-parser should sanitize before this validator runs"
            )
        return v

    @field_validator("region_reason")
    @classmethod
    def _region_reason_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("region_reason must be a non-empty evidence string")
        if len(v) > 200:
            raise ValueError("region_reason must be ≤ 200 chars (one line)")
        return v

    @field_validator("tag_strategy")
    @classmethod
    def _tag_strategy_has_mandatory_keys(cls, v: dict[str, str]) -> dict[str, str]:
        missing = MANDATORY_TAG_KEYS - set(v.keys())
        if missing:
            raise ValueError(
                f"tag_strategy missing mandatory keys: {sorted(missing)}"
            )
        owner = v.get("autopilot:owner")
        if owner != "archie":
            raise ValueError(
                f"autopilot:owner must be 'archie' (got {owner!r})"
            )
        return v

    @field_validator("budget_limit_usd")
    @classmethod
    def _budget_nonnegative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("budget_limit_usd must be ≥ 0")
        if v > 100:
            raise ValueError(
                "budget_limit_usd must be ≤ 100 (long-running upper bound); "
                "higher values require explicit Stage 1.5 carve-out"
            )
        return v


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
    # Stage 1: full test environment (required when verdict=go, optional when skip)
    environment: Optional[TestEnvironment] = None

    @model_validator(mode="after")
    def _environment_required_when_go(self) -> "ResearchResult":
        if self.verdict == Verdict.GO and self.environment is None:
            raise ValueError(
                "environment is required when verdict=go (Stage 1 spec §1)"
            )
        return self


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
