"""API Gateway Lambda handler — task CRUD operations."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from pydantic import ValidationError

from src.api.models import (
    CreateTaskRequest,
    CreateTaskResponse,
    ErrorResponse,
    TaskResultResponse,
    TaskStatusResponse,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sfn = boto3.client("stepfunctions")

TASKS_TABLE = os.environ.get("TASKS_TABLE", "handson-tasks")
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _get_table():
    return dynamodb.Table(TASKS_TABLE)


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    try:
        if method == "GET" and path == "/tasks":
            return _list_tasks(event)
        elif method == "POST" and path == "/tasks":
            return _create_task(event)
        elif method == "POST" and path == "/tasks/batch":
            return _create_batch(event)
        elif method == "POST" and path.startswith("/tasks/") and path.endswith("/approve"):
            task_id = path.split("/")[2]
            return _approve_task(task_id)
        elif method == "GET" and path.startswith("/tasks/") and path.endswith("/result"):
            task_id = path.split("/")[2]
            return _get_task_result(task_id)
        elif method == "GET" and path.startswith("/tasks/"):
            task_id = path.split("/")[2]
            return _get_task_status(task_id)
        elif method == "DELETE" and path.startswith("/tasks/"):
            task_id = path.split("/")[2]
            return _delete_task(task_id)
        else:
            return _json_response(404, {"error": "Not Found"})
    except Exception:
        logger.exception("Unhandled error")
        return _json_response(500, {"error": "Internal Server Error"})


def _create_task(event):
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")

    try:
        req = CreateTaskRequest.model_validate_json(body)
    except ValidationError as e:
        return _json_response(400, ErrorResponse(error="Validation error", detail=str(e)).model_dump())

    now = datetime.now(timezone.utc)
    task_id = str(uuid.uuid4())
    created_at = now.isoformat()

    item = {
        "task_id": task_id,
        "url": str(req.url),
        "state": "queued",
        "rework_count": 0,
        "created_at": created_at,
        "created_date": now.strftime("%Y-%m-%d"),
        "updated_at": created_at,
    }
    if req.callback_url:
        item["callback_url"] = str(req.callback_url)
    if req.notify_slack:
        item["notify_slack"] = req.notify_slack
    if req.config_override:
        item["config_override"] = req.config_override

    table = _get_table()
    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(task_id)",
    )

    sfn_input = {
        "task_id": task_id,
        "url": str(req.url),
        "rework_count": 0,
    }
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=task_id,
        input=json.dumps(sfn_input),
    )

    resp = CreateTaskResponse(
        task_id=task_id,
        state="queued",
        created_at=created_at,
        estimated_duration="~30 min",
    )
    return _json_response(202, resp.model_dump())


def _get_task_status(task_id: str):
    table = _get_table()
    result = table.get_item(
        Key={"task_id": task_id},
        ProjectionExpression="task_id, #u, #s, rework_count, created_at, updated_at",
        ExpressionAttributeNames={"#s": "state", "#u": "url"},
    )
    item = result.get("Item")
    if not item:
        return _json_response(404, {"error": "Task not found"})

    state = item.get("state", "unknown")
    progress = _infer_progress(state, item)

    resp = TaskStatusResponse(
        task_id=item["task_id"],
        url=item.get("url", ""),
        state=state,
        rework_count=int(item.get("rework_count", 0)),
        progress=progress,
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )
    return _json_response(200, resp.model_dump())


def _infer_progress(state: str, item: dict) -> dict:
    stages = ["queued", "researching", "executing", "publishing", "completed"]
    stage_map = {
        "queued": 0,
        "researching": 1,
        "research_done": 1,
        "executing_explore": 2,
        "executing_verify": 2,
        "execute_done": 2,
        "publishing": 3,
        "completed": 4,
        "skipped": 4,
        "failed": -1,
        "needs_human": -1,
        "cancelled": -1,
    }
    idx = stage_map.get(state, 0)
    pct = max(0, int(idx / (len(stages) - 1) * 100)) if idx >= 0 else 0
    return {
        "current_stage": state,
        "stages": stages,
        "percent": pct,
        "rework_count": int(item.get("rework_count", 0)),
    }


def _get_task_result(task_id: str):
    table = _get_table()
    result = table.get_item(Key={"task_id": task_id})
    item = result.get("Item")
    if not item:
        return _json_response(404, {"error": "Task not found"})

    publish_result = item.get("publish_result") or {}
    preview_url = item.get("preview_url") or publish_result.get("preview_url")

    resp = TaskResultResponse(
        task_id=item["task_id"],
        state=item.get("state", "unknown"),
        url=item.get("url", ""),
        research_result=item.get("research_result"),
        execute_result=item.get("execute_result"),
        publish_result=item.get("publish_result"),
        preview_url=preview_url,
        published_url=item.get("published_url"),
        test_results=item.get("test_results"),
        rework_count=int(item.get("rework_count", 0)),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )
    return _json_response(200, resp.model_dump())


def _list_tasks(event):
    qs = event.get("queryStringParameters") or {}
    state_filter = qs.get("state")
    table = _get_table()

    if state_filter:
        from boto3.dynamodb.conditions import Key as DDBKey
        resp = table.query(
            IndexName="state-index",
            KeyConditionExpression=DDBKey("state").eq(state_filter),
        )
    else:
        resp = table.scan()

    items = resp.get("Items", [])
    tasks = [
        {
            "task_id": it["task_id"],
            "url": it.get("url", ""),
            "state": it.get("state", "unknown"),
            "created_at": it.get("created_at", ""),
        }
        for it in items
    ]
    return _json_response(200, {"tasks": tasks})


def _create_batch(event):
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")

    data = json.loads(body)
    urls = data.get("urls", [])
    if not urls:
        return _json_response(400, {"error": "urls list is required"})

    table = _get_table()
    results = []
    for url_str in urls:
        now = datetime.now(timezone.utc)
        task_id = str(uuid.uuid4())
        created_at = now.isoformat()

        item = {
            "task_id": task_id,
            "url": url_str,
            "state": "queued",
            "rework_count": 0,
            "created_at": created_at,
            "created_date": now.strftime("%Y-%m-%d"),
            "updated_at": created_at,
        }
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(task_id)",
        )

        sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=task_id,
            input=json.dumps({"task_id": task_id, "url": url_str, "rework_count": 0}),
        )
        results.append({"task_id": task_id, "state": "queued"})

    return _json_response(202, {"tasks": results})


def _approve_task(task_id: str):
    """Approve a completed task: push article to GitHub main branch."""
    table = _get_table()
    result = table.get_item(Key={"task_id": task_id})
    item = result.get("Item")
    if not item:
        return _json_response(404, {"error": "Task not found"})

    state = item.get("state", "")
    if state != "completed":
        return _json_response(400, {"error": f"Task state is '{state}', must be 'completed' to approve"})

    if item.get("published_url"):
        return _json_response(400, {"error": "Task already published", "published_url": item["published_url"]})

    # Read article from S3
    bucket = os.environ.get("S3_BUCKET", "")
    s3 = boto3.client("s3")
    key = f"tasks/{task_id}/article.md"
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        article_content = resp["Body"].read().decode("utf-8")
    except Exception:
        logger.exception("Failed to read article from S3 for task %s", task_id)
        return _json_response(500, {"error": "Failed to read article from S3"})

    # Derive article_path from publish_result
    publish_result = item.get("publish_result") or {}
    article_path = publish_result.get("article_path", "")
    if not article_path:
        return _json_response(400, {"error": "No article_path in publish_result"})

    # Push to GitHub via git_push tool logic
    from src.agents.publish.tools import git_push

    push_result_str = git_push(
        article_content=article_content,
        article_path=article_path,
        commit_message=f"publish: {article_path}",
    )
    push_result = json.loads(push_result_str)

    if push_result.get("error"):
        return _json_response(500, {"error": push_result["error"]})

    # Build published URL
    published_url = push_result.get("url", "")
    if not published_url:
        # Fallback: construct from article_path
        path_without_docs = article_path.removeprefix("docs/").removesuffix(".md")
        published_url = f"https://chaosreload.github.io/aws-hands-on-lab/{path_without_docs}/"

    # Update DynamoDB
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET published_url = :url, updated_at = :now",
        ExpressionAttributeValues={":url": published_url, ":now": now},
    )

    return _json_response(200, {"task_id": task_id, "published_url": published_url})


def _delete_task(task_id: str):
    table = _get_table()
    result = table.get_item(Key={"task_id": task_id})
    if not result.get("Item"):
        return _json_response(404, {"error": "Task not found"})

    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET #s = :state, updated_at = :now",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":state": "cancelled", ":now": now},
    )
    return {"statusCode": 204, "body": ""}
