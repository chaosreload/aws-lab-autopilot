"""B4.b — strict V7 unit test (Stage 1 spec §5.1).

Verifies `_apply_post_parser` overrides a forged `environment.account_id` with
the caller identity Account from `aws_identity`, and surfaces the rewrite via
`post_parser_warnings`.

Run:
    cd /data/projects/chaosreload/study/repo/chaosreload/aws-lab-autopilot
    .venv/bin/python -m pytest memory/b4-validation/test_v7_strict.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make src.* importable when running from project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.research.agent import _apply_post_parser  # noqa: E402


CALLER_ACCOUNT = "595842667825"
FORGED_ACCOUNT = "000000000000"


def _base_parsed(account_id: str) -> dict:
    """Minimal verdict=go payload that passes TestEnvironment validation."""
    return {
        "task_id": "task-v7-strict-test",
        "verdict": "go",
        "task_type": "api_call",
        "environment": {
            "region": "us-east-1",
            "region_reason": "fixed for test",
            "account_id": account_id,
            "vpc_preference": "none",
            "tag_strategy": {
                "autopilot:task_id": "task-v7-strict-test",
                "autopilot:stage": "execute",
                "autopilot:owner": "archie",
            },
            "budget_limit_usd": 1.0,
            "cleanup_policy": {
                "ttl_hours": 0.5,
                "on_failure": "terminate_all",
                "orphan_scan": True,
            },
            "prerequisites": [],
        },
    }


def test_v7_forged_account_id_is_overwritten():
    """Core V7: agent emits wrong account_id -> post-parser rewrites it."""
    parsed = _base_parsed(FORGED_ACCOUNT)
    aws_identity = {"Account": CALLER_ACCOUNT, "Arn": "arn:aws:iam::595842667825:user/test"}

    result = _apply_post_parser(parsed, aws_identity)

    assert result["environment"]["account_id"] == CALLER_ACCOUNT, (
        f"expected account_id={CALLER_ACCOUNT}, got {result['environment']['account_id']!r}"
    )
    assert result["verdict"] == "go", "valid env after rewrite must stay go"

    warnings = result.get("post_parser_warnings") or []
    assert any(
        FORGED_ACCOUNT in w and CALLER_ACCOUNT in w and "overriding" in w.lower()
        for w in warnings
    ), f"expected override warning referencing both ids; got: {warnings}"


def test_v7_correct_account_id_is_not_rewritten():
    """Sanity: when Agent already emits the right account_id, no warning fires."""
    parsed = _base_parsed(CALLER_ACCOUNT)
    aws_identity = {"Account": CALLER_ACCOUNT, "Arn": "arn:aws:iam::595842667825:user/test"}

    result = _apply_post_parser(parsed, aws_identity)

    assert result["environment"]["account_id"] == CALLER_ACCOUNT
    warnings = result.get("post_parser_warnings") or []
    assert not any("overriding" in w.lower() and "account_id" in w.lower() for w in warnings), (
        f"no override expected; got: {warnings}"
    )


def test_v7_missing_account_id_is_filled():
    """Edge case: empty string account_id is still overwritten to caller id."""
    parsed = _base_parsed("")
    aws_identity = {"Account": CALLER_ACCOUNT, "Arn": "arn:aws:iam::595842667825:user/test"}

    result = _apply_post_parser(parsed, aws_identity)

    assert result["environment"]["account_id"] == CALLER_ACCOUNT
    warnings = result.get("post_parser_warnings") or []
    assert any("overriding" in w.lower() for w in warnings), (
        f"expected override warning; got: {warnings}"
    )


if __name__ == "__main__":
    # Allow running without pytest for quick smoke.
    for fn in [
        test_v7_forged_account_id_is_overwritten,
        test_v7_correct_account_id_is_not_rewritten,
        test_v7_missing_account_id_is_filled,
    ]:
        fn()
        print(f"PASS {fn.__name__}")
    print("OK all 3")
