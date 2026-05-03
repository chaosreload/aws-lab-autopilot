"""Live smoke tests for get_regional_availability (requires network).

Run with: AUTOPILOT_LIVE_TESTS=1 pytest tests/aws/test_knowledge_service_mapping_live.py -v
"""

from __future__ import annotations

import os

import pytest

LIVE = os.environ.get("AUTOPILOT_LIVE_TESTS", "") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="AUTOPILOT_LIVE_TESTS not set")


def test_bedrock_regional_availability():
    from src.aws.knowledge import get_regional_availability

    try:
        result = get_regional_availability("bedrock", ["us-east-1", "us-west-2"])
    except Exception as exc:
        pytest.skip(f"Network call failed: {exc}")

    # The result should contain product availability info
    assert isinstance(result, (dict, list))
    text = str(result)
    assert "isAvailableIn" in text or "Amazon Bedrock" in text
