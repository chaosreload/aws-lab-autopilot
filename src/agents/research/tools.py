"""Research Agent tools — Strands @tool functions for documentation search, model listing, and note writing."""

from __future__ import annotations

import json
import logging
import os

import boto3
from strands import tool

from src.aws.knowledge import (
    get_regional_availability,
    read_documentation,
    search_documentation,
)

logger = logging.getLogger(__name__)


@tool
def aws_knowledge_read(query: str) -> str:
    """Search and read AWS official documentation.

    Args:
        query: Search query for AWS documentation.

    Returns:
        JSON string with search results containing title, url, and excerpt for each match.
    """
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


@tool
def aws_knowledge_region(service: str, regions: list[str] = None) -> str:
    """Query AWS service regional availability.

    Args:
        service: AWS service name (e.g. 'bedrock', 'aurora').
        regions: List of AWS regions to check. Defaults to us-east-1, us-west-2, ap-southeast-1.

    Returns:
        JSON string with regional availability data.
    """
    if regions is None:
        regions = ["us-east-1", "us-west-2", "ap-southeast-1"]
    result = get_regional_availability(service, regions)
    return json.dumps(result, ensure_ascii=False)


@tool
def list_bedrock_models(output_modality: str = None, provider: str = None) -> str:
    """List available Bedrock foundation models.

    Args:
        output_modality: Filter by output modality (e.g. 'TEXT', 'IMAGE', 'EMBEDDING').
        provider: Filter by provider name (e.g. 'Amazon', 'Anthropic', 'Meta').

    Returns:
        JSON string with list of models including modelId, modelName, status, and modalities.
    """
    client = boto3.client("bedrock", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    kwargs = {}
    if output_modality:
        kwargs["byOutputModality"] = output_modality
    if provider:
        kwargs["byProvider"] = provider
    resp = client.list_foundation_models(**kwargs)
    models = []
    for m in resp.get("modelSummaries", []):
        models.append({
            "modelId": m.get("modelId", ""),
            "modelName": m.get("modelName", ""),
            "providerName": m.get("providerName", ""),
            "modelLifecycle": m.get("modelLifecycle", {}).get("status", ""),
            "inputModalities": m.get("inputModalities", []),
            "outputModalities": m.get("outputModalities", []),
        })
    return json.dumps({"models": models}, ensure_ascii=False)


@tool
def write_notes(task_id: str, content: str) -> str:
    """Write research notes to S3.

    Args:
        task_id: The task identifier.
        content: Markdown content for the research notes.

    Returns:
        JSON string with the S3 path of the written notes.
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        raise RuntimeError("S3_BUCKET environment variable is not set")
    key = f"tasks/{task_id}/notes.md"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/markdown")
    s3_path = f"s3://{bucket}/{key}"
    logger.info("Wrote research notes to %s", s3_path)
    return json.dumps({"notes_path": s3_path})


@tool
def memory_search(query: str) -> str:
    """Search AgentCore Memory for historical pitfall records. (Phase 1 stub)

    Args:
        query: Search query for memory.

    Returns:
        JSON string with search results (empty in Phase 1).
    """
    return json.dumps({"results": [], "message": "Memory search not yet implemented"})
