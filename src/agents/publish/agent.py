"""Publish Agent — writes high-quality AWS Hands-on Lab articles and publishes them."""

from __future__ import annotations

import json
import logging

from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.publish.tools import (
    aws_knowledge_read_publish,
    git_push,
    quality_check,
    read_execute_results,
    read_research_notes,
    write_article,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是 AWS 技术文章发布工程师。
你的工作是基于研究笔记和测试结果，撰写高质量的 AWS Hands-on Lab 文章并发布。

工作流程：
1. 用 read_research_notes 和 read_execute_results 读取素材
2. 用 aws_knowledge_read_publish 校准关键技术声明（至少 3 条）
3. 撰写文章（Markdown 格式，含：背景、前置条件、步骤、测试数据、踩坑、IAM Policy、费用、清理）
4. 用 quality_check 自检 7 条红线，不通过则修改文章
5. 用 write_article 保存到 S3
6. 用 git_push 发布到 GitHub

输出格式（JSON）：
{
  "quality_passed": true,
  "article_path": "docs/ai-ml/xxx.md",
  "published_url": "https://chaosreload.github.io/aws-hands-on-lab/ai-ml/xxx/",
  "calibration": {"verified": 3, "corrected": 0, "undocumented": 1},
  "rework_needed": true/false（如果 quality_check 反复失败 2 次，设为 true）
}
注意：如果不需要 rework，不要返回 rework_needed 字段（设计文档约定）。
"""

MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"


def _create_agent() -> Agent:
    model = BedrockModel(model_id=MODEL_ID)
    return Agent(
        model=model,
        tools=[
            read_research_notes,
            read_execute_results,
            aws_knowledge_read_publish,
            quality_check,
            write_article,
            git_push,
        ],
        system_prompt=SYSTEM_PROMPT,
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


def run_publish(task_id: str, research_result: dict, execute_result: dict) -> dict:
    """Run the Publish Agent to write and publish an article.

    Args:
        task_id: Unique task identifier.
        research_result: Output from the research agent.
        execute_result: Output from the execute agent.

    Returns:
        PublishResult dict with quality_passed, article_path, published_url,
        calibration, and optionally rework_needed.
    """
    agent = _create_agent()

    prompt = (
        f"Task ID: {task_id}\n"
        f"Research Result: {json.dumps(research_result, ensure_ascii=False)}\n"
        f"Execute Result: {json.dumps(execute_result, ensure_ascii=False)}\n\n"
        f"Please read the research notes and execute results, then write and publish "
        f"a high-quality AWS Hands-on Lab article. Return the result as a JSON object."
    )

    logger.info("Starting publish agent for task=%s", task_id)
    result = agent(prompt)
    response_text = str(result).strip()
    logger.info("Publish agent completed for task=%s", task_id)

    parsed = _parse_agent_response(response_text)

    defaults = {
        "quality_passed": False,
        "article_path": "",
        "published_url": "",
        "calibration": {"verified": 0, "corrected": 0, "undocumented": 0},
    }
    for key, default in defaults.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
