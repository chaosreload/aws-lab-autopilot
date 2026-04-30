"""Execute Agent tools — @tool functions for AWS CLI execution, evidence logging, and resource tracking."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time

import boto3

from src.autopilot.aws_session import get_boto3_session
from strands import tool

from src.agents.execute.safety_guard import SafetyGuard
from src.aws.knowledge import read_documentation, search_documentation

logger = logging.getLogger(__name__)

_guard = SafetyGuard()

# Module-level resource registry for track_resource / cleanup_resources
_TRACKED_RESOURCES: list[dict] = []


@tool
def aws_cli_execute(command: str) -> str:
    """Run an AWS CLI command and return structured output.

    Args:
        command: The full AWS CLI command to execute (e.g. "aws bedrock-runtime invoke-model ...").

    Returns:
        JSON string with stdout, stderr, exit_code, and duration_ms.
    """
    verdict = _guard.pre_execute(command)
    if not verdict.allowed:
        logger.warning("SafetyGuard blocked command: %s", command)
        return json.dumps({
            "stdout": "",
            "stderr": f"BLOCKED by SafetyGuard: {verdict.summary}",
            "exit_code": -1,
            "duration_ms": 0,
        })

    logger.info("Executing: %s", command[:200])
    start = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        duration_ms = int((time.time() - start) * 1000)
        result = {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        result = {
            "stdout": "",
            "stderr": "Command timed out after 300 seconds",
            "exit_code": -1,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = int((time.time() - start) * 1000)
        result = {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
            "duration_ms": duration_ms,
        }

    logger.info(
        "Command exit_code=%d duration_ms=%d stdout_len=%d stderr_len=%d",
        result["exit_code"],
        result["duration_ms"],
        len(result["stdout"]),
        len(result["stderr"]),
    )
    return json.dumps(result)


@tool
def iam_add_permission(action: str, resource: str) -> str:
    """Request an additional IAM permission (Phase 1 stub).

    Args:
        action: IAM action to add (e.g. "bedrock:InvokeModel").
        resource: IAM resource ARN or "*".

    Returns:
        JSON string with status and details.
    """
    verdict = _guard.check_iam_action(action)
    if not verdict.allowed:
        logger.warning("SafetyGuard blocked IAM action: %s", action)
        return json.dumps({
            "status": "blocked",
            "action": action,
            "resource": resource,
            "reason": verdict.summary,
        })

    logger.info("IAM permission requested (stub): %s on %s", action, resource)
    return json.dumps({
        "status": "noted",
        "action": action,
        "resource": resource,
    })


@tool
def write_execute_log(task_id: str, round_name: str, content: str, fmt: str) -> str:
    """Write execution evidence log to S3.

    Args:
        task_id: The task identifier.
        round_name: Round name — "explore" or "verify".
        content: Log content (markdown or JSON string).
        fmt: File format — "md" or "json".

    Returns:
        JSON string with the S3 path of the written log.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        raise RuntimeError("S3_BUCKET environment variable is not set")

    key = f"tasks/{task_id}/evidence/{round_name}-log.{fmt}"
    content_type = "text/markdown" if fmt == "md" else "application/json"

    s3 = get_boto3_session().client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType=content_type,
    )
    s3_path = f"s3://{bucket}/{key}"
    logger.info("Wrote execute log to %s", s3_path)
    return json.dumps({"s3_path": s3_path})


@tool
def track_resource(task_id: str, resource_type: str, resource_id: str, region: str) -> str:
    """Register a created AWS resource for later cleanup.

    Args:
        task_id: The task identifier.
        resource_type: Resource type (e.g. "s3:bucket", "bedrock:inference").
        resource_id: Resource ID or ARN.
        region: AWS region where the resource was created.

    Returns:
        JSON string confirming the resource was tracked.
    """
    entry = {
        "task_id": task_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "region": region,
        "tracked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _TRACKED_RESOURCES.append(entry)
    logger.info("Tracked resource: %s %s in %s", resource_type, resource_id, region)
    return json.dumps({"tracked": True, "resource_type": resource_type, "resource_id": resource_id})


@tool
def cleanup_resources(task_id: str) -> str:
    """Return tracked resources for cleanup and clear the registry.

    Args:
        task_id: The task identifier.

    Returns:
        JSON string with the list of resources to clean up and count.
    """
    resources = [r for r in _TRACKED_RESOURCES if r["task_id"] == task_id]
    # Clear entries for this task
    _TRACKED_RESOURCES[:] = [r for r in _TRACKED_RESOURCES if r["task_id"] != task_id]
    logger.info("Cleanup: returning %d tracked resources for task %s", len(resources), task_id)
    return json.dumps({"resources_to_cleanup": resources, "count": len(resources)})


@tool
def memory_create(task_id: str, pitfall_desc: str, verified: bool) -> str:
    """Record a pitfall to persistent memory (Phase 1 stub).

    Args:
        task_id: The task identifier.
        pitfall_desc: One-line description of the pitfall.
        verified: Whether the pitfall has stdout/stderr evidence.

    Returns:
        JSON string confirming the pitfall was noted.
    """
    # Phase 2: persist to AgentCore Memory
    logger.info("memory_create (stub): task=%s verified=%s pitfall=%s", task_id, verified, pitfall_desc)
    return json.dumps({"status": "noted", "task_id": task_id, "pitfall": pitfall_desc})


@tool
def aws_knowledge_read(query: str) -> str:
    """Search AWS official documentation for API details, usage examples, and constraints.

    Use this tool when:
    - An AWS CLI command returns UnknownOperationException or NoSuchOperation
    - You need to confirm the correct API name, parameters, or response format
    - You are unsure about service endpoints, required permissions, or request body schema

    Args:
        query: Natural language search query (e.g. "InvokeAgentRuntimeCommand API boto3").

    Returns:
        JSON string with search results containing title, url, and excerpt for each match.
    """
    try:
        results = search_documentation(query, limit=5)
        items = []
        for r in results:
            url = r.get("url", "")
            if url:
                detail = read_documentation(url, max_length=10000)
                items.append({
                    "title": r.get("title", r.get("text", "")),
                    "url": url,
                    "excerpt": detail[:3000] if isinstance(detail, str) else str(detail)[:3000],
                })
            else:
                items.append({
                    "title": r.get("title", r.get("text", "")),
                    "url": "",
                    "excerpt": r.get("text", ""),
                })
        return json.dumps({"results": items}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_knowledge_read failed: %s", exc)
        return f"Documentation search unavailable: {exc}"
