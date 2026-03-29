"""Strands tool definitions for the Research Agent."""

from __future__ import annotations

import json
import logging
import os

import boto3
from strands import tool

from src.aws.knowledge import read_documentation, search_documentation

logger = logging.getLogger(__name__)


@tool
def aws_knowledge_read(query: str) -> str:
    """Search AWS documentation for information related to the query.

    Args:
        query: A search phrase describing the AWS topic to research.

    Returns:
        JSON string with search results including titles, URLs, and page excerpts.
    """
    results = search_documentation(query, limit=3)
    if not results:
        return json.dumps({"results": [], "message": "No results found"})

    enriched = []
    for r in results:
        excerpt = ""
        url = r.get("url", "")
        if url:
            excerpt = read_documentation(url, max_length=4000)
        enriched.append(
            {
                "title": r.get("title", ""),
                "url": url,
                "excerpt": excerpt[:2000] if excerpt else r.get("context", r.get("text", "")),
            }
        )
    return json.dumps({"results": enriched}, ensure_ascii=False)


@tool
def write_notes(task_id: str, content: str) -> str:
    """Write research notes to S3 for a given task.

    Args:
        task_id: The unique task identifier.
        content: Markdown content to write as research notes.

    Returns:
        The S3 URI where the notes were stored.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return json.dumps({"error": "S3_BUCKET environment variable not set"})

    key = f"tasks/{task_id}/notes.md"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/markdown")
    s3_uri = f"s3://{bucket}/{key}"
    logger.info("Wrote research notes to %s", s3_uri)
    return json.dumps({"notes_path": s3_uri})


@tool
def memory_search(query: str) -> str:
    """Search past research memory for relevant information.

    Args:
        query: A search phrase to look up in memory.

    Returns:
        JSON string with matching memory entries (currently a stub).
    """
    # Stub: will be backed by a vector store or DynamoDB in a future phase
    return json.dumps({"results": [], "message": "Memory search not yet implemented"})
