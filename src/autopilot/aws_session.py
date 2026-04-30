"""Centralized AWS session / credentials factory for aws-lab-autopilot.

Supports three credential sources, in priority order:
  1. Explicit access keys (env: AUTOPILOT_AWS_ACCESS_KEY_ID + AUTOPILOT_AWS_SECRET_ACCESS_KEY [+ AUTOPILOT_AWS_SESSION_TOKEN])
  2. Assume role (env: AUTOPILOT_AWS_ROLE_ARN [+ AUTOPILOT_AWS_ROLE_SESSION_NAME])
  3. Named profile (env: AUTOPILOT_AWS_PROFILE, e.g. "weichaol-testenv2-awswhatsnewtest")
  4. Default credential chain (fallback; only for local dev / dev-server instance role)

Intentionally NOT used for DynamoDB Local (task_store / ddb_schema), which has
its own dummy-credentialed client for the local endpoint. Use this factory for
all *business* AWS calls (Bedrock, S3, IAM, EC2, S3 Files, etc).

Usage:
    from src.autopilot.aws_session import get_boto3_session, who_am_i

    session = get_boto3_session()
    s3 = session.client("s3")

    identity = who_am_i()   # {"Account": ..., "Arn": ..., "UserId": ..., "credential_source": "profile:weichaol-..."}
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import boto3
from botocore.credentials import CredentialResolver

logger = logging.getLogger("autopilot.aws_session")


# ---------------------------------------------------------------------------
# Env keys (all optional; choose one source)
# ---------------------------------------------------------------------------

ENV_PROFILE = "AUTOPILOT_AWS_PROFILE"
ENV_ROLE_ARN = "AUTOPILOT_AWS_ROLE_ARN"
ENV_ROLE_SESSION = "AUTOPILOT_AWS_ROLE_SESSION_NAME"
ENV_ACCESS_KEY = "AUTOPILOT_AWS_ACCESS_KEY_ID"
ENV_SECRET_KEY = "AUTOPILOT_AWS_SECRET_ACCESS_KEY"
ENV_SESSION_TOKEN = "AUTOPILOT_AWS_SESSION_TOKEN"
ENV_REGION = "AUTOPILOT_AWS_REGION"

DEFAULT_REGION = os.environ.get(ENV_REGION, "us-east-1")


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

_cached_session: Optional[boto3.Session] = None
_credential_source: Optional[str] = None


def _build_session() -> tuple[boto3.Session, str]:
    """Build a fresh boto3.Session based on env configuration.

    Returns:
        (session, credential_source_label)
    """
    # 1. Explicit access keys (highest priority — e.g. CI, ephemeral creds)
    ak = os.environ.get(ENV_ACCESS_KEY)
    sk = os.environ.get(ENV_SECRET_KEY)
    if ak and sk:
        st = os.environ.get(ENV_SESSION_TOKEN)
        session = boto3.Session(
            aws_access_key_id=ak,
            aws_secret_access_key=sk,
            aws_session_token=st,
            region_name=DEFAULT_REGION,
        )
        source = "access_key:" + ak[:8] + "…"
        return session, source

    # 2. Assume role (use sts on top of a source session)
    role_arn = os.environ.get(ENV_ROLE_ARN)
    if role_arn:
        role_session_name = os.environ.get(ENV_ROLE_SESSION, "autopilot-session")
        base_session = boto3.Session(region_name=DEFAULT_REGION)
        sts = base_session.client("sts")
        creds = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=role_session_name,
        )["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=DEFAULT_REGION,
        )
        source = f"role:{role_arn}"
        return session, source

    # 3. Named profile
    profile = os.environ.get(ENV_PROFILE)
    if profile:
        session = boto3.Session(profile_name=profile, region_name=DEFAULT_REGION)
        source = f"profile:{profile}"
        return session, source

    # 4. Default credential chain (dev-server instance role, etc.)
    session = boto3.Session(region_name=DEFAULT_REGION)
    source = "default_chain"
    return session, source


def get_boto3_session(force_refresh: bool = False) -> boto3.Session:
    """Return (and cache) the autopilot-wide boto3 Session.

    Call with `force_refresh=True` after rotating env credentials (test only).
    """
    global _cached_session, _credential_source
    if _cached_session is None or force_refresh:
        _cached_session, _credential_source = _build_session()
        logger.info("AWS session initialized via %s (region=%s)",
                    _credential_source, DEFAULT_REGION)
    return _cached_session


def get_credential_source() -> str:
    """Return a short label describing how the current session got its creds."""
    if _cached_session is None:
        get_boto3_session()
    return _credential_source or "unknown"


def who_am_i() -> dict:
    """Call sts:GetCallerIdentity for the current session.

    Returns dict with Account, Arn, UserId, plus `credential_source` label.
    Safe to call from both task_store and API handlers.
    """
    session = get_boto3_session()
    try:
        sts = session.client("sts")
        resp = sts.get_caller_identity()
        return {
            "Account": resp.get("Account"),
            "Arn": resp.get("Arn"),
            "UserId": resp.get("UserId"),
            "credential_source": get_credential_source(),
            "region": DEFAULT_REGION,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "credential_source": get_credential_source(),
            "region": DEFAULT_REGION,
        }
