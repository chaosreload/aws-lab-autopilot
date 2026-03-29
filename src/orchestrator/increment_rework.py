"""Lambda: increment rework_count or mark task completed in DynamoDB (atomic ops)."""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
TASKS_TABLE = os.environ.get("TASKS_TABLE", "handson-tasks")


def handler(event, context):
    task_id = event.get("task_id")
    action = event.get("action", "increment_rework")
    logger.info("increment_rework called for task %s (action=%s)", task_id, action)

    if not task_id:
        raise ValueError("task_id is required")

    table = dynamodb.Table(TASKS_TABLE)
    now = datetime.now(timezone.utc).isoformat()

    if action == "mark_completed":
        table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET #s = :state, updated_at = :now",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":state": "completed", ":now": now},
        )
        return {"state": "completed"}

    # Default: increment_rework
    result = table.update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET rework_count = rework_count + :inc, updated_at = :now",
        ExpressionAttributeValues={":inc": 1, ":now": now},
        ReturnValues="UPDATED_NEW",
    )
    new_count = int(result["Attributes"]["rework_count"])
    logger.info("task %s rework_count now %d", task_id, new_count)
    return {"rework_count": new_count}
