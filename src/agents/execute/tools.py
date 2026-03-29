"""Strands tool definitions for the Execute Agent."""

from __future__ import annotations

import json
import logging
import os
import shlex

import boto3
from botocore.exceptions import ClientError
from strands import tool

from src.agents.execute.safety_guard import SafetyGuard
from src.aws.iam_manager import IAMManager
from src.aws.resource_tracker import ResourceTracker

logger = logging.getLogger(__name__)

_safety_guard = SafetyGuard()
_iam_manager: IAMManager | None = None
_resource_tracker: ResourceTracker | None = None


def _get_iam_manager() -> IAMManager:
    global _iam_manager
    if _iam_manager is None:
        _iam_manager = IAMManager()
    return _iam_manager


def _get_resource_tracker() -> ResourceTracker:
    global _resource_tracker
    if _resource_tracker is None:
        _resource_tracker = ResourceTracker()
    return _resource_tracker

# ---------------------------------------------------------------------------
# Supported service routers (aws cli command -> boto3 call)
# ---------------------------------------------------------------------------

_SERVICE_CLIENTS: dict[str, object] = {}


def _get_client(service: str):
    if service not in _SERVICE_CLIENTS:
        _SERVICE_CLIENTS[service] = boto3.client(service)
    return _SERVICE_CLIENTS[service]


def _route_s3(args: list[str]) -> dict:
    """Route s3 sub-commands to boto3."""
    client = _get_client("s3")
    if not args:
        return {"error": "No s3 sub-command provided"}
    sub = args[0]
    if sub == "mb" and len(args) >= 2:
        bucket = args[1].replace("s3://", "")
        client.create_bucket(Bucket=bucket)
        return {"action": "CreateBucket", "bucket": bucket, "status": "created"}
    if sub == "rb" and len(args) >= 2:
        bucket = args[1].replace("s3://", "")
        client.delete_bucket(Bucket=bucket)
        return {"action": "DeleteBucket", "bucket": bucket, "status": "deleted"}
    if sub == "ls":
        resp = client.list_buckets()
        return {"buckets": [b["Name"] for b in resp.get("Buckets", [])]}
    if sub == "cp" and len(args) >= 3:
        src, dst = args[1], args[2]
        if src.startswith("s3://"):
            parts = src.replace("s3://", "").split("/", 1)
            resp = client.get_object(Bucket=parts[0], Key=parts[1] if len(parts) > 1 else "")
            return {"action": "GetObject", "bucket": parts[0], "status": "downloaded"}
        if dst.startswith("s3://"):
            parts = dst.replace("s3://", "").split("/", 1)
            client.put_object(Bucket=parts[0], Key=parts[1] if len(parts) > 1 else "", Body=b"")
            return {"action": "PutObject", "bucket": parts[0], "status": "uploaded"}
    return {"error": f"Unsupported s3 sub-command: {sub}"}


def _route_dynamodb(args: list[str]) -> dict:
    """Route dynamodb sub-commands to boto3."""
    client = _get_client("dynamodb")
    if not args:
        return {"error": "No dynamodb sub-command provided"}
    sub = args[0]
    if sub == "list-tables":
        resp = client.list_tables()
        return {"tables": resp.get("TableNames", [])}
    if sub == "describe-table" and len(args) >= 2:
        table_name = args[1].replace("--table-name", "").strip()
        resp = client.describe_table(TableName=table_name)
        return {"table": resp["Table"]["TableName"], "status": resp["Table"]["TableStatus"]}
    return {"error": f"Unsupported dynamodb sub-command: {sub}"}


def _route_lambda(args: list[str]) -> dict:
    """Route lambda sub-commands to boto3."""
    client = _get_client("lambda")
    if not args:
        return {"error": "No lambda sub-command provided"}
    sub = args[0]
    if sub == "list-functions":
        resp = client.list_functions()
        return {"functions": [f["FunctionName"] for f in resp.get("Functions", [])]}
    return {"error": f"Unsupported lambda sub-command: {sub}"}


def _route_sts(args: list[str]) -> dict:
    """Route sts sub-commands to boto3."""
    client = _get_client("sts")
    if not args:
        return {"error": "No sts sub-command provided"}
    sub = args[0]
    if sub == "get-caller-identity":
        resp = client.get_caller_identity()
        return {"Account": resp["Account"], "Arn": resp["Arn"], "UserId": resp["UserId"]}
    return {"error": f"Unsupported sts sub-command: {sub}"}


