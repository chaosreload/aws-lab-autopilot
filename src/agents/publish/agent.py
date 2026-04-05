"""Publish Agent — writes calibrated Hands-on Lab articles from Research + Execute results."""

from __future__ import annotations

import json
import logging
import re

from botocore.config import Config
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.publish.tools import (
    aws_knowledge_read_publish,
    generate_preview_url,
    git_push,
    memory_search_publish,
    quality_check,
    read_execute_results,
    read_research_notes,
    write_article,
)

logger = logging.getLogger(__name__)

BEDROCK_CONFIG = Config(
    read_timeout=600,
    connect_timeout=60,
    retries={"max_attempts": 2},
)

SYSTEM_PROMPT = """\
You are an AWS Hands-on Lab article writer (Publish Agent).
Your job: receive Research Agent notes and Execute Agent evidence, calibrate against official \
documentation, write a high-quality technical article, pass quality checks, and save to S3.

═══════════════════════════════════════════════════════════
WORKFLOW — strictly follow these steps in order, do not skip
═══════════════════════════════════════════════════════════

Step A: Read Materials
  1. Call read_research_notes to get the research notes (notes.md)
  2. Call read_execute_results to get execution evidence (verify-log.md + verify-log.json)

Step B: Calibration — you MUST call aws_knowledge_read_publish at least 3 times
  Each call targets one dimension:
  1. Verify service names, API operation names, and model IDs/ARNs
  2. Verify API request/response schema (field names, data types, nesting)
  3. Verify service limits (quotas, max sizes, timeouts, supported regions)
  If calibration reveals errors in the execute results, trust the documentation over the
  agent's speculation. Record corrections for the calibration stats.

Step C: Write Article
  Write the full article following the 10 mandatory sections below.
  ALL API responses, error messages, performance numbers, and model IDs MUST come from
  the read_execute_results evidence (actual stdout/stderr).
  NEVER generate API response examples from LLM memory.
  If evidence is missing, write "未验证数据" instead.

  ## 代码示例脱敏规则（强制执行，不得跳过）

  所有代码示例中，必须将以下内容替换为占位符：
  - 真实 ARN → `'arn:aws:bedrock-agentcore:REGION:ACCOUNT_ID:runtime/YOUR-RUNTIME-ID'`
  - 真实 Account ID → `ACCOUNT_ID`
  - 真实 Session ID → `f'lab-session-{int(time.time())}-{"0" * 20}'`（并附说明：≥33 字符）
  - AWS Profile 名 → `'your-aws-profile'`
  - 实际资源 ID → `'YOUR-RUNTIME-ID'`

  文章正文中禁止出现：
  - "来自 verify-log"、"来自 execute 结果"、"来自 S3 evidence"等内部引用
  - 任何真实 Account ID（12位数字）
  - 任何真实 ARN（必须用占位符替换）

  ## 代码风格规范

  - 优先使用辅助函数（helper function）封装重复的 boto3 样板代码，而不是每个 Step 重复写完整的 client 初始化
  - 第一个代码块应展示 helper function 定义，后续 Step 直接调用
  - 示例结构：
    ```python
    import boto3, time

    session = boto3.Session(profile_name='your-aws-profile', region_name='us-east-1')
    client = session.client('bedrock-agentcore')

    AGENT_ARN = 'arn:aws:bedrock-agentcore:REGION:ACCOUNT_ID:runtime/YOUR-RUNTIME-ID'
    SESSION_ID = f'lab-{int(time.time())}-{"0" * 20}'  # ≥33 字符

    def run_command(command, timeout=60):
        response = client.invoke_agent_runtime_command(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=SESSION_ID,
            body={'command': command, 'timeout': timeout}
        )
        exit_code = None
        for event in response['stream']:  # 注意：是 stream 不是 body
            chunk = event.get('chunk', event)
            if 'contentDelta' in chunk:
                if 'stdout' in chunk['contentDelta']:
                    print(chunk['contentDelta']['stdout'], end='')
            elif 'contentStop' in chunk:
                exit_code = chunk['contentStop']['exitCode']
                status = chunk['contentStop']['status']
        return exit_code, status
    ```

Step D: Quality Check（严格限制，不得超过 2 次 quality_check 调用）

  第 1 次：调用 quality_check(article)
    - 如果 passed=true → 直接进入 Step E
    - 如果 passed=false → 针对 failures 列表修复文章，进入第 2 次检查

  第 2 次（最终）：调用 quality_check(fixed_article)
    - 如果 passed=true → 进入 Step E
    - 如果 passed=false → 立即返回 rework_needed 输出，不得再次修改或再次调用 quality_check

  ⚠️ 铁律：quality_check 总调用次数不得超过 2 次。超过即为 bug。

Step E: Save and Preview
  Call write_article to save the article to S3.
  Call generate_preview_url to get a 24-hour preview link.
  Do NOT call git_push (publishing is only triggered via /tasks/{id}/approve).

═══════════════════════════════════════════════════════════
10 MANDATORY ARTICLE SECTIONS — every article must have all 10
═══════════════════════════════════════════════════════════

1. !!! info "Lab 信息" admonition at the very start:
   !!! info "Lab 信息"
       - **难度**：中级
       - **预计时间**：XX 分钟
       - **预计费用**：$X.XX
       - **推荐区域**：us-east-1
       - **最后验证**：YYYY-MM-DD

2. ## 核心概念
   Parameter overview table or version comparison table.

3. Step sections (## Step 1, ## Step 2, etc.)
   Each Step's FIRST code block must be AWS CLI (```bash).
   Each Step MUST end with a **发现:** paragraph.
   Minimum 3 Steps = minimum 3 **发现:** blocks.

4. ## 测试结果
   Summary table with one row per Step (test ID, name, result, key measurement).

5. !!! warning "踩坑 N: title" admonitions
   One warning admonition per verified pitfall from execute_result.pitfalls.
   Only include AWS product limitations that all users would encounter.
   Each warning MUST include the actual error message from stdout/stderr.
   Do NOT use speculative language (可能会, 有时会, 也许, might, may cause).

6. ## 费用明细
   Cost breakdown table with service, operation, and cost.

7. ## 清理资源
   Include a !!! danger admonition warning about resource costs if not cleaned up.
   List specific cleanup commands.

8. ## IAM 权限
   Minimal IAM policy needed to reproduce this lab.

9. ## 结论与建议
   Scenario-based recommendation table.

10. ## 参考链接（强制章节，必须包含以下内容）：
    - AWS What's New 原文链接（从 research_result 的 url 字段获取）
    - AWS 官方文档链接（从 aws_knowledge_read_publish 的查询结果中提取，至少 2 个）
    - 格式：`- [链接标题](URL)`

═══════════════════════════════════════════════════════════
OUTPUT FORMAT — strict JSON, no markdown fencing
═══════════════════════════════════════════════════════════

CRITICAL: All values in the JSON output MUST be valid JSON string literals.

Normal completion — return:
{
  "quality_passed": true,
  "article_path": "s3://...",
  "preview_url": "https://...",
  "published_url": null,
  "calibration": {"verified": 3, "corrected": 0, "undocumented": 0}
}

Rework needed — return:
{
  "rework_needed": true,
  "rework_type": "retest_specific",
  "reason": "..."
}

CRITICAL: When no rework is needed, do NOT include the rework_needed field at all.
Step Functions uses IsPresent semantics — including rework_needed: false would trigger
an incorrect rework loop.
"""

