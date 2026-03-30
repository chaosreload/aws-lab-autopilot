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


_PLACEHOLDER_RE = re.compile(r"\.\.\.|预期输出|expected output|TBD", re.IGNORECASE)
_PRECISION_RE = re.compile(r"\d+\.\d{3,}")
_TABLE_RE = re.compile(r"^\|.+\|", re.MULTILINE)
_ERROR_KW_RE = re.compile(r"exception|error|denied|traceback|failed", re.IGNORECASE)
_SPECULATIVE_RE = re.compile(r"可能|建议注意|可能会|maybe|might", re.IGNORECASE)


def _extract_pitfall_section(article_md: str) -> str:
    """Extract the pitfall / 踩坑 section content."""
    pattern = re.compile(
        r"^#{1,3}\s*(?:踩坑|pitfall).*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(article_md)
    if not m:
        return ""
    start = m.end()
    next_heading = re.search(r"^#{1,3}\s", article_md[start:], re.MULTILINE)
    if next_heading:
        return article_md[start : start + next_heading.start()]
    return article_md[start:]


@tool
def quality_check(article_md: str) -> str:
    """Run 7 quality red-line checks on an article with substantive validation.

    Args:
        article_md: The full Markdown content of the article to check.

    Returns:
        JSON string with passed (bool), checks (dict of {pass, detail}),
        failed_checks (list), and blocking_issues (list).
    """
    lower = article_md.lower()

    # --- has_data: table + precision number + no placeholders ---
    has_table = bool(_TABLE_RE.search(article_md))
    has_precision = bool(_PRECISION_RE.search(article_md))
    has_placeholder = bool(_PLACEHOLDER_RE.search(article_md))
    data_pass = has_table and has_precision and not has_placeholder
    data_detail = (
        f"table={'Y' if has_table else 'N'}, "
        f"precision={'Y' if has_precision else 'N'}, "
        f"placeholder={'Y (blocking)' if has_placeholder else 'N'}"
    )

    # --- has_pitfall: error keywords + no speculative language ---
    pitfall_section = _extract_pitfall_section(article_md)
    pitfall_has_error_kw = bool(_ERROR_KW_RE.search(pitfall_section)) if pitfall_section else False
    pitfall_has_speculative = bool(_SPECULATIVE_RE.search(pitfall_section)) if pitfall_section else False
    pitfall_pass = bool(pitfall_section) and pitfall_has_error_kw and not pitfall_has_speculative
    pitfall_detail = (
        f"section={'Y' if pitfall_section else 'N'}, "
        f"error_kw={'Y' if pitfall_has_error_kw else 'N'}, "
        f"speculative={'Y (blocking)' if pitfall_has_speculative else 'N'}"
    )

    # --- other checks (kept from original) ---
    reproducible_pass = bool(re.search(r"```", article_md))
    boundary_pass = bool(re.search(r"边界|boundary|限制|limit", lower))
    cost_pass = bool(re.search(r"费用|cost|清理|cleanup|\$", lower))
    calibrated_pass = bool(re.search(r"校准|calibrated|aws-knowledge|官方文档", lower))
    iam_pass = bool(re.search(r"iam|policy|permission", lower))

    checks = {
        "reproducible": {"pass": reproducible_pass, "detail": "Has code blocks" if reproducible_pass else "No code blocks found"},
        "has_data": {"pass": data_pass, "detail": data_detail},
        "has_boundary": {"pass": boundary_pass, "detail": "Boundary section present" if boundary_pass else "No boundary/limit keywords"},
        "has_cost": {"pass": cost_pass, "detail": "Cost info present" if cost_pass else "No cost/cleanup keywords"},
        "has_pitfall": {"pass": pitfall_pass, "detail": pitfall_detail},
        "calibrated": {"pass": calibrated_pass, "detail": "Calibration references found" if calibrated_pass else "No calibration keywords"},
        "has_iam": {"pass": iam_pass, "detail": "IAM info present" if iam_pass else "No IAM/policy/permission keywords"},
    }

    failed = [k for k, v in checks.items() if not v["pass"]]
    blocking_issues = []
    if has_placeholder:
        blocking_issues.append("Article contains placeholder text (e.g. '...' / '预期输出' / 'TBD')")
    if pitfall_section and pitfall_has_speculative:
        blocking_issues.append("Pitfall section contains speculative language instead of evidence-based findings")
    if pitfall_section and not pitfall_has_error_kw:
        blocking_issues.append("Pitfall section lacks error evidence keywords (exception/error/denied)")

    return json.dumps({
        "passed": len(failed) == 0,
        "checks": checks,
        "failed_checks": failed,
        "blocking_issues": blocking_issues,
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
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/plain; charset=utf-8")
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
def generate_preview_url(task_id: str) -> str:
    """Generate a pre-signed URL for previewing the article on S3 (valid 24 hours).

    Args:
        task_id: The unique task identifier.

    Returns:
        JSON string with preview_url and expires_in.
    """
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return json.dumps({"error": "S3_BUCKET environment variable not set"})

    key = f"tasks/{task_id}/article.md"
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=86400,
    )
    return json.dumps({"preview_url": url, "expires_in": "24 hours"})


_github_config_cache: dict | None = None


def _get_github_config() -> dict:
    """Read GitHub config from Secrets Manager with local cache."""
    global _github_config_cache
    if _github_config_cache is not None:
        return _github_config_cache
    secret_name = os.environ.get("GITHUB_SECRET_NAME", "aws-lab-autopilot/github")
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = client.get_secret_value(SecretId=secret_name)
    _github_config_cache = json.loads(response["SecretString"])
    return _github_config_cache


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
    try:
        cfg = _get_github_config()
    except Exception as e:
        logger.warning("Failed to read GitHub secret: %s", e)
        return json.dumps({"message": "GitHub secret not configured, skipping push"})

    token = cfg.get("GITHUB_TOKEN", "")
    if not token:
        return json.dumps({"message": "GITHUB_TOKEN not configured, skipping push"})

    repo = cfg.get("GITHUB_REPO", "")
    if not repo:
        return json.dumps({"error": "GITHUB_REPO not set in secret"})

    branch = cfg.get("GITHUB_BRANCH", "main")
    base_path = cfg.get("GITHUB_ARTICLE_BASE_PATH", "docs")

    # Ensure article_path is prefixed with base_path
    if not article_path.startswith(base_path + "/") and not article_path.startswith(base_path + "\\"):
        article_path = f"{base_path}/{article_path}"

    api_url = f"https://api.github.com/repos/{repo}/contents/{article_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if file already exists to get its sha
    sha = None
    resp = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=30)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    # Create or update the file
    payload = {
        "message": commit_message,
        "content": base64.b64encode(article_content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        return json.dumps({
            "status": "pushed",
            "path": article_path,
            "branch": branch,
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
