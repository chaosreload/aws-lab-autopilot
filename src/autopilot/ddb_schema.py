"""DynamoDB table schema for aws-lab-autopilot tasks.

Covers Phase 2 Gap 8 (sub-agent lifecycle observability) heartbeat fields
and Phase 3 append_progress / mark_phase_complete ergonomics.

Usage:
    # Local (DynamoDB Local on 8001):
    python -m src.autopilot.ddb_schema create

    # Inspect:
    python -m src.autopilot.ddb_schema describe
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("AUTOPILOT_TABLE", "autopilot-tasks")
ENDPOINT = os.environ.get("DDB_ENDPOINT", "http://localhost:8001")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def _client():
    """Return a DynamoDB client pointed at either DDB Local or real AWS."""
    kwargs = {"region_name": REGION}
    if ENDPOINT:
        kwargs["endpoint_url"] = ENDPOINT
        # DDB Local accepts any credentials; provide dummies so boto3 doesn't try signing real env creds.
        kwargs["aws_access_key_id"] = "local"
        kwargs["aws_secret_access_key"] = "local"
    return boto3.client("dynamodb", **kwargs)


def create_table() -> None:
    """Create the autopilot-tasks table.

    Schema:
        PK: task_id (S)
        GSI1: status-created_at-index (for "list running tasks", "list failed")

    All non-key attributes are stored as individual top-level attributes:
        status (S), phase (S), current_step (S),
        heartbeat_at (S, ISO8601 UTC),
        url (S), region (S), region_reason (S),
        notes_path (S), test_matrix (L of M), task_type (S),
        estimated_execution_minutes (N),
        research_result (M),    -- full ResearchResult dict
        execute_result (M),     -- full ExecuteResult dict (Stage 2+)
        publish_result (M),     -- full PublishResult dict (Stage 3+)
        created_at (S, ISO8601 UTC),
        updated_at (S, ISO8601 UTC),
        error (M, optional)
    """
    client = _client()
    try:
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "task_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-created_at-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
        )
        print(f"Created table {TABLE_NAME} @ {ENDPOINT}")
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceInUseException":
            print(f"Table {TABLE_NAME} already exists")
        else:
            raise


def describe_table() -> None:
    client = _client()
    resp = client.describe_table(TableName=TABLE_NAME)
    table = resp["Table"]
    print(f"Table:  {table['TableName']}")
    print(f"Status: {table['TableStatus']}")
    print(f"Items:  {table['ItemCount']}")
    print(f"Keys:   {table['KeySchema']}")
    gsis = table.get("GlobalSecondaryIndexes", [])
    for gsi in gsis:
        print(f"GSI:    {gsi['IndexName']} ({gsi['IndexStatus']})")


def delete_table() -> None:
    client = _client()
    try:
        client.delete_table(TableName=TABLE_NAME)
        print(f"Deleted table {TABLE_NAME}")
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"Table {TABLE_NAME} did not exist")
        else:
            raise


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "create"
    {
        "create": create_table,
        "describe": describe_table,
        "delete": delete_table,
    }[cmd]()


if __name__ == "__main__":
    main()
