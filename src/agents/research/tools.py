"""Strands tool definitions for the Research Agent."""

from __future__ import annotations

import json
import logging
import os

import boto3
from strands import tool

from src.aws.knowledge import get_regional_availability, read_documentation, search_documentation

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
def aws_knowledge_region(service: str, regions: list[str] = None) -> str:
    """Query AWS service availability in specified regions.

    Args:
        service: The AWS service name to check (e.g. "bedrock", "lambda").
        regions: List of AWS region codes. Defaults to us-east-1, us-west-2, ap-southeast-1.

    Returns:
        JSON string with regional availability information.
    """
    if regions is None:
        regions = ["us-east-1", "us-west-2", "ap-southeast-1"]
    result = get_regional_availability(service, regions)
    return json.dumps(result, ensure_ascii=False)


@tool
def list_bedrock_models(output_modality: str = None, provider: str = None) -> str:
    """List available Amazon Bedrock foundation models in the current AWS account/region.

    Use this tool to verify exact model IDs before including them in test_matrix.
    AWS documentation may reference outdated or preview model IDs that differ from
    what's actually deployed. Always call this for Bedrock model-related announcements.

    Args:
        output_modality: Filter by output type. One of: TEXT, IMAGE, EMBEDDING.
                         Leave empty to list all models.
        provider: Filter by provider name (e.g. "amazon", "anthropic", "cohere").
                  Leave empty to list all providers.

    Returns:
        JSON string with list of models: [{modelId, modelName, status, inputModalities, outputModalities}]
    """
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client("bedrock", region_name=region)

    kwargs: dict = {}
    if output_modality:
        kwargs["byOutputModality"] = output_modality.upper()
    if provider:
        kwargs["byProvider"] = provider

    try:
        response = client.list_foundation_models(**kwargs)
        models = [
            {
                "modelId": m.get("modelId", ""),
                "modelName": m.get("modelName", ""),
                "status": m.get("modelLifecycle", {}).get("status", "UNKNOWN"),
                "inputModalities": m.get("inputModalities", []),
                "outputModalities": m.get("outputModalities", []),
            }
            for m in response.get("modelSummaries", [])
        ]
        return json.dumps({"models": models, "count": len(models)}, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to list Bedrock models: %s", e)
        return json.dumps({"error": str(e), "models": []})


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
