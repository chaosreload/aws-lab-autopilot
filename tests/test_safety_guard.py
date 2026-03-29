"""Tests for SafetyGuard."""

import pytest

from src.agents.execute.safety_guard import (
    ALLOWED_SERVICES,
    DEFAULT_MAX_COST_USD,
    DEFAULT_MAX_RESOURCES,
    SafetyGuard,
)


@pytest.fixture
def guard() -> SafetyGuard:
    return SafetyGuard()


def _make_policy(*actions: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": list(actions), "Resource": "*"}],
    }


# ---------- Service allow-list ----------


class TestServiceAllowList:
    def test_allowed_services_pass(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            services=["s3", "lambda", "dynamodb"],
        )
        assert verdict.allowed is True
        assert verdict.violations == []

    def test_blocked_service_fails(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            services=["s3", "redshift"],
        )
        assert verdict.allowed is False
        assert any(v.category == "blocked_service" for v in verdict.violations)
        assert "redshift" in verdict.violations[0].detail

    def test_empty_services_pass(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("s3:GetObject"))
        assert verdict.allowed is True

    def test_service_case_insensitive(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            services=["S3", "Lambda"],
        )
        assert verdict.allowed is True


# ---------- Action deny-list ----------


class TestActionDenyList:
    def test_safe_actions_pass(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject", "s3:PutObject", "dynamodb:GetItem"),
        )
        assert verdict.allowed is True

    def test_wildcard_star_blocked(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("*"))
        assert verdict.allowed is False
        assert any("Wildcard" in v.detail for v in verdict.violations)

    def test_iam_create_user_blocked(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("iam:CreateUser"))
        assert verdict.allowed is False
        assert any("iam:CreateUser" in v.detail for v in verdict.violations)

    def test_organizations_blocked(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("organizations:DescribeOrganization"))
        assert verdict.allowed is False

    def test_ec2_run_instances_blocked(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("ec2:RunInstances"))
        assert verdict.allowed is False

    def test_action_string_not_list(self, guard: SafetyGuard):
        policy = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}],
        }
        verdict = guard.check(iam_policy=policy)
        assert verdict.allowed is True

    def test_empty_policy_passes(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy={"Version": "2012-10-17", "Statement": []})
        assert verdict.allowed is True

    def test_multiple_denied_actions_all_reported(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("iam:CreateUser", "iam:CreateAccessKey"),
        )
        assert verdict.allowed is False
        assert len(verdict.violations) == 2


# ---------- Cost limit ----------


class TestCostLimit:
    def test_under_cost_limit_passes(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            estimated_cost=4.99,
        )
        assert verdict.allowed is True

    def test_exact_cost_limit_passes(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            estimated_cost=DEFAULT_MAX_COST_USD,
        )
        assert verdict.allowed is True

    def test_over_cost_limit_fails(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            estimated_cost=DEFAULT_MAX_COST_USD + 0.01,
        )
        assert verdict.allowed is False
        assert any(v.category == "cost_limit" for v in verdict.violations)

    def test_custom_cost_limit(self):
        guard = SafetyGuard(max_cost_usd=10.0)
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            estimated_cost=7.0,
        )
        assert verdict.allowed is True


# ---------- Resource count limit ----------


class TestResourceLimit:
    def test_under_resource_limit_passes(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            resource_count=DEFAULT_MAX_RESOURCES,
        )
        assert verdict.allowed is True

    def test_over_resource_limit_fails(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            resource_count=DEFAULT_MAX_RESOURCES + 1,
        )
        assert verdict.allowed is False
        assert any(v.category == "resource_limit" for v in verdict.violations)

    def test_custom_resource_limit(self):
        guard = SafetyGuard(max_resources=5)
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            resource_count=6,
        )
        assert verdict.allowed is False


# ---------- Combined violations ----------


class TestCombinedViolations:
    def test_multiple_violation_types(self, guard: SafetyGuard):
        verdict = guard.check(
            iam_policy=_make_policy("iam:CreateUser"),
            services=["redshift"],
            estimated_cost=100.0,
            resource_count=999,
        )
        assert verdict.allowed is False
        categories = {v.category for v in verdict.violations}
        assert categories == {"denied_action", "blocked_service", "cost_limit", "resource_limit"}


# ---------- Verdict summary ----------


class TestVerdictSummary:
    def test_pass_summary(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("s3:GetObject"))
        assert "PASS" in verdict.summary

    def test_blocked_summary(self, guard: SafetyGuard):
        verdict = guard.check(iam_policy=_make_policy("iam:CreateUser"))
        assert "BLOCKED" in verdict.summary
        assert "violation" in verdict.summary


# ---------- Custom allowed services ----------


class TestCustomAllowedServices:
    def test_custom_services(self):
        guard = SafetyGuard(allowed_services=frozenset({"s3"}))
        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            services=["s3"],
        )
        assert verdict.allowed is True

        verdict = guard.check(
            iam_policy=_make_policy("s3:GetObject"),
            services=["lambda"],
        )
        assert verdict.allowed is False