TOOLS = [
    read_research_notes,
    read_execute_results,
    aws_knowledge_read_publish,
    quality_check,
    write_article,
    generate_preview_url,
    git_push,
    memory_search_publish,
]

_DEFAULTS = {
    "quality_passed": False,
    "article_path": "",
    "preview_url": "",
    "published_url": None,
    "calibration": {"verified": 0, "corrected": 0, "undocumented": 0},
}


_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ ")


def _strip_log_lines_and_parse(text: str) -> dict:
    """Remove interleaved Python log lines from JSON text and parse."""
    lines = text.splitlines()
    cleaned = [line for line in lines if not _LOG_LINE_RE.match(line)]
    return json.loads("\n".join(cleaned))


def _parse_json_response(text: str) -> dict:
    """Extract JSON from agent text response, handling preamble text and markdown fences."""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    brace_pos = text.find("{")
    if brace_pos == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    in_string = False
    escape = False
    for i in range(brace_pos, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[brace_pos : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return _strip_log_lines_and_parse(candidate)

    try:
        return json.loads(text[brace_pos:])
    except json.JSONDecodeError:
        return _strip_log_lines_and_parse(text[brace_pos:])


def run_publish(task_id: str, research_result: dict, execute_result: dict) -> dict:
    """Run the Publish Agent to write and quality-check an article.

    Args:
        task_id: Unique task identifier.
        research_result: Research Agent output dict.
        execute_result: Execute Agent output dict.

    Returns:
        PublishResult dict with quality_passed, article_path, preview_url, calibration, etc.

    Raises:
        RuntimeError: If the agent response cannot be parsed as JSON.
    """
    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-6",
        boto_client_config=BEDROCK_CONFIG,
    )
    agent = Agent(
        model=bedrock_model,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )

    prompt = (
        f"Task ID: {task_id}\n\n"
        f"Research Result:\n```json\n{json.dumps(research_result, indent=2, ensure_ascii=False)}\n```\n\n"
        f"Execute Result:\n```json\n{json.dumps(execute_result, indent=2, ensure_ascii=False)}\n```\n\n"
        "Follow the workflow: read materials → calibrate → write article → quality check → save. "
        "Return your final answer as a JSON object."
    )

    result = agent(prompt)
    response_text = str(result)

    try:
        parsed = _parse_json_response(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse Publish Agent JSON response: %s", exc)
        raise RuntimeError(f"Publish Agent returned unparseable response: {exc}") from exc

    # Apply defaults for missing fields (only for normal completion, not rework)
    if "rework_needed" not in parsed:
        for key, default in _DEFAULTS.items():
            if key not in parsed:
                parsed[key] = default

    return parsed
