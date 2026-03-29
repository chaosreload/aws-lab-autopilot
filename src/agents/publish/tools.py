"""Strands tool definitions for the Publish Agent."""

from __future__ import annotations

import base64
import json
import logging
import os
import re

import boto3
import requests
from strands import tool

from src.aws.knowledge import search_documentation

logger = logging.getLogger(__name__)


@tool
def quality_check(article_md: str) -> str:
    """Run 7 quality red-line checks on an article.

    Args:
        article_md: The full Markdown content of the article to check.

    Returns:
        JSON string with passed (bool), checks (dict), and failed_checks (list).
    """
    lower = article_md.lower()

    checks = {
        "reproducible": bool(re.search(r"```", article_md)),
        "has_data": bool(re.search(r"\|", article_md) or re.search(r"测试结果|test results", lower)),
        "has_boundary": bool(re.search(r"边界|boundary|限制|limit", lower)),
        "has_cost": bool(re.search(r"费用|cost|清理|cleanup|\$", lower)),
        "has_pitfall": bool(re.search(r"踩坑|pitfall|注意|warning", lower)),
        "calibrated": bool(re.search(r"校准|calibrated|aws-knowledge|官方文档", lower)),
        "has_iam": bool(re.search(r"iam|policy|permission", lower)),
    }

    failed = [k for k, v in checks.items() if not v]
    return json.dumps({
        "passed": len(failed) == 0,
        "checks": checks,
        "failed_checks": failed,
    })


@tool
def write_article(task_id: str, content: str, title: str = "") -> str:
    """Write an article to S3 and optionally update the DynamoDB task title.

    Args:
        task_id: The unique task identifier.
        content: Markdown content of the article.
        title: Optional article title to store in DynamoDB.

    Returns:
        The S3 path where the article was stored.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return json.dumps({"error": "S3_BUCKET environment variable not set"})

    key = f"tasks/{task_id}/article.md"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/markdown")
    s3_path = f"s3://{bucket}/{key}"
    logger.info("Wrote article to %s", s3_path)

    if title:
        table_name = os.environ.get("TASKS_TABLE", "handson-tasks")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET article_title = :t",
            ExpressionAttributeValues={":t": title},
        )

    return json.dumps({"article_path": s3_path})


@tool
def read_research_notes(task_id: str) -> str:
    """Read research notes from S3 for a given task.

    Args:
        task_id: The unique task identifier.

    Returns:
        The Markdown content of the research notes, or empty string if not found.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return ""

    key = f"tasks/{task_id}/notes.md"
    s3 = boto3.client("s3")
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return ""
    except Exception:
        logger.exception("Failed to read research notes for task %s", task_id)
        return ""


@tool
def read_execute_results(task_id: str) -> str:
    """Read execution verify log from S3 for a given task.

    Args:
        task_id: The unique task identifier.

    Returns:
        The Markdown content of the verify log, or empty string if not found.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return ""

    key = f"tasks/{task_id}/evidence/verify-log.md"
    s3 = boto3.client("s3")
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return ""
    except Exception:
        logger.exception("Failed to read execute results for task %s", task_id)
        return ""


@tool
def git_push(article_content: str, article_path: str, commit_message: str) -> str:
    """Push an article to a GitHub repository via the REST API.

    Args:
        article_content: The full Markdown content to push.
        article_path: File path within the repo (e.g. "docs/ai-ml/xxx.md").
        commit_message: The git commit message.

    Returns:
        JSON string with the result or skip message.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return json.dumps({"message": "GITHUB_TOKEN not configured, skipping push"})

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        return json.dumps({"error": "GITHUB_REPO environment variable not set"})

    api_url = f"https://api.github.com/repos/{repo}/contents/{article_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if file already exists to get its sha
    sha = None
    resp = requests.get(api_url, headers=headers, timeout=30)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    # Create or update the file
    payload = {
        "message": commit_message,
        "content": base64.b64encode(article_content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        return json.dumps({
            "status": "pushed",
            "path": article_path,
            "url": resp.json().get("content", {}).get("html_url", ""),
        })
    return json.dumps({"error": f"GitHub API error: {resp.status_code} {resp.text}"})


@tool
def aws_knowledge_read_publish(query: str) -> str:
    """Search AWS documentation for calibration during article publishing.

    Args:
        query: A search phrase describing the AWS topic to verify.

    Returns:
        JSON string with search results.
    """
    results = search_documentation(query, limit=3)
    if not results:
        return json.dumps({"results": [], "message": "No results found"})
    return json.dumps({"results": results}, ensure_ascii=False)
