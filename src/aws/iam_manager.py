"""IAM Scoped Role Manager — create / update / delete scoped IAM roles for lab execution.

Each lab execution gets a temporary IAM role with:
  - Layer 1: Base Policy (constant, always attached)
  - Layer 2: Task-specific inline policy (derived from ResearchResult.iam_policy)

Roles are tagged with task_id for tracking and cleanup.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)

ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
ROLE_PREFIX = "handson-lab-"
SESSION_DURATION = 3600  # 1 hour

# ---------------------------------------------------------------------------
# Layer 1 — Base Policy (always attached to every scoped role)
# ---------------------------------------------------------------------------

BASE_POLICY: dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BaseCloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
            ],
            "Resource": "arn:aws:logs:*:*:*",
        },
        {
            "Sid": "BaseCloudWatchMetrics",
            "Effect": "Allow",
            "Action": [
                "cloudwatch:PutMetricData",
                "cloudwatch:GetMetricData",
                "cloudwatch:ListMetrics",
            ],
            "Resource": "*",
        },
        {
            "Sid": "BaseSTSSelf",
            "Effect": "Allow",
            "Action": [
                "sts:GetCallerIdentity",
            ],
            "Resource": "*",
        },
        {
            "Sid": "BaseDenyEscalation",
            "Effect": "Deny",
            "Action": [
                "iam:CreateUser",
                "iam:CreateLoginProfile",
                "iam:CreateAccessKey",
                "iam:AttachUserPolicy",
                "iam:PutUserPolicy",
                "iam:AddUserToGroup",
                "organizations:*",
                "account:*",
            ],
            "Resource": "*",
        },
    ],
}

ASSUME_ROLE_POLICY: dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


class IAMManager:
    """Manages scoped IAM roles for per-task lab execution."""

    def __init__(self, *, iam_client=None):
        self.iam = iam_client or boto3.client("iam")

    def role_name(self, task_id: str) -> str:
        return f"{ROLE_PREFIX}{task_id[:32]}"

    def create_scoped_role(self, task_id: str, task_policy: dict) -> str:
        """Create an IAM role with base + task-specific policies. Returns role ARN."""
        name = self.role_name(task_id)
        logger.info("Creating scoped IAM role %s for task %s", name, task_id)

        try:
            resp = self.iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(ASSUME_ROLE_POLICY),
                MaxSessionDuration=SESSION_DURATION,
                Tags=[
                    {"Key": "project", "Value": "handson-lab"},
                    {"Key": "task_id", "Value": task_id},
                    {"Key": "managed-by", "Value": "autopilot"},
                ],
            )
            role_arn = resp["Role"]["Arn"]
        except self.iam.exceptions.EntityAlreadyExistsException:
            logger.info("Role %s already exists, reusing", name)
            resp = self.iam.get_role(RoleName=name)
            role_arn = resp["Role"]["Arn"]

        self.iam.put_role_policy(
            RoleName=name,
            PolicyName="base-policy",
            PolicyDocument=json.dumps(BASE_POLICY),
        )

        self.iam.put_role_policy(
            RoleName=name,
            PolicyName="task-policy",
            PolicyDocument=json.dumps(task_policy),
        )

        logger.info("Scoped role ready: %s", role_arn)
        return role_arn

    def update_task_policy(self, task_id: str, task_policy: dict) -> None:
        """Replace the task-specific inline policy on an existing role."""
        name = self.role_name(task_id)
        logger.info("Updating task-policy on role %s", name)
        self.iam.put_role_policy(
            RoleName=name,
            PolicyName="task-policy",
            PolicyDocument=json.dumps(task_policy),
        )

    def delete_scoped_role(self, task_id: str) -> None:
        """Delete the scoped role and its inline policies."""
        name = self.role_name(task_id)
        logger.info("Deleting scoped role %s", name)

        for policy_name in ("base-policy", "task-policy"):
            try:
                self.iam.delete_role_policy(RoleName=name, PolicyName=policy_name)
            except self.iam.exceptions.NoSuchEntityException:
                pass

        try:
            self.iam.delete_role(RoleName=name)
        except self.iam.exceptions.NoSuchEntityException:
            logger.warning("Role %s not found during cleanup", name)
