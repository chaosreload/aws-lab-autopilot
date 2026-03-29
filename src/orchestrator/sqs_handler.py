"""SQS to AgentCore bridge Lambda — mock implementation for Phase 1."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")

MOCK_RESULTS = {
    "research": {
        "verdict": "go",
        "complexity": "M",
        "estimated_cost": 1.2,
        "notes_path": "s3://mock-bucket/notes.md",
        "test_matrix": [
            {"id": "T1", "name": "basic-deploy", "priority": "P0"},
            {"id": "T2", "name": "iam-validation", "priority": "P1"},
        ],
        "iam_policy": {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "*"}
            ],
        },
        "services": ["s3", "lambda", "dynamodb"],
    },
    "execute": {
        "test_results": {"T1": "pass", "T2": "pass"},
        "final_iam_policy": {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "*"}
            ],
        },
        "cost_actual": 0.5,
    },
    "publish": {
        "quality_passed": True,
        "article_path": "docs/mock-article.md",
        "published_url": "https://mock.example.com/article",
    },
}


def _detect_agent_type(body: dict) -> str:
    if "agent_type" in body:
        return body["agent_type"]
    if "research" in body:
        if "execute_result" in body:
            return "publish"
        return "execute"
    return "research"


def handler(event, context):
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        task_token = body.get("token") or body.get("TaskToken")
        task_id = body.get("task_id")
        agent_type = _detect_agent_type(body)

        logger.info("Processing task %s as agent_type=%s", task_id, agent_type)

        if not task_token:
            logger.error("No TaskToken found for task %s, skipping", task_id)
            continue

        try:
            mock_result = MOCK_RESULTS.get(agent_type, {"status": "unknown_agent_type"})
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps(mock_result),
            )
            logger.info("Sent task success for task %s (agent_type=%s)", task_id, agent_type)
        except Exception:
            logger.exception("Failed to process task %s", task_id)
            try:
                sfn.send_task_failure(
                    taskToken=task_token,
                    error="AgentError",
                    cause=f"Mock agent '{agent_type}' failed for task {task_id}",
                )
            except Exception:
                logger.exception("Failed to send task failure for task %s", task_id)
