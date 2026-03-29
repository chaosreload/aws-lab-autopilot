"""Research Agent — reads AWS documentation and produces a research verdict."""

from __future__ import annotations

import json
import logging

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.research.tools import aws_knowledge_read, memory_search, write_notes

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Research Agent for an AWS hands-on lab autopilot system.

Given an AWS documentation URL for a hands-on tutorial, your job is to:
1. Use aws_knowledge_read to search and read relevant AWS documentation.
2. Determine whether the tutorial is feasible to automate (verdict: "go" or "skip").
3. Identify the AWS services involved.
4. Draft a minimal IAM policy needed to execute the tutorial.
5. Create a test matrix with test cases (each with id, name, priority).
6. Write detailed research notes using write_notes.

You MUST respond with a valid JSON object (no markdown fencing) containing:
{
  "verdict": "go" or "skip",
  "notes_path": "<s3 uri from write_notes>",
  "test_matrix": [{"id": "T1", "name": "...", "priority": "P0"}],
  "iam_policy": {"Version": "2012-10-17", "Statement": [...]},
  "services": ["s3", "lambda", ...]
}
"""

MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"


def _create_agent() -> Agent:
    model = BedrockModel(model_id=MODEL_ID)
    return Agent(
        model=model,
        tools=[aws_knowledge_read, write_notes, memory_search],
        system_prompt=SYSTEM_PROMPT,
    )


def run_research(task_id: str, url: str) -> dict:
    """Run the research agent for a given task and URL.

    Args:
        task_id: Unique task identifier (used for S3 path).
        url: AWS documentation URL to research.

    Returns:
        dict with verdict, notes_path, test_matrix, iam_policy, services.
    """
    agent = _create_agent()
    prompt = (
        f"Research the following AWS hands-on tutorial and produce a structured verdict.\n"
        f"Task ID: {task_id}\n"
        f"URL: {url}\n"
    )

    logger.info("Starting research agent for task=%s url=%s", task_id, url)
    result = agent(prompt)
    response_text = str(result).strip()
    logger.info("Research agent completed for task=%s", task_id)

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse agent response as JSON, extracting from text")
        parsed = None
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(response_text[start:end])
            except json.JSONDecodeError:
                pass
        if parsed is None:
            parsed = {
                "verdict": "skip",
                "notes_path": "",
                "test_matrix": [],
                "iam_policy": {},
                "services": [],
                "error": "Failed to parse agent response",
            }

    defaults = {
        "verdict": "skip",
        "notes_path": "",
        "test_matrix": [],
        "iam_policy": {},
        "services": [],
    }
    for key, default in defaults.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
