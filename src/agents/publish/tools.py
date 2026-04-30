"""Publish Agent tools — @tool functions for article writing, quality checks, and publishing."""

from __future__ import annotations

import json
import logging
import os
import re

import boto3
from strands import tool

from src.aws.knowledge import read_documentation, search_documentation

logger = logging.getLogger(__name__)

# Module-level counter for calibration tracking
_KNOWLEDGE_CALL_COUNT = 0


def _get_s3_bucket() -> str:
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        raise RuntimeError("S3_BUCKET environment variable is not set")
    return bucket


def _s3_get_text(bucket: str, key: str) -> str | None:
    """Read a text object from S3, returning None if not found."""
    try:
        s3 = boto3.client("s3")
        resp = s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return None
    except Exception:
        return None


@tool
def read_research_notes(task_id: str) -> str:
    """Read research notes from S3.

    Args:
        task_id: The task identifier.

    Returns:
        Research notes markdown content, or error message if not found.
    """
    bucket = _get_s3_bucket()
    key = f"tasks/{task_id}/notes.md"
    content = _s3_get_text(bucket, key)
    if content is None:
        logger.warning("Research notes not found: s3://%s/%s", bucket, key)
        return f"ERROR: Research notes not found at s3://{bucket}/{key}"
    logger.info("Read research notes: %d chars from s3://%s/%s", len(content), bucket, key)
    return content


@tool
def read_execute_results(task_id: str) -> str:
    """Read execution evidence from S3 (verify-log preferred, explore-log as fallback).

    Args:
        task_id: The task identifier.

    Returns:
        Combined markdown and JSON evidence content.
    """
    bucket = _get_s3_bucket()
    parts = []

    # Try verify-log first, fall back to explore-log
    for prefix in ("verify", "explore"):
        md_key = f"tasks/{task_id}/evidence/{prefix}-log.md"
        json_key = f"tasks/{task_id}/evidence/{prefix}-log.json"
        md_content = _s3_get_text(bucket, md_key)
        json_content = _s3_get_text(bucket, json_key)

        if md_content is not None or json_content is not None:
            if md_content:
                parts.append(f"# {prefix.title()} Log (Markdown)\n\n{md_content}")
            if json_content:
                parts.append(f"# {prefix.title()} Log (JSON)\n\n{json_content}")
            logger.info("Read %s-log evidence for task %s", prefix, task_id)
            break
    else:
        logger.warning("No execution evidence found for task %s", task_id)
        return f"ERROR: No execution evidence found for task {task_id}"

    return "\n\n---\n\n".join(parts)


