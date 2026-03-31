"""Tests for Research Agent tools and agent runner."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestWriteNotes:
    """Test write_notes tool S3 path format and behaviour."""

    @patch("src.agents.research.tools.boto3")
    @patch.dict("os.environ", {"S3_BUCKET": "my-test-bucket"})
    def test_write_notes_s3_path(self, mock_boto3):
        from src.agents.research.tools import write_notes

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        result_json = write_notes(task_id="task-123", content="# Notes\nSome content")
        result = json.loads(result_json)

        assert result["notes_path"] == "s3://my-test-bucket/tasks/task-123/notes.md"
        mock_s3.put_object.assert_called_once_with(
            Bucket="my-test-bucket",
            Key="tasks/task-123/notes.md",
            Body=b"# Notes\nSome content",
            ContentType="text/markdown",
        )

    @patch("src.agents.research.tools.boto3")
    @patch.dict("os.environ", {"S3_BUCKET": "another-bucket"})
    def test_write_notes_different_task_id(self, mock_boto3):
        from src.agents.research.tools import write_notes

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        result_json = write_notes(task_id="abc-def-456", content="test")
        result = json.loads(result_json)

        assert result["notes_path"] == "s3://another-bucket/tasks/abc-def-456/notes.md"

    @patch.dict("os.environ", {}, clear=False)
    def test_write_notes_no_bucket_env(self):
        import os
        os.environ.pop("S3_BUCKET", None)

        from src.agents.research.tools import write_notes

        result_json = write_notes(task_id="task-123", content="test")
        result = json.loads(result_json)

        assert "error" in result
        assert "S3_BUCKET" in result["error"]


class TestMemorySearch:
    def test_memory_search_returns_empty(self):
        from src.agents.research.tools import memory_search

        result_json = memory_search(query="anything")
        result = json.loads(result_json)

        assert result["results"] == []


class TestAwsKnowledgeRead:
    @patch("src.agents.research.tools.read_documentation")
    @patch("src.agents.research.tools.search_documentation")
    def test_returns_enriched_results(self, mock_search, mock_read):
        from src.agents.research.tools import aws_knowledge_read

        mock_search.return_value = [
            {"url": "https://docs.aws.amazon.com/s3/latest/userguide/example.html", "title": "S3 Example", "context": "ctx"},
        ]
        mock_read.return_value = "S3 documentation content here"

        result_json = aws_knowledge_read(query="s3 bucket")
        result = json.loads(result_json)

        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "S3 Example"
        assert "S3 documentation content" in result["results"][0]["excerpt"]
        mock_read.assert_called_once_with(
            "https://docs.aws.amazon.com/s3/latest/userguide/example.html",
            max_length=4000,
        )

    @patch("src.agents.research.tools.search_documentation")
    def test_no_results(self, mock_search):
        from src.agents.research.tools import aws_knowledge_read

        mock_search.return_value = []

        result_json = aws_knowledge_read(query="nonexistent")
        result = json.loads(result_json)

        assert result["results"] == []

    @patch("src.agents.research.tools.read_documentation")
    @patch("src.agents.research.tools.search_documentation")
    def test_fallback_to_text_field(self, mock_search, mock_read):
        """When MCP search returns results with 'text' instead of 'context', use that as fallback."""
        from src.agents.research.tools import aws_knowledge_read

        mock_search.return_value = [
            {"url": "", "title": "Fallback", "text": "fallback content"},
        ]

        result_json = aws_knowledge_read(query="test")
        result = json.loads(result_json)

        assert result["results"][0]["excerpt"] == "fallback content"
        mock_read.assert_not_called()


class TestRunResearch:
    @patch("src.agents.research.agent._create_agent")
    def test_run_research_parses_json(self, mock_create):
        from src.agents.research.agent import run_research

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.__str__ = lambda self: json.dumps({
            "verdict": "go",
            "notes_path": "s3://bucket/tasks/t1/notes.md",
            "test_matrix": [{"id": "T1", "name": "basic", "priority": "P0"}],
            "iam_policy": {"Version": "2012-10-17", "Statement": []},
            "services": ["s3"],
        })
        mock_agent.return_value = mock_result
        mock_create.return_value = mock_agent

        result = run_research("t1", "https://docs.aws.amazon.com/example.html")

        assert result["verdict"] == "go"
        assert result["services"] == ["s3"]
        assert len(result["test_matrix"]) == 1

    @patch("src.agents.research.agent._create_agent")
    def test_run_research_handles_non_json(self, mock_create):
        from src.agents.research.agent import run_research

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.__str__ = lambda self: "Some text before {\"verdict\": \"skip\", \"notes_path\": \"\", \"test_matrix\": [], \"iam_policy\": {}, \"services\": []} and after"
        mock_agent.return_value = mock_result
        mock_create.return_value = mock_agent

        result = run_research("t2", "https://docs.aws.amazon.com/example.html")

        assert result["verdict"] == "skip"

    @patch("src.agents.research.agent._create_agent")
    def test_run_research_handles_garbage(self, mock_create):
        from src.agents.research.agent import run_research

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.__str__ = lambda self: "completely unparseable"
        mock_agent.return_value = mock_result
        mock_create.return_value = mock_agent

        result = run_research("t3", "https://docs.aws.amazon.com/example.html")

        assert result["verdict"] == "skip"
        assert "error" in result


class TestListBedrockModels:
    @patch("src.agents.research.tools.boto3")
    @patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"})
    def test_returns_models(self, mock_boto3):
        from src.agents.research.tools import list_bedrock_models

        mock_bedrock = MagicMock()
        mock_boto3.client.return_value = mock_bedrock
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                {
                    "modelId": "amazon.nova-2-multimodal-embeddings-v1:0",
                    "modelName": "Amazon Nova Multimodal Embeddings",
                    "modelLifecycle": {"status": "ACTIVE"},
                    "inputModalities": ["TEXT", "IMAGE"],
                    "outputModalities": ["EMBEDDING"],
                },
            ]
        }

        result_json = list_bedrock_models(output_modality="EMBEDDING", provider="amazon")
        result = json.loads(result_json)

        assert result["count"] == 1
        assert result["models"][0]["modelId"] == "amazon.nova-2-multimodal-embeddings-v1:0"
        assert result["models"][0]["status"] == "ACTIVE"
        mock_bedrock.list_foundation_models.assert_called_once_with(
            byOutputModality="EMBEDDING", byProvider="amazon"
        )

    @patch("src.agents.research.tools.boto3")
    def test_handles_api_error(self, mock_boto3):
        from src.agents.research.tools import list_bedrock_models

        mock_bedrock = MagicMock()
        mock_boto3.client.return_value = mock_bedrock
        mock_bedrock.list_foundation_models.side_effect = Exception("AccessDenied")

        result_json = list_bedrock_models()
        result = json.loads(result_json)

        assert "error" in result
        assert result["models"] == []

    @patch("src.agents.research.tools.boto3")
    def test_no_filters(self, mock_boto3):
        from src.agents.research.tools import list_bedrock_models

        mock_bedrock = MagicMock()
        mock_boto3.client.return_value = mock_bedrock
        mock_bedrock.list_foundation_models.return_value = {"modelSummaries": []}

        result_json = list_bedrock_models()
        result = json.loads(result_json)

        assert result["count"] == 0
        mock_bedrock.list_foundation_models.assert_called_once_with()
