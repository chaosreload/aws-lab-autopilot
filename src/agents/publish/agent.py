"""Publish Agent — writes high-quality AWS Hands-on Lab articles and publishes them."""

from __future__ import annotations

import json
import logging
import os

from botocore.config import Config
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.publish.tools import (
    aws_knowledge_read_publish,
    generate_preview_url,
    quality_check,
    read_execute_results,
    read_research_notes,
    write_article,
)
from src.agents.research.tools import memory_search

logger = logging.getLogger(__name__)

# Path to the article template (relative to this file: src/agents/publish/agent.py)
_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "templates", "article.md"
)

SYSTEM_PROMPT = """\
你是 AWS 技术文章发布工程师。
你的工作是基于研究笔记和测试结果，撰写高质量的 AWS Hands-on Lab 文章并发布。

Evidence 优先原则：
- 文章中的 API 响应格式、错误信息文本、性能数字、Model ID 必须来自 read_execute_results 返回的内容。
- 禁止 LLM 自行生成以上内容。
- 如果 read_execute_results 返回空，必须在文章中标记为"未验证数据"。

工作流程：
1. 用 read_research_notes 和 read_execute_results 读取素材
2. 用 aws_knowledge_read_publish 校准关键技术声明（根据文章技术声明的数量决定调用次数，每条重要技术声明都需要校准）
3. 撰写文章时必须严格遵循 templates/article.md 的结构（已在 prompt 末尾附上完整模板）：
   - Lab 信息框（难度/时间/费用）是 MANDATORY，必须出现在文章开头（## Lab 信息 小节）
   - 动手实践 Steps 先给 AWS CLI 命令，再给 Python（如果适用）
   - 每个测试结果小节必须以 "**发现:**" 行结尾，提炼数据洞察
   - 所有数字（延迟 ms、相似度分数、向量维度等）必须来自 read_execute_results 的实测数据，禁止估算或填写"~XXX ms"
   - 踩坑记录只写 Execute Agent evidence 中出现的真实错误（stderr/exception），禁止捏造
   - 代码示例中的向量值、响应示例必须来自 evidence 里的真实数据
   - 文章长度控制在 2500-5000 字（中文），不要写成论文
4. 用 quality_check 自检 7 条红线，不通过则修改文章
5. 用 write_article 保存到 S3
6. 用 generate_preview_url 生成 S3 预览链接（24小时有效）
7. 不要调用 git_push，发布由人工审批后触发

输出格式（JSON）：
{
  "quality_passed": true,
  "article_path": "docs/ai-ml/xxx.md",
  "preview_url": "https://s3.amazonaws.com/...",
  "published_url": null,
  "calibration": {"verified": 3, "corrected": 0, "undocumented": 1},
  "rework_needed": true/false（如果 quality_check 反复失败 2 次，设为 true）
}
注意：如果不需要 rework，不要返回 rework_needed 字段（设计文档约定）。
注意：published_url 在此阶段始终为 null，等人工 approve 后才会填入。
"""

MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def _load_article_template() -> str:
    """Load the article template from templates/article.md."""
    path = os.path.abspath(_TEMPLATE_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Article template not found at %s", path)
        return ""


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
            read_research_notes,
            read_execute_results,
            aws_knowledge_read_publish,
            quality_check,
            write_article,
            generate_preview_url,
            memory_search,
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

    template = _load_article_template()
    template_section = ""
    if template:
        template_section = (
            "\n\n## 文章结构模板\n\n"
            "以下是文章的标准模板（Jinja2 风格，`{{ }}` 为占位符，`{# #}` 为约束说明）。\n"
            "撰写文章时，将所有 `{{ }}` 占位符替换为实测数据，严格遵守 `{# #}` 中的约束：\n\n"
            f"{template}"
        )

    prompt = (
        f"Task ID: {task_id}\n"
        f"Research Result: {json.dumps(research_result, ensure_ascii=False)}\n"
        f"Execute Result: {json.dumps(execute_result, ensure_ascii=False)}\n\n"
        f"请读取研究笔记和测试结果，严格按照末尾的文章模板结构撰写一篇高质量的 "
        f"AWS Hands-on Lab 文章，然后返回 JSON 结果。"
        f"{template_section}"
    )

    logger.info("Starting publish agent for task=%s", task_id)
    result = agent(prompt)
    response_text = str(result).strip()
    logger.info("Publish agent completed for task=%s", task_id)

    parsed = _parse_agent_response(response_text)

    defaults = {
        "quality_passed": False,
        "article_path": "",
        "preview_url": "",
        "published_url": None,
        "calibration": {"verified": 0, "corrected": 0, "undocumented": 0},
    }
    for key, default in defaults.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
