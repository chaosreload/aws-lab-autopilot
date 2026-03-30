"""SafetyGuard — pre-execution safety checks for the Execute Agent.

Validates that an execution plan is safe before running it in a real AWS account.
Enforces:
  - Service allow-list (only approved AWS services)
  - Action deny-list (block destructive / high-risk actions)
  - Estimated cost ceiling
  - Resource count limits per execution
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allow / Deny lists
# ---------------------------------------------------------------------------

ALLOWED_SERVICES: frozenset[str] = frozenset(
    {
        "s3",
        "dynamodb",
        "lambda",
        "iam",
        "sqs",
        "sns",
        "cloudformation",
        "cloudwatch",
        "logs",
        "ec2",
        "ecs",
        "ecr",
        "apigateway",
        "stepfunctions",
        "sts",
        "kms",
        "secretsmanager",
        "ssm",
        "events",
        "bedrock",
        "bedrock-runtime",
        "bedrock-agent-runtime",
        "bedrock-agent",
        "kinesis",
        "firehose",
        "athena",
        "glue",
        "codebuild",
        "codepipeline",
        "route53",
        "elasticloadbalancing",
        "elasticloadbalancingv2",
        "autoscaling",
        "application-autoscaling",
        "rds",
        "elasticache",
        "xray",
        "cognito-idp",
        "cognito-identity",
    }
)

DENIED_ACTION_PATTERNS: list[str] = [
    r"iam:CreateUser",
    r"iam:CreateLoginProfile",
    r"iam:CreateAccessKey",
    r"iam:AttachUserPolicy",
    r"iam:PutUserPolicy",
    r"iam:AddUserToGroup",
    r"organizations:.*",
    r"account:.*",
    r"ec2:RunInstances",
    r"ec2:RequestSpotInstances",
    r"ec2:StartInstances",
    r"rds:CreateDBInstance",
    r"rds:RestoreDBInstanceFromDBSnapshot",
    r"elasticache:CreateCacheCluster",
    r".*:Delete\*",
    r".*:Terminate\*",
]

_DENIED_RE = [re.compile(p) for p in DENIED_ACTION_PATTERNS]

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

DEFAULT_MAX_COST_USD: float = 5.0
DEFAULT_MAX_RESOURCES: int = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    category: str  # "denied_action" | "blocked_service" | "cost_limit" | "resource_limit"
    detail: str


@dataclass
class SafetyVerdict:
    allowed: bool
    violations: list[Violation] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.allowed:
            return "PASS — no violations detected"
        lines = [f"BLOCKED — {len(self.violations)} violation(s):"]
        for v in self.violations:
            lines.append(f"  [{v.category}] {v.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SafetyGuard
# ---------------------------------------------------------------------------


class SafetyGuard:
    """Stateless safety checker invoked before every execution step."""

    def __init__(
        self,
        *,
        allowed_services: frozenset[str] | None = None,
        max_cost_usd: float = DEFAULT_MAX_COST_USD,
        max_resources: int = DEFAULT_MAX_RESOURCES,
    ):
        self.allowed_services = allowed_services or ALLOWED_SERVICES
        self.max_cost_usd = max_cost_usd
        self.max_resources = max_resources

    # ---- public API --------------------------------------------------------

    def pre_execute(self, command: str) -> SafetyVerdict:
        """Check whether a CLI-style command is safe to execute.

        Parses the command to extract the service and action, then validates
        against the allow/deny lists.
        """
        violations: list[Violation] = []
        parts = command.strip().split()

        # Extract service from 'aws <service> ...' pattern
        if len(parts) >= 2 and parts[0] == "aws":
            service = parts[1].lower()
            if service not in self.allowed_services:
                violations.append(
                    Violation(
                        category="blocked_service",
                        detail=f"Service '{service}' is not in the allow-list",
                    )
                )

        # Check for dangerous CIDR patterns (e.g. 0.0.0.0/0)
        if "0.0.0.0/0" in command or "::/0" in command:
            violations.append(
                Violation(
                    category="denied_action",
                    detail="Open CIDR block (0.0.0.0/0 or ::/0) is not allowed",
                )
            )

        verdict = SafetyVerdict(allowed=len(violations) == 0, violations=violations)
        if not verdict.allowed:
            logger.warning("SafetyGuard pre_execute BLOCKED: %s", verdict.summary)
        return verdict

    def check_iam_action(self, action: str) -> SafetyVerdict:
        """Check whether a single IAM action is safe to grant."""
        violations: list[Violation] = []
        for pat in _DENIED_RE:
            if pat.fullmatch(action):
                violations.append(
                    Violation(
                        category="denied_action",
                        detail=f"Action '{action}' matches deny pattern '{pat.pattern}'",
                    )
                )
                break

        verdict = SafetyVerdict(allowed=len(violations) == 0, violations=violations)
        if not verdict.allowed:
            logger.warning("SafetyGuard check_iam_action BLOCKED: %s", verdict.summary)
        return verdict

    def check(
        self,
        *,
        iam_policy: dict,
        estimated_cost: float = 0.0,
        services: list[str] | None = None,
        resource_count: int = 0,
    ) -> SafetyVerdict:
        violations: list[Violation] = []

        violations.extend(self._check_services(services or []))
        violations.extend(self._check_actions(iam_policy))
        violations.extend(self._check_cost(estimated_cost))
        violations.extend(self._check_resource_count(resource_count))

        verdict = SafetyVerdict(allowed=len(violations) == 0, violations=violations)
        if not verdict.allowed:
            logger.warning("SafetyGuard BLOCKED: %s", verdict.summary)
        else:
            logger.info("SafetyGuard PASS")
        return verdict

    # ---- internal checks ---------------------------------------------------

    def _check_services(self, services: list[str]) -> list[Violation]:
        violations: list[Violation] = []
        for svc in services:
            if svc.lower() not in self.allowed_services:
                violations.append(
                    Violation(
                        category="blocked_service",
                        detail=f"Service '{svc}' is not in the allow-list",
                    )
                )
        return violations

    def _check_actions(self, iam_policy: dict) -> list[Violation]:
        violations: list[Violation] = []
        for stmt in iam_policy.get("Statement", []):
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                if action == "*":
                    violations.append(
                        Violation(
                            category="denied_action",
                            detail="Wildcard action '*' is not allowed",
                        )
                    )
                    continue
                for pat in _DENIED_RE:
                    if pat.fullmatch(action):
                        violations.append(
                            Violation(
                                category="denied_action",
                                detail=f"Action '{action}' matches deny pattern '{pat.pattern}'",
                            )
                        )
                        break
        return violations

    def _check_cost(self, estimated_cost: float) -> list[Violation]:
        if estimated_cost > self.max_cost_usd:
            return [
                Violation(
                    category="cost_limit",
                    detail=(
                        f"Estimated cost ${estimated_cost:.2f} "
                        f"exceeds limit ${self.max_cost_usd:.2f}"
                    ),
                )
            ]
        return []

    def _check_resource_count(self, resource_count: int) -> list[Violation]:
        if resource_count > self.max_resources:
            return [
                Violation(
                    category="resource_limit",
                    detail=(
                        f"Resource count {resource_count} "
                        f"exceeds limit {self.max_resources}"
                    ),
                )
            ]
        return []
