"""Unit tests for SERVICE_DISPLAY_NAME mapping in src/aws/knowledge.py."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from src.aws.knowledge import get_regional_availability, SERVICE_DISPLAY_NAME


@pytest.fixture
def mock_call_mcp():
    """Patch _call_mcp to capture the arguments it receives."""
    with patch("src.aws.knowledge._call_mcp", return_value='{"content":{"result":{}}}') as m:
        yield m


class TestServiceMapping:
    def test_bedrock_maps_to_display_name(self, mock_call_mcp):
        get_regional_availability("bedrock", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["Amazon Bedrock"]

    def test_bedrock_case_insensitive(self, mock_call_mcp):
        get_regional_availability("BEDROCK", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["Amazon Bedrock"]

    def test_unknown_service_falls_back(self, mock_call_mcp, caplog):
        with caplog.at_level(logging.INFO, logger="src.aws.knowledge"):
            get_regional_availability("unknown-service-xyz", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["unknown-service-xyz"]
        assert "No SERVICE_DISPLAY_NAME mapping" in caplog.text

    def test_lambda_maps_correctly(self, mock_call_mcp):
        get_regional_availability("lambda", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["AWS Lambda"]

    def test_aurora_maps_correctly(self, mock_call_mcp):
        get_regional_availability("aurora", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["Amazon Aurora"]

    def test_s3_maps_correctly(self, mock_call_mcp):
        get_regional_availability("s3", ["us-east-1"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["Amazon S3"]

    def test_mixed_case_lambda(self, mock_call_mcp):
        get_regional_availability("Lambda", ["us-west-2"])
        args = mock_call_mcp.call_args[0]
        assert args[1]["filters"] == ["AWS Lambda"]
