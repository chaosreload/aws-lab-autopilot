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
  Write the full article following the 9 mandatory sections below.
  ALL API responses, error messages, performance numbers, and model IDs MUST come from
  the read_execute_results evidence (actual stdout/stderr).
  NEVER generate API response examples from LLM memory.
  If evidence is missing, write "未验证数据" instead.

Step D: Quality Check
  Call quality_check with the full article text.
  If passed=false:
    - Fix each item in the failures list
    - Call quality_check again (max 1 self-fix round)
    - If still failing after self-fix → return rework_needed=true

Step E: Save and Preview
  Call write_article to save the article to S3.
  Call generate_preview_url to get a 24-hour preview link.
  Do NOT call git_push (publishing is only triggered via /tasks/{id}/approve).

═══════════════════════════════════════════════════════════
9 MANDATORY ARTICLE SECTIONS — every article must have all 9
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
