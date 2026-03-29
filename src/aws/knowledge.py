"""AWS Documentation search and read via aws-knowledge MCP server (JSON-RPC 2.0 over HTTP)."""

from __future__ import annotations

import json
import logging

import requests

logger = logging.getLogger(__name__)

MCP_ENDPOINT = "https://knowledge-mcp.global.api.aws"
_TIMEOUT = 30


def _call_mcp(method: str, params: dict) -> str:
    """Call a tool on the aws-knowledge MCP server and return the text content."""
    response = requests.post(
        MCP_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": method, "arguments": params},
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"MCP error: {result['error']}")
    content = result.get("result", {}).get("content", [])
    if content and content[0].get("type") == "text":
        return content[0]["text"]
    return ""


def _unwrap_mcp_text(text: str):
    """Unwrap the nested ``{"content": {"result": ...}}`` envelope from MCP responses."""
    try:
        parsed = json.loads(text) if isinstance(text, str) else text
        if isinstance(parsed, dict) and "content" in parsed:
            inner = parsed["content"]
            if isinstance(inner, dict) and "result" in inner:
                return inner["result"]
        return parsed
    except (json.JSONDecodeError, TypeError):
        return text


def search_documentation(query: str, limit: int = 5) -> list[dict]:
    """Search AWS documentation via MCP and return parsed results."""
    text = _call_mcp(
        "aws___search_documentation",
        {"search_phrase": query, "limit": limit},
    )
    result = _unwrap_mcp_text(text)
    if isinstance(result, list):
        return result
    if isinstance(result, str) and result:
        return [{"text": result}]
    return []


def read_documentation(url: str, max_length: int = 10000) -> str:
    """Read an AWS documentation page via MCP and return text content."""
    text = _call_mcp(
        "aws___read_documentation",
        {"url": url, "max_length": max_length},
    )
    result = _unwrap_mcp_text(text)
    return result if isinstance(result, str) else json.dumps(result)


def get_regional_availability(service: str, regions: list[str]) -> dict:
    """Check regional availability for an AWS service via MCP."""
    text = _call_mcp(
        "aws___get_regional_availability",
        {
            "resource_type": "product",
            "regions": regions,
            "filters": [service],
        },
    )
    result = _unwrap_mcp_text(text)
    if isinstance(result, (dict, list)):
        return result
    return {"text": result}
