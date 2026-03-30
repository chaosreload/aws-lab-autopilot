"""Execute Agent — dual-round execution (explore + verify) on AWS."""

from __future__ import annotations

import json
import logging

from botocore.config import Config
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.execute.tools import (
    aws_cli_execute,
    cleanup_resources,
    iam_add_permission,
    memory_create,
    python_execute,
    reset_evidence,
    track_resource,
    write_execute_log,
)
from src.agents.research.tools import aws_knowledge_read

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an AWS lab execution engineer.
Your job is to execute test operations on AWS according to the test matrix.

执行铁律：
1. 每段代码必须用 aws_cli_execute 或 python_execute 真实执行。禁止编造输出，禁止"预期输出"占位符。
2. 每个测试项完成后调用 write_execute_log 记录真实的 stdout/stderr。
3. 双轮执行：探索轮可以 debug；复测轮从干净状态重执行，这一轮数据是最终数据。
4. 性能数据：至少 3 次采样，计算 avg/min/max。
5. 遇到错误先用 aws_knowledge_read 查文档，确认是 AWS 限制则记录为 pitfall。
6. 踩坑只记录真实发现（非预期行为 + 有 stdout/stderr 证据），禁止推测性踩坑。

Execution principles:
1. Execute tests from the test matrix one by one. If you get ACCESS_DENIED, use \
iam_add_permission to add the required permission and retry.
2. After each test execution, use write_execute_log to record the result.
3. When encountering errors, use aws_knowledge_read to check documentation and \
determine if it is an operational error or an AWS limitation.
4. Track all created resources with track_resource.
5. After the explore round, use cleanup_resources to clean up (keep IAM Role).
6. After the verify round, do a final cleanup including IAM Role.

Output format (JSON):
{
  "test_results": {"T1": "pass", "T2": "fail"},
  "final_iam_policy": {...},
  "permissions_added": ["s3:CreateBucket"],
  "pitfalls": [{"desc": "...", "verified": true}],
  "cost_actual": 0.5
}
"""

MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def _create_agent() -> Agent:
    model = BedrockModel(
        model_id=MODEL_ID,
        boto_client_config=Config(
            read_timeout=600,
            connect_timeout=60,
            retries={"max_attempts": 2},
        ),
    )
    return Agent(
        model=model,
        tools=[
            aws_cli_execute,
            python_execute,
            iam_add_permission,
            track_resource,
            cleanup_resources,
            write_execute_log,
            memory_create,
            aws_knowledge_read,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def _build_prompt(task_id: str, phase: str, research_result: dict) -> str:
    """Build a prompt for one execution round."""
    test_matrix = json.dumps(research_result.get("test_matrix", []), ensure_ascii=False)
    iam_policy = json.dumps(research_result.get("iam_policy", {}), ensure_ascii=False)
    services = ", ".join(research_result.get("services", []))

    return (
        f"Task ID: {task_id}\n"
        f"Phase: {phase}\n"
        f"Services: {services}\n"
        f"IAM Policy: {iam_policy}\n"
        f"Test Matrix: {test_matrix}\n\n"
        f"Execute all tests in the matrix for the '{phase}' round. "
        f"Return the result as a JSON object."
    )


def _parse_agent_response(text: str) -> dict:
    """Parse agent response, extracting JSON from text if needed."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return {}


def run_execute(task_id: str, research_result: dict) -> dict:
    """Run dual-round execution: explore -> cleanup -> verify -> cleanup.

    Args:
        task_id: Unique task identifier.
        research_result: Output from the research agent.

    Returns:
        ExecuteResult dict with test_results, final_iam_policy, permissions_added,
        pitfalls, and cost_actual.
    """
    reset_evidence()
    agent = _create_agent()

    # Round 1: Explore
    logger.info("Starting explore round for task=%s", task_id)
    explore_prompt = _build_prompt(task_id, "explore", research_result)
    explore_result = agent(explore_prompt)
    explore_data = _parse_agent_response(str(explore_result))
    logger.info("Explore round completed for task=%s", task_id)

    # Round 2: Verify (fresh agent for clean context)
    agent = _create_agent()
    logger.info("Starting verify round for task=%s", task_id)
    verify_prompt = _build_prompt(task_id, "verify", research_result)
    if explore_data.get("permissions_added"):
        verify_prompt += (
            f"\nPermissions added during explore: "
            f"{json.dumps(explore_data['permissions_added'])}"
        )
    if explore_data.get("pitfalls"):
        verify_prompt += (
            f"\nPitfalls found during explore: "
            f"{json.dumps(explore_data['pitfalls'], ensure_ascii=False)}"
        )

    verify_result = agent(verify_prompt)
    verify_data = _parse_agent_response(str(verify_result))
    logger.info("Verify round completed for task=%s", task_id)

    # Merge results: verify round is authoritative for test_results
    merged = {
        "test_results": verify_data.get("test_results", explore_data.get("test_results", {})),
        "final_iam_policy": verify_data.get(
            "final_iam_policy", explore_data.get("final_iam_policy", research_result.get("iam_policy", {}))
        ),
        "permissions_added": list(
            set(explore_data.get("permissions_added", []))
            | set(verify_data.get("permissions_added", []))
        ),
        "pitfalls": verify_data.get("pitfalls", explore_data.get("pitfalls", [])),
        "cost_actual": (
            explore_data.get("cost_actual", 0) + verify_data.get("cost_actual", 0)
        ),
    }

    return merged
