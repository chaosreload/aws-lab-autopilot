"""Tests for Execute Agent tools and SafetyGuard extensions."""

import json
from unittest.mock import MagicMock

import pytest

from src.agents.execute.safety_guard import SafetyGuard


# ---------------------------------------------------------------------------
# SafetyGuard.pre_execute
# ---------------------------------------------------------------------------


class TestPreExecuteSafetyCheck:
    def test_open_cidr_blocked(self):
        guard = SafetyGuard()
        verdict = guard.pre_execute("aws ec2 authorize-security-group-ingress --cidr 0.0.0.0/0")
        assert verdict.allowed is False
        assert any("0.0.0.0/0" in v.detail for v in verdict.violations)

    def test_ipv6_open_cidr_blocked(self):
        guard = SafetyGuard()
        verdict = guard.pre_execute("aws ec2 authorize-security-group-ingress --cidr ::/0")
        assert verdict.allowed is False

    def test_safe_command_passes(self):
        guard = SafetyGuard()
        verdict = guard.pre_execute("aws s3 ls")
        assert verdict.allowed is True

    def test_blocked_service(self):
        guard = SafetyGuard()
        verdict = guard.pre_execute("aws organizations list-accounts")
        assert verdict.allowed is False
        assert any("organizations" in v.detail for v in verdict.violations)


# ---------------------------------------------------------------------------
# SafetyGuard.check_iam_action
# ---------------------------------------------------------------------------


class TestCheckIamAction:
    def test_create_user_blocked(self):
        guard = SafetyGuard()
        verdict = guard.check_iam_action("iam:CreateUser")
        assert verdict.allowed is False
        assert any("iam:CreateUser" in v.detail for v in verdict.violations)

    def test_safe_action_passes(self):
        guard = SafetyGuard()
        verdict = guard.check_iam_action("s3:CreateBucket")
        assert verdict.allowed is True

    def test_organizations_blocked(self):
        guard = SafetyGuard()
        verdict = guard.check_iam_action("organizations:ListAccounts")
        assert verdict.allowed is False


# ---------------------------------------------------------------------------
# aws_cli_execute tool
# ---------------------------------------------------------------------------


class TestAwsCliExecute:
    def test_blocked_by_safety(self):
        from src.agents.execute import tools

        mock_verdict = MagicMock()
        mock_verdict.allowed = False
        mock_verdict.summary = "BLOCKED"

        original = tools._safety_guard
        try:
            tools._safety_guard = MagicMock()
            tools._safety_guard.pre_execute.return_value = mock_verdict
            result = json.loads(tools.aws_cli_execute("aws ec2 authorize-security-group-ingress --cidr 0.0.0.0/0"))
            assert result["blocked"] is True
        finally:
            tools._safety_guard = original

    def test_unsupported_command(self):
        from src.agents.execute import tools

        # sns is in the allow-list but has no router implemented
        result = json.loads(tools.aws_cli_execute("aws sns list-topics"))
        assert "COMMAND_NOT_SUPPORTED" in result.get("error", "")

    def test_non_aws_command(self):
        from src.agents.execute import tools

        result = json.loads(tools.aws_cli_execute("ls -la"))
        assert "COMMAND_NOT_SUPPORTED" in result.get("error", "")


# ---------------------------------------------------------------------------
# iam_add_permission tool
# ---------------------------------------------------------------------------


class TestIamAddPermission:
    def test_blocked_action(self):
        from src.agents.execute import tools

        mock_verdict = MagicMock()
        mock_verdict.allowed = False
        mock_verdict.summary = "BLOCKED — iam:CreateUser denied"

        original = tools._safety_guard
        try:
            tools._safety_guard = MagicMock()
            tools._safety_guard.check_iam_action.return_value = mock_verdict
            result = json.loads(tools.iam_add_permission("test-role", "iam:CreateUser"))
            assert result["blocked"] is True
        finally:
            tools._safety_guard = original

    def test_allowed_action(self):
        from src.agents.execute import tools

        mock_verdict = MagicMock()
        mock_verdict.allowed = True

        mock_iam = MagicMock()
        original_guard = tools._safety_guard
        original_get_iam = tools._get_iam_manager
        try:
            tools._safety_guard = MagicMock()
            tools._safety_guard.check_iam_action.return_value = mock_verdict
            tools._get_iam_manager = lambda: mock_iam
            result = json.loads(tools.iam_add_permission("test-role", "s3:CreateBucket"))
            assert "Added" in result["status"]
        finally:
            tools._safety_guard = original_guard
            tools._get_iam_manager = original_get_iam


# ---------------------------------------------------------------------------
# track_resource tool
# ---------------------------------------------------------------------------


class TestTrackResource:
    def test_record_called(self):
        from src.agents.execute import tools

        mock_tracker = MagicMock()
        original = tools._get_resource_tracker
        try:
            tools._get_resource_tracker = lambda: mock_tracker
            result = json.loads(
                tools.track_resource(
                    "task-123",
                    "arn:aws:s3:::my-bucket",
                    "s3:bucket",
                    "us-east-1",
                )
            )
            assert "Tracked" in result["status"]
            mock_tracker.record.assert_called_once_with(
                task_id="task-123",
                resource_type="s3:bucket",
                resource_arn="arn:aws:s3:::my-bucket",
                region="us-east-1",
            )
        finally:
            tools._get_resource_tracker = original


# ---------------------------------------------------------------------------
# cleanup_resources tool
# ---------------------------------------------------------------------------


class TestCleanupResources:
    def test_cleanup(self):
        from src.agents.execute import tools

        mock_tracker = MagicMock()
        mock_tracker.mark_all_deleted.return_value = 3
        original = tools._get_resource_tracker
        try:
            tools._get_resource_tracker = lambda: mock_tracker
            result = json.loads(tools.cleanup_resources("task-123"))
            assert result["resources_cleaned"] == 3
        finally:
            tools._get_resource_tracker = original
