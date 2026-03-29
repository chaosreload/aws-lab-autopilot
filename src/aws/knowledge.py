"""AWS Documentation search and read — lightweight wrapper using the public AWS docs search API."""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

SEARCH_API_URL = "https://proxy.search.docs.aws.com/search"
DOCS_BASE = "https://docs.aws.amazon.com"
_TIMEOUT = 15


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor for AWS doc pages."""

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._pieces.append(text)

    def get_text(self) -> str:
        return "\n".join(self._pieces)


def search_documentation(query: str, limit: int = 5) -> list[dict]:
    """Search AWS documentation and return a list of ``{url, title, context}`` dicts."""
    body = {
        "textQuery": {"input": query},
        "contextAttributes": [{"key": "domain", "value": "docs.aws.amazon.com"}],
        "acceptSuggestionBody": "RawText",
        "locales": ["en_us"],
    }
    try:
        resp = requests.post(SEARCH_API_URL, json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("AWS docs search failed for query=%s", query)
        return []

    results: list[dict] = []
    for item in data.get("searchResults", [])[:limit]:
        results.append(
            {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "context": item.get("context", ""),
            }
        )
    return results


def read_documentation(url: str, max_chars: int = 8000) -> str:
    """Fetch an AWS documentation page and return plain-text content (truncated)."""
    if not re.match(r"^https?://docs\.aws\.amazon\.com/", url):
        return f"Invalid URL: {url}. Must be from docs.aws.amazon.com"

    try:
        resp = requests.get(
            url,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; aws-lab-autopilot/0.1)"
            },
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Failed to fetch %s", url)
        return f"Error fetching {url}"

    extractor = _TextExtractor()
    extractor.feed(resp.text)
    text = extractor.get_text()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text
