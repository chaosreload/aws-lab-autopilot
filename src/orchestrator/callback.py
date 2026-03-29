"""Step Functions callback helpers — send_success / send_failure."""

import json
import os

import boto3

sfn = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def send_success(task_token: str, output: dict) -> None:
    """Report successful task completion to Step Functions."""
    sfn.send_task_success(taskToken=task_token, output=json.dumps(output))


def send_failure(task_token: str, error: str, cause: str) -> None:
    """Report task failure to Step Functions."""
    sfn.send_task_failure(taskToken=task_token, error=error[:256], cause=cause[:32768])
