"""Resource Tracker — records AWS resources created during lab execution.

Writes to a DynamoDB table (handson-resources) so resources can be listed,
audited, and cleaned up after lab completion or on failure.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger(__name__)

RESOURCES_TABLE = os.environ.get("RESOURCES_TABLE", "handson-resources")


class ResourceTracker:
    """Track AWS resources created during a lab execution."""

    def __init__(self, *, table_name: str | None = None, dynamodb_resource=None):
        self._ddb = dynamodb_resource or boto3.resource("dynamodb")
        self._table_name = table_name or RESOURCES_TABLE
        self._table = self._ddb.Table(self._table_name)

    def record(
        self,
        *,
        task_id: str,
        resource_type: str,
        resource_arn: str,
        region: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a single resource creation event."""
        now = datetime.now(timezone.utc).isoformat()
        item: dict[str, Any] = {
            "task_id": task_id,
            "resource_arn": resource_arn,
            "resource_type": resource_type,
            "region": region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            "status": "active",
            "created_at": now,
        }
        if metadata:
            item["metadata"] = metadata

        self._table.put_item(Item=item)
        logger.info(
            "Tracked resource %s (%s) for task %s", resource_arn, resource_type, task_id
        )

    def list_resources(self, task_id: str) -> list[dict[str, Any]]:
        """List all resources for a given task."""
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("task_id").eq(task_id),
        )
        return resp.get("Items", [])

    def mark_deleted(self, task_id: str, resource_arn: str) -> None:
        """Mark a resource as deleted (soft-delete)."""
        now = datetime.now(timezone.utc).isoformat()
        self._table.update_item(
            Key={"task_id": task_id, "resource_arn": resource_arn},
            UpdateExpression="SET #s = :status, deleted_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": "deleted", ":now": now},
        )
        logger.info("Marked resource %s as deleted for task %s", resource_arn, task_id)

    def mark_all_deleted(self, task_id: str) -> int:
        """Mark all resources for a task as deleted. Returns count."""
        resources = self.list_resources(task_id)
        count = 0
        for r in resources:
            if r.get("status") != "deleted":
                self.mark_deleted(task_id, r["resource_arn"])
                count += 1
        logger.info("Marked %d resources as deleted for task %s", count, task_id)
        return count
