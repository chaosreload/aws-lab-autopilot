"""Strands tool definitions for the Execute Agent."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import time

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

# ---------------------------------------------------------------------------
# Evidence records (module-level accumulator)
# ---------------------------------------------------------------------------

_evidence_records: list[dict] = []


def reset_evidence() -> None:
    """Clear all accumulated evidence records."""
    _evidence_records.clear()


def _append_evidence(
    *,
    tool_name: str,
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_ms: int,
) -> None:
    _evidence_records.append({
        "tool": tool_name,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


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
# Tools
# ---------------------------------------------------------------------------


@tool
def aws_cli_execute(command: str) -> str:
    """Execute an AWS CLI command via subprocess.

    The command is first validated by SafetyGuard, then executed as a real
    subprocess call to the AWS CLI.

    Args:
        command: An AWS CLI command string (e.g. "aws s3 ls").

    Returns:
        JSON string with stdout, stderr, exit_code, and duration_ms.
    """
    verdict = _safety_guard.pre_execute(command)
    if not verdict.allowed:
        return json.dumps({"blocked": True, "reason": verdict.summary})

    parts = shlex.split(command)
    if len(parts) < 2 or parts[0] != "aws":
        return json.dumps({"error": f"COMMAND_NOT_SUPPORTED: {command}"})

    start = time.monotonic()
    try:
        proc = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return json.dumps({"error": "AWS CLI not found. Please install AWS CLI v2."})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Command timed out after 120 seconds"})

    duration_ms = int((time.monotonic() - start) * 1000)

    _append_evidence(
        tool_name="aws_cli_execute",
        command=command,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
    )

    return json.dumps({
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
    }, ensure_ascii=False)


@tool
def python_execute(code: str) -> str:
    """Execute a Python code snippet via subprocess.

    Writes the code to a temporary .py file, runs it with python3, and returns
    the result. The temporary file is cleaned up after execution.

    Args:
        code: Python source code to execute.

    Returns:
        JSON string with stdout, stderr, exit_code, and duration_ms.
    """
    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="lab_exec_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["python3", script_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Python script timed out after 120 seconds"})

        duration_ms = int((time.monotonic() - start) * 1000)

        _append_evidence(
            tool_name="python_execute",
            command=f"python3 <script>\n{code}",
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            duration_ms=duration_ms,
        )

        return json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }, ensure_ascii=False)
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


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
        resource_arn=resource_arn,
        region=region,
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

    Writes both a Markdown log and a structured JSON evidence file.

    Args:
        task_id: The unique task identifier.
        phase: The execution phase ("explore" or "verify").
        content: Markdown content to write as the log.

    Returns:
        The S3 URIs where the log and evidence JSON were stored.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return json.dumps({"error": "S3_BUCKET environment variable not set"})

    s3 = boto3.client("s3")

    # Write Markdown log
    md_key = f"tasks/{task_id}/evidence/{phase}-log.md"
    s3.put_object(Bucket=bucket, Key=md_key, Body=content.encode("utf-8"), ContentType="text/markdown")
    md_uri = f"s3://{bucket}/{md_key}"

    # Write evidence JSON
    json_key = f"tasks/{task_id}/evidence/{phase}-log.json"
    evidence_body = json.dumps(_evidence_records, ensure_ascii=False, indent=2)
    s3.put_object(Bucket=bucket, Key=json_key, Body=evidence_body.encode("utf-8"), ContentType="application/json")
    json_uri = f"s3://{bucket}/{json_key}"

    logger.info("Wrote execute log to %s and evidence to %s", md_uri, json_uri)
    return json.dumps({"log_path": md_uri, "evidence_path": json_uri})


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
