"""Research Agent — reads AWS documentation and produces a research verdict."""

from __future__ import annotations

import json
import logging

from botocore.config import Config
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.research.tools import (
    aws_knowledge_read,
    aws_knowledge_region,
    list_bedrock_models,
    memory_search,
    write_notes,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是 AWS Hands-on Lab 内容规划师。

输入：AWS What's New 公告 URL（不一定是教程页面）。
你的工作：从公告中识别出可以实操验证的技术内容，制定 Lab 执行计划。

## 判断标准：什么时候给 "go"

只要公告涉及以下任一情况，就应该给 "go"：
1. 新发布的 AWS 服务或功能（有 API/CLI 可以调用）
2. 新模型、新算法、新数据格式（可以调 API 验证）
3. 新的服务配置、新参数、新权限模式（可以创建资源验证）
4. 现有服务的重要更新（可以对比新旧行为）

只有以下情况才给 "skip"：
1. 纯区域扩展（某服务在新区域上线），且不涉及新功能
2. 纯定价变更（没有功能变化）
3. 纯控制台 UI 改进（没有 API/CLI 变化）
4. 服务下线或废弃通知
5. 纯文档更新

## 工作流程

1. 用 aws_knowledge_read 搜索该公告相关的 AWS 文档，理解技术细节
2. 判断 verdict（go/skip），并写出理由
3. 如果是 go：
   a. **如果涉及 Bedrock 模型（embedding、inference、foundation model）**：
      - 必须先调用 list_bedrock_models 查询实际可用的模型列表
      - 从返回结果中找到匹配的模型 ID（status=ACTIVE 的那个）
      - 在 test_matrix 的 api_hints 里使用确认后的 model ID
      - 注意：AWS 文档中的 model ID 可能是预览版或已更名，实际 ID 以 list_bedrock_models 返回为准
   b. 设计 3-5 个测试用例（T1=核心功能验证P0, T2=边界条件P0, T3=对比测试P1...）
      - 每个测试用例必须包含具体的 API 调用参数（api_hints 字段），不能只写描述
      - 对于 Bedrock 模型测试，api_hints 里的 model_id 必须是已用 list_bedrock_models 确认过的
   c. 推导最小 IAM Policy（只包含测试需要的 actions）
   d. 列出涉及的 AWS services（用 CLI service 名，如 bedrock-runtime，不是 Amazon Bedrock）
4. 用 write_notes 写入详细研究笔记（包含：技术分析、测试设计、IAM 推导、注意事项）

## 输出格式（严格 JSON，不要 markdown fencing）

{
  "verdict": "go",
  "notes_path": "s3://...",
  "test_matrix": [
    {
      "id": "T1",
      "name": "核心 API 调用验证",
      "priority": "P0",
      "api_hints": {
        "service": "bedrock-runtime",
        "operation": "invoke_model",
        "model_id": "amazon.nova-2-multimodal-embeddings-v1:0",
        "request_body": {"schemaVersion": "nova-multimodal-embed-v1", "taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {"embeddingPurpose": "GENERIC_INDEX", "embeddingDimension": 256, "text": {"truncationMode": "END", "value": "test"}}}
      }
    },
    {"id": "T2", "name": "边界条件测试", "priority": "P0", "api_hints": {}},
    {"id": "T3", "name": "与旧版/相近功能对比", "priority": "P1", "api_hints": {}}
  ],
  "iam_policy": {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": [...], "Resource": "*"}]
  },
  "services": ["bedrock-runtime", "cloudwatch", "s3"]
}

## 关键约束

- test_matrix 里的 api_hints.model_id 必须来自 list_bedrock_models 的返回值，不能凭经验填写
- 如果 list_bedrock_models 里找不到相关模型（status 不是 ACTIVE），说明该模型在当前账户/区域不可用，需要在 notes 里记录这个限制，但仍然可以给 go（Execute Agent 会遇到 ValidationException 并标记为 "model not available"）

## 示例判断

- "Amazon Nova Multimodal Embeddings GA" → go（先调 list_bedrock_models(output_modality="EMBEDDING") 确认 model ID，实际是 amazon.nova-2-multimodal-embeddings-v1:0，然后设计 embedding 验证用例）
- "Amazon Bedrock TTFT CloudWatch metrics" → go（新指标，可以调 bedrock-runtime + cloudwatch 验证）
- "Amazon S3 Express One Zone available in ap-southeast-1" → skip（纯区域扩展）
- "AWS Lambda now supports Python 3.13" → go（runtime 更新，可以创建函数验证新 runtime）
"""

MODEL_ID = "us.anthropic.claude-opus-4-6-v1"


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
        tools=[aws_knowledge_read, aws_knowledge_region, list_bedrock_models, write_notes, memory_search],
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
        f"分析以下 AWS What's New 公告，制定 Hands-on Lab 执行计划。\n"
        f"Task ID: {task_id}\n"
        f"URL: {url}\n\n"
        f"请先用 aws_knowledge_read 搜索相关文档了解技术细节，然后给出 verdict 和完整的 Lab 计划。\n"
        f"如果公告涉及 Bedrock 模型，务必先调用 list_bedrock_models 确认实际可用的 model ID，"
        f"不要凭文档中的 model ID 直接填写（文档可能有误或使用了旧版/预览版 ID）。\n"
        f"记住：What's New 公告页面本身不是教程，但里面描述的新功能通常都有对应的 API 可以验证。"
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
