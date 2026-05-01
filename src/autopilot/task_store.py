"""Task store — DynamoDB-backed persistence for autopilot tasks.

Thin wrapper around boto3 that owns the task lifecycle events:
  - create_task(url)
  - get_task(task_id)
  - update_status(task_id, status, phase=None, current_step=None)
  - heartbeat(task_id, phase, current_step, last_evidence_path=None)  # Phase 2 Gap 8
  - save_research_result(task_id, result)
  - list_by_status(status, limit=20)

Uses DynamoDB JSON marshalling via boto3.dynamodb.types.TypeSerializer so that
the stored schema maps 1:1 with pydantic dumps. That keeps the code simple and
keeps the door open to migrate to a real managed table later without changing
call sites.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

TABLE_NAME = os.environ.get("AUTOPILOT_TABLE", "autopilot-tasks")
ENDPOINT = os.environ.get("DDB_ENDPOINT", "http://localhost:8001")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _client():
    kwargs = {"region_name": REGION}
    if ENDPOINT:
        kwargs["endpoint_url"] = ENDPOINT
        kwargs["aws_access_key_id"] = "local"
        kwargs["aws_secret_access_key"] = "local"
    return boto3.client("dynamodb", **kwargs)


def _marshal(data: dict) -> dict:
    """Convert a plain Python dict into a DynamoDB AttributeValue map."""
    return {k: _serializer.serialize(v) for k, v in data.items() if v is not None}


def _unmarshal(item: dict) -> dict:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def _to_ddb_compatible(value: Any) -> Any:
    """Recursively convert a value into a DynamoDB-serializable shape.

    DynamoDB's TypeSerializer refuses Python float and bans empty string for
    String attributes in older APIs. We normalize ahead of time:
      - float -> Decimal (DDB stores as Number; Stage 1 budget_limit_usd / ttl_hours need this)
      - dict / list: recurse
      - everything else: return as-is

    NaN / Infinity become "0" Decimals to avoid blowing up the request;
    Research Agent should never produce those but we defensively guard here.
    """
    if isinstance(value, float):
        # Decimal(float) introduces binary precision noise; go through str().
        if value != value or value in (float("inf"), float("-inf")):
            return Decimal("0")
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb_compatible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb_compatible(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

def create_task(url: str, task_type: Optional[str] = None, aws_identity: Optional[dict] = None) -> dict:
    """Create a new queued task and return the full record.

    aws_identity (optional) should be the sts:GetCallerIdentity result for the
    business credentials that will run the task, captured at submit time.
    Keeps a trail of which IAM principal was intended to run each task.
    """
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    item = {
        "task_id": task_id,
        "status": "queued",
        "phase": "pending",
        "current_step": "",
        "url": url,
        "task_type": task_type or "",
        "created_at": now,
        "updated_at": now,
        "heartbeat_at": now,
    }
    if aws_identity:
        item["aws_identity"] = aws_identity
    _client().put_item(TableName=TABLE_NAME, Item=_marshal(item))
    return item


def get_task(task_id: str) -> Optional[dict]:
    resp = _client().get_item(
        TableName=TABLE_NAME,
        Key={"task_id": {"S": task_id}},
    )
    item = resp.get("Item")
    return _unmarshal(item) if item else None


def update_status(
    task_id: str,
    status: str,
    phase: Optional[str] = None,
    current_step: Optional[str] = None,
) -> None:
    """Atomic status/phase update with updated_at + heartbeat_at auto-set."""
    now = _now_iso()
    names = {"#s": "status", "#ua": "updated_at", "#hb": "heartbeat_at"}
    values = {
        ":s": {"S": status},
        ":ua": {"S": now},
        ":hb": {"S": now},
    }
    set_parts = ["#s = :s", "#ua = :ua", "#hb = :hb"]
    if phase is not None:
        names["#p"] = "phase"
        values[":p"] = {"S": phase}
        set_parts.append("#p = :p")
    if current_step is not None:
        names["#cs"] = "current_step"
        values[":cs"] = {"S": current_step}
        set_parts.append("#cs = :cs")

    _client().update_item(
        TableName=TABLE_NAME,
        Key={"task_id": {"S": task_id}},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def heartbeat(
    task_id: str,
    phase: str,
    current_step: str,
    last_evidence_path: Optional[str] = None,
) -> None:
    """Phase 2 Gap 8: write a heartbeat record so EventBridge can detect stuck tasks.

    Intentionally idempotent — multiple calls overwrite the same fields.
    """
    now = _now_iso()
    names = {
        "#hb": "heartbeat_at",
        "#p": "phase",
        "#cs": "current_step",
        "#ua": "updated_at",
    }
    values = {
        ":hb": {"S": now},
        ":p": {"S": phase},
        ":cs": {"S": current_step},
        ":ua": {"S": now},
    }
    set_parts = ["#hb = :hb", "#p = :p", "#cs = :cs", "#ua = :ua"]
    if last_evidence_path is not None:
        names["#lep"] = "last_evidence_path"
        values[":lep"] = {"S": last_evidence_path}
        set_parts.append("#lep = :lep")

    _client().update_item(
        TableName=TABLE_NAME,
        Key={"task_id": {"S": task_id}},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def save_research_result(task_id: str, result: dict) -> None:
    """Persist full ResearchResult dict under `research_result` attribute,
    plus extract a few top-level convenience fields for fast filtering.

    Stage 1: also persists `environment` sub-dict at top level and promotes
    a handful of environment fields (region, region_reason, budget_limit_usd,
    tag_strategy) so downstream EventBridge / orphan scanner / filters can
    query them without unpacking nested maps.
    """
    now = _now_iso()
    result = _to_ddb_compatible(result)
    names = {
        "#rr": "research_result",
        "#ua": "updated_at",
        "#hb": "heartbeat_at",
    }
    values = {
        ":rr": _serializer.serialize(result),
        ":ua": {"S": now},
        ":hb": {"S": now},
    }
    set_parts = ["#rr = :rr", "#ua = :ua", "#hb = :hb"]

    for key in ("notes_path", "task_type", "task_type_reason"):
        if key in result and result[key] is not None:
            alias = f"#k_{key}"
            val_alias = f":v_{key}"
            names[alias] = key
            values[val_alias] = _serializer.serialize(result[key])
            set_parts.append(f"{alias} = {val_alias}")

    # Stage 1 §5/§8: promote post_parser_warnings to top level when non-empty
    warnings = result.get("post_parser_warnings")
    if warnings:
        names["#ppw"] = "post_parser_warnings"
        values[":ppw"] = _serializer.serialize(warnings)
        set_parts.append("#ppw = :ppw")

    # Stage 1 environment promotion
    env = result.get("environment") or {}
    # region / region_reason: prefer environment.*, fall back to top-level
    # (Stage 0 used to emit them at top level; keep the fallback so an older
    # Research Agent output still round-trips without losing data).
    region = env.get("region") or result.get("region")
    region_reason = env.get("region_reason") or result.get("region_reason")
    if region:
        names["#reg"] = "region"
        values[":reg"] = {"S": region}
        set_parts.append("#reg = :reg")
    if region_reason:
        names["#rreg"] = "region_reason"
        values[":rreg"] = {"S": region_reason}
        set_parts.append("#rreg = :rreg")

    if env:
        names["#env"] = "environment"
        values[":env"] = _serializer.serialize(env)
        set_parts.append("#env = :env")

        budget = env.get("budget_limit_usd")
        if budget is not None:
            names["#bdg"] = "budget_limit_usd"
            values[":bdg"] = _serializer.serialize(budget)
            set_parts.append("#bdg = :bdg")

        tag_strategy = env.get("tag_strategy")
        if tag_strategy:
            names["#tag"] = "tag_strategy"
            values[":tag"] = _serializer.serialize(tag_strategy)
            set_parts.append("#tag = :tag")

    _client().update_item(
        TableName=TABLE_NAME,
        Key={"task_id": {"S": task_id}},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def save_error(task_id: str, message: str, where: str = "") -> None:
    now = _now_iso()
    _client().update_item(
        TableName=TABLE_NAME,
        Key={"task_id": {"S": task_id}},
        UpdateExpression=(
            "SET #s = :s, #err = :err, #ua = :ua, #hb = :hb"
        ),
        ExpressionAttributeNames={
            "#s": "status",
            "#err": "error",
            "#ua": "updated_at",
            "#hb": "heartbeat_at",
        },
        ExpressionAttributeValues={
            ":s": {"S": "failed"},
            ":err": _serializer.serialize({"message": message, "where": where}),
            ":ua": {"S": now},
            ":hb": {"S": now},
        },
    )


def list_by_status(status: str, limit: int = 20) -> list[dict]:
    resp = _client().query(
        TableName=TABLE_NAME,
        IndexName="status-created_at-index",
        KeyConditionExpression="#s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": {"S": status}},
        ScanIndexForward=False,  # newest first
        Limit=limit,
    )
    return [_unmarshal(item) for item in resp.get("Items", [])]


def list_all(limit: int = 50) -> list[dict]:
    resp = _client().scan(TableName=TABLE_NAME, Limit=limit)
    items = [_unmarshal(i) for i in resp.get("Items", [])]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items