_ROUTERS = {
    "s3": _route_s3,
    "dynamodb": _route_dynamodb,
    "lambda": _route_lambda,
    "sts": _route_sts,
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def aws_cli_execute(command: str) -> str:
    """Execute an AWS CLI-style command via boto3 routing.

    The command is first validated by SafetyGuard. Supported services: s3, dynamodb, lambda, sts.

    Args:
        command: An AWS CLI command string (e.g. "aws s3 ls").

    Returns:
        JSON string with command output, or an error/block marker.
    """
    verdict = _safety_guard.pre_execute(command)
    if not verdict.allowed:
        return json.dumps({"blocked": True, "reason": verdict.summary})

    parts = shlex.split(command)
    if len(parts) < 2 or parts[0] != "aws":
        return json.dumps({"error": f"COMMAND_NOT_SUPPORTED: {command}"})

    service = parts[1].lower()
    router = _ROUTERS.get(service)
    if router is None:
        return json.dumps({"error": f"COMMAND_NOT_SUPPORTED: {command}"})

    try:
        result = router(parts[2:])
        return json.dumps(result, default=str, ensure_ascii=False)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess"):
            return json.dumps({"error": f"ACCESS_DENIED: {msg}"})
        return json.dumps({"error": f"{code}: {msg}"})


@tool
def iam_add_permission(role_name: str, action: str, resource: str = "*") -> str:
    """Add an IAM permission to a scoped role after safety validation.

    Args:
        role_name: The IAM role name to update.
        action: The IAM action to grant (e.g. "s3:CreateBucket").
        resource: The resource ARN scope. Defaults to "*".

    Returns:
        Confirmation message or block reason.
    """
    verdict = _safety_guard.check_iam_action(action)
    if not verdict.allowed:
        return json.dumps({"blocked": True, "reason": verdict.summary})

    policy = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": [action], "Resource": resource}],
    }
    try:
        _get_iam_manager().iam.put_role_policy(
            RoleName=role_name,
            PolicyName=f"added-{action.replace(':', '-')}",
            PolicyDocument=json.dumps(policy),
        )
        return json.dumps({"status": f"Added {action} to {role_name}"})
    except ClientError as e:
        return json.dumps({"error": e.response["Error"]["Message"]})


@tool
def track_resource(task_id: str, resource_arn: str, resource_type: str, region: str) -> str:
    """Register a created AWS resource for tracking and cleanup.

    Args:
        task_id: The unique task identifier.
        resource_arn: The ARN of the created resource.
        resource_type: The type of resource (e.g. "s3:bucket", "lambda:function").
        region: The AWS region where the resource was created.

    Returns:
        Confirmation message.
    """
    _get_resource_tracker().record(
        task_id=task_id,
        resource_type=resource_type,
        resource_id=resource_arn,
        region=region,
        arn=resource_arn,
    )
    return json.dumps({"status": f"Tracked {resource_arn}"})


@tool
def cleanup_resources(task_id: str) -> str:
    """Mark all tracked resources for a task as deleted.

    Args:
        task_id: The unique task identifier.

    Returns:
        JSON with cleanup count.
    """
    count = _get_resource_tracker().mark_all_deleted(task_id)
    return json.dumps({"status": "cleanup_complete", "resources_cleaned": count})


@tool
def write_execute_log(task_id: str, phase: str, content: str) -> str:
    """Write an execution log to S3 for evidence.

    Args:
        task_id: The unique task identifier.
        phase: The execution phase ("explore" or "verify").
        content: Markdown content to write as the log.

    Returns:
        The S3 URI where the log was stored.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return json.dumps({"error": "S3_BUCKET environment variable not set"})

    key = f"tasks/{task_id}/evidence/{phase}-log.md"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/markdown")
    uri = f"s3://{bucket}/{key}"
    logger.info("Wrote execute log to %s", uri)
    return json.dumps({"log_path": uri})


@tool
def memory_create(content: str, task_id: str = "") -> str:
    """Record a memory entry for future reference (stub — Phase 2 will use AgentCore Memory).

    Args:
        content: The content to remember.
        task_id: Optional task identifier for context.

    Returns:
        Confirmation message.
    """
    logger.info("Memory recorded (stub) task=%s content=%s", task_id, content[:100])
    return json.dumps({"status": "Memory recorded"})