@tool
def aws_knowledge_read_publish(query: str) -> str:
    """Search AWS official documentation for calibration.

    Use this to verify API names, response schemas, service limits, and model IDs
    before writing the article. You MUST call this at least 3 times during calibration.

    Args:
        query: Natural language search query.

    Returns:
        JSON string with search results containing title, url, and excerpt for each match.
    """
    global _KNOWLEDGE_CALL_COUNT
    _KNOWLEDGE_CALL_COUNT += 1
    logger.info("aws_knowledge_read_publish call #%d: %s", _KNOWLEDGE_CALL_COUNT, query[:100])

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
        return json.dumps({"results": items, "call_count": _KNOWLEDGE_CALL_COUNT}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("aws_knowledge_read_publish failed: %s", exc)
        return f"Documentation search unavailable: {exc}"


@tool
def quality_check(article: str) -> str:
    """Run 7 quality red-line checks on the article.

    Args:
        article: The full article markdown content.

    Returns:
        JSON string with passed (bool), checks (dict), and failures (list).
    """
    checks = {}

    # 1. reproducible: >= 2 code blocks AND at least one ```bash or ```console
    code_blocks = re.findall(r"```\w*", article)
    has_cli_block = bool(re.search(r"```(?:bash|console)", article))
    checks["reproducible"] = len(code_blocks) >= 2 and has_cli_block

    # 2. has_data: markdown table AND numeric data AND no placeholder text
    has_table = bool(re.search(r"^\|.+\|", article, re.MULTILINE))
    has_numbers = bool(re.search(r"\b\d+(?:\.\d+)?\b", article))
    # has_placeholder: only detect standalone ... in prose, skip code blocks and backtick spans
    _in_code = False
    _has_ph = False
    for _line in article.split("\n"):
        _s = _line.strip()
        if _s.startswith("```"):
            _in_code = not _in_code
        if _in_code:
            continue
        _cleaned = re.sub(r"`[^`]*`", "", _s)
        if re.search(r"(?<!\.)\.\.\.(?!\.)", _cleaned) or re.search(r"\b预期输出\b|\bTBD\b", _cleaned):
            _has_ph = True
            break
    checks["has_data"] = has_table and has_numbers and not _has_ph

    # 3. has_boundary
    checks["has_boundary"] = bool(re.search(
        r"boundary|边界|limit|限制|ValidationException|overflow|超出|最大|最小",
        article, re.IGNORECASE,
    ))

    # 4. has_cost
    checks["has_cost"] = bool(re.search(
        r"费用|cost|清理|cleanup|\$|USD|¥",
        article, re.IGNORECASE,
    ))

    # 5. has_pitfall: !!! warning AND error evidence AND no speculative language
    has_warning = "!!! warning" in article
    has_error_evidence = bool(re.search(
        r"Error|Exception|error|failed|失败|报错", article,
    ))
    has_speculative = bool(re.search(
        r"可能会|有时会|也许|might|may cause", article,
    ))
    checks["has_pitfall"] = has_warning and has_error_evidence and not has_speculative

    # 6. calibrated: !!! info AND ## 核心概念 AND >= 3 **发现:** AND ## 参考链接
    has_info = "!!! info" in article
    has_core_concepts = "## 核心概念" in article
    discovery_count = len(re.findall(r"\*\*发现\*\*[:：]|\*\*发现[:：]\*\*", article))
    has_refs = "## 参考链接" in article
    checks["calibrated"] = has_info and has_core_concepts and discovery_count >= 3 and has_refs

    # 7. has_iam
    checks["has_iam"] = bool(re.search(
        r"IAM|iam|policy|Policy|permission|Permission|权限|策略",
        article,
    ))

    # 8. has_background: ## 背景 section exists
    checks["has_background"] = "## 背景" in article

    # 9. has_prerequisites: ## 前置条件 section exists
    checks["has_prerequisites"] = "## 前置条件" in article

    # 10. no_internal_ids: headings must not contain internal test IDs (T0, T1, T2...)
    heading_lines = re.findall(r"^#{1,3} .+$", article, re.MULTILINE)
    has_internal_ids = any(re.search(r"\bT\d+\b", h) for h in heading_lines)
    checks["no_internal_ids"] = not has_internal_ids

    failures = [name for name, passed in checks.items() if not passed]
    result = {
        "passed": len(failures) == 0,
        "checks": checks,
        "failures": failures,
    }
    logger.info("quality_check: passed=%s failures=%s", result["passed"], failures)
    return json.dumps(result)


@tool
def write_article(task_id: str, content: str, title: str = "") -> str:
    """Write the article to S3.

    Args:
        task_id: The task identifier.
        content: Full article markdown content.
        title: Optional article title for logging.

    Returns:
        JSON string with s3_path and size_bytes.
    """
    bucket = _get_s3_bucket()
    key = f"tasks/{task_id}/article.md"

    s3 = boto3.client("s3")
    body = content.encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain; charset=utf-8")

    s3_path = f"s3://{bucket}/{key}"
    if title:
        logger.info("Wrote article '%s' to %s (%d bytes)", title, s3_path, len(body))
    else:
        logger.info("Wrote article to %s (%d bytes)", s3_path, len(body))
    return json.dumps({"s3_path": s3_path, "size_bytes": len(body)})


@tool
def generate_preview_url(task_id: str) -> str:
    """Generate a 24-hour pre-signed URL for the article.

    Args:
        task_id: The task identifier.

    Returns:
        JSON string with the preview_url.
    """
    bucket = _get_s3_bucket()
    key = f"tasks/{task_id}/article.md"

    s3 = boto3.client("s3")
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=86400,
    )
    logger.info("Generated preview URL for task %s (24h expiry)", task_id)
    return json.dumps({"preview_url": url})


@tool
def git_push(task_id: str, article_path: str, title: str) -> str:
    """Push article to GitHub (Phase 1 stub — only called from /tasks/{id}/approve).

    Args:
        task_id: The task identifier.
        article_path: S3 path of the article.
        title: Article title.

    Returns:
        JSON string with stub status.
    """
    # Phase 1: stub — git_push is only called from /tasks/{id}/approve endpoint
    logger.info("git_push (stub): task=%s article=%s title=%s", task_id, article_path, title)
    return json.dumps({
        "status": "stub",
        "note": "git_push is only called from /tasks/{id}/approve endpoint",
    })


@tool
def memory_search_publish(query: str) -> str:
    """Search AgentCore Memory for historical calibration records (Phase 1 stub).

    Args:
        query: Search query for memory.

    Returns:
        JSON string with empty results.
    """
    # Phase 2: persist to AgentCore Memory
    return json.dumps({
        "results": [],
        "note": "memory_search stub — Phase 2 will connect to AgentCore Memory",
    })
