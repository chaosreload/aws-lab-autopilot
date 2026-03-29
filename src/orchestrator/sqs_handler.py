"""SQS to AgentCore bridge Lambda — dispatches to real agents or mock fallbacks."""

import json
import logging
import os

from datetime import datetime, timezone

import boto3

from src.agents.execute.agent import run_execute
from src.agents.research.agent import run_research
from src.orchestrator.callback import send_failure, send_success

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
TASKS_TABLE = os.environ.get("TASKS_TABLE", "handson-tasks")

AGENT_STATE_MAP = {
    "research": "researching",
    "execute": "executing",
    "publish": "publishing",
}


def _update_task_state(task_id: str, agent_type: str):
    new_state = AGENT_STATE_MAP.get(agent_type)
    if not new_state or not task_id:
        return
    table = dynamodb.Table(TASKS_TABLE)
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET #s = :state, updated_at = :now",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={
            ":state": new_state,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )

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
        task_token = body.get("token")
        task_id = body.get("task_id")
        agent_type = _detect_agent_type(body)

        logger.info("Processing task %s as agent_type=%s", task_id, agent_type)
        _update_task_state(task_id, agent_type)

        if not task_token:
            logger.error("No TaskToken found for task %s, skipping", task_id)
            continue

        try:
            if agent_type == "research":
                url = body.get("url", "")
                result = run_research(task_id, url)
            elif agent_type == "execute":
                research_result = body.get("research", {})
                result = run_execute(task_id, research_result)
            else:
                result = MOCK_RESULTS.get(agent_type, {"status": "unknown_agent_type"})

            send_success(task_token, result)
            logger.info("Sent task success for task %s (agent_type=%s)", task_id, agent_type)
        except Exception:
            logger.exception("Failed to process task %s", task_id)
            try:
                send_failure(
                    task_token,
                    "AgentError",
                    f"Agent '{agent_type}' failed for task {task_id}",
                )
            except Exception:
                logger.exception("Failed to send task failure for task %s", task_id)
