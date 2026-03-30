"""Tests for Publish Agent tools."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.agents.publish.tools import (
    git_push,
    quality_check,
    write_article,
)


# ---------------------------------------------------------------------------
# quality_check
# ---------------------------------------------------------------------------

FULL_ARTICLE = """\
# AWS S3 Hands-on Lab

## 背景

本文基于官方文档（aws-knowledge 校准）介绍 S3 的使用。

## 前置条件

需要以下 IAM Policy / permission：

```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Action": ["s3:*"], "Resource": "*"}]
}
```

## 步骤

```bash
aws s3 mb s3://my-test-bucket
```

## 测试结果

| 测试项 | 结果 | 延迟 |
|--------|------|------|
| 创建桶 | pass | 0.6831s |

## 边界条件 / limit

桶名最长 63 字符。

## 踩坑 / pitfall

执行 `aws s3 cp` 时遇到 AccessDenied error，需要额外权限。

## 费用 / cost

约 $0.02，请及时清理 cleanup。
"""


class TestQualityCheckAllPass:
    def test_all_checks_pass(self):
        result = json.loads(quality_check(FULL_ARTICLE))
        assert result["passed"] is True
        assert result["failed_checks"] == []
        for check_name, check_val in result["checks"].items():
            assert check_val["pass"] is True, f"{check_name} should pass"
        assert result["blocking_issues"] == []


class TestQualityCheckMissingData:
    def test_missing_data(self):
        article = "# Title\n\nSome content without tables or data.\n"
        result = json.loads(quality_check(article))
        assert result["checks"]["has_data"]["pass"] is False
        assert "has_data" in result["failed_checks"]

    def test_placeholder_blocks(self):
        article = (
            "# Title\n\n"
            "```bash\necho hello\n```\n\n"
            "| col1 | col2 |\n|--|--|\n| a | 0.6831 |\n\n"
            "预期输出: xxx\n"
            "边界 limit\n费用 $1 cleanup\n"
            "## 踩坑 / pitfall\n\nGot error AccessDenied\n"
            "校准 aws-knowledge\niam policy\n"
        )
        result = json.loads(quality_check(article))
        assert result["checks"]["has_data"]["pass"] is False
        assert any("placeholder" in issue.lower() for issue in result["blocking_issues"])


class TestQualityCheckMissingIam:
    def test_missing_iam(self):
        article = (
            "# Title\n\n"
            "```bash\necho hello\n```\n\n"
            "| col1 | col2 |\n|--|--|\n| a | 0.6831 |\n\n"
            "边界 limit\n"
            "费用 $1 cleanup\n"
            "## 踩坑 / pitfall\n\nGot error AccessDenied\n"
            "校准 aws-knowledge\n"
        )
        result = json.loads(quality_check(article))
        assert result["checks"]["has_iam"]["pass"] is False
        assert "has_iam" in result["failed_checks"]


class TestQualityCheckPitfallSpeculative:
    def test_speculative_pitfall_fails(self):
        article = (
            "# Title\n\n"
            "```bash\necho hello\n```\n\n"
            "| col1 | col2 |\n|--|--|\n| a | 0.6831 |\n\n"
            "边界 limit\n费用 $1 cleanup\n"
            "## 踩坑 / pitfall\n\n可能会出现问题，建议注意。\n"
            "校准 aws-knowledge\niam policy\n"
        )
        result = json.loads(quality_check(article))
        assert result["checks"]["has_pitfall"]["pass"] is False
        assert any("speculative" in issue.lower() for issue in result["blocking_issues"])


# ---------------------------------------------------------------------------
# write_article
# ---------------------------------------------------------------------------


class TestWriteArticleS3Path:
    @patch("src.agents.publish.tools.boto3")
    def test_s3_path_format(self, mock_boto3):
        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        mock_boto3.resource.return_value = MagicMock()

        with patch.dict(os.environ, {"S3_BUCKET": "my-bucket", "TASKS_TABLE": "handson-tasks"}):
            result = json.loads(write_article("task-123", "# Article", "My Title"))
            assert result["article_path"] == "s3://my-bucket/tasks/task-123/article.md"
            mock_s3.put_object.assert_called_once()


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


class TestGitPushNoToken:
    def test_no_token_skips(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure GITHUB_TOKEN is not set
            os.environ.pop("GITHUB_TOKEN", None)
            result = json.loads(git_push("# content", "docs/test.md", "test commit"))
            assert "skipping push" in result["message"].lower() or "not configured" in result["message"].lower()
