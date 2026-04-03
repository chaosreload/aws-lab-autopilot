"""Research Agent — evaluates AWS What's New announcements and designs test matrices."""

from __future__ import annotations

import json
import logging
import re

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

BEDROCK_CONFIG = Config(
    read_timeout=600,
    connect_timeout=60,
    retries={"max_attempts": 2},
)

SYSTEM_PROMPT = """\
You are an AWS Hands-on Lab content planner (Research Agent).
Your job: evaluate an AWS What's New announcement, research the feature deeply, \
design a test matrix, derive a minimal IAM policy, and write structured notes to S3.

═══════════════════════════════════════════════════════════
WORKFLOW — you MUST follow these steps in order
═══════════════════════════════════════════════════════════

Step 1: Call aws_knowledge_read to search for documentation about the announced feature.
Step 2: If the announcement might be a pure regional expansion, call aws_knowledge_region to confirm before giving a "skip" verdict.
Step 3: Decide verdict (go / skip) and task_type (api_call / infrastructure / mixed).
Step 4: If verdict is "go" and the feature involves Bedrock models, call list_bedrock_models to verify actual model IDs. Never trust documentation model IDs blindly.
Step 5: Design the test matrix following the Three Principles below.
Step 6: Derive the minimal IAM Policy needed for the test matrix.
Step 7: Estimate total execution time (include infrastructure wait times for infrastructure/mixed tasks).
Step 8: Call write_notes to save your complete research notes to S3.

═══════════════════════════════════════════════════════════
VERDICT CRITERIA
═══════════════════════════════════════════════════════════

Give "go" when the announcement involves ANY of:
  - New AWS service or feature that has API/CLI endpoints
  - New model, algorithm, or data format callable via API
  - New service configuration, parameter, or permission pattern (can create resources to verify)
  - Important update to existing service (can compare old vs new behavior)

Give "skip" ONLY for:
  - Pure regional expansion (no new features) — you MUST call aws_knowledge_region first to confirm
  - Pure pricing change (no functional change)
  - Pure console UI improvement (no API/CLI change)
  - Service deprecation notice
  - Pure documentation update

═══════════════════════════════════════════════════════════
TASK TYPE CLASSIFICATION
═══════════════════════════════════════════════════════════

  - api_call: Only calls existing AWS APIs, no resource creation needed. Expected execution < 15 min.
  - infrastructure: Requires creating, waiting for, and cleaning up AWS resources. Expected 30-120 min.
  - mixed: Some tests are direct API calls, others require infrastructure setup.

You MUST include "task_type_reason" in your output — a one-line explanation of why you chose this task_type.

═══════════════════════════════════════════════════════════
TEST MATRIX DESIGN — Three Principles
═══════════════════════════════════════════════════════════

Principle 1: A/B comparison > single validation
  Design comparison tests (e.g., different parameters, old vs new), not just "it works" tests.

Principle 2: Boundary conditions are MANDATORY
  At least 1 test MUST cover limits, quotas, empty input, oversized input, or type errors.

Principle 3: Dry-run first
  If the AWS service supports dry-run or simulate mode, include it as the first test.

Test matrix total ≤ 8 items, structured as:

  Type A — Valid value tests (max 3):
    T1: Core API call verification (P0)
    T2: Comparison test — different params or old-vs-new (P0)
    T3: Enum coverage — merge all valid enum values into one test (P0)

  Type B — Invalid value tests (merge into 1):
    T4: Combined invalid values — all invalid inputs in one test (P1)

  Type C — Boundary value tests (merge into 1):
    T5: Combined boundary tests — empty, oversized, type errors (P1)

Every test item MUST include ALL of these fields:
  - "type": one of api_call | infrastructure | data_validation | cdc
  - "prerequisites": list of test IDs that must run first ([] for independent tests)
  - "api_hints": dict with at least service and operation
  - "infrastructure_hints": dict with resources_needed, estimated_wait_minutes, cleanup_on_failure ({} for pure api_call tests)
  - "validation_criteria": dict with "type" and "expected" fields
  - "description": one-line summary of what the test verifies

If the feature involves Bedrock models, you MUST call list_bedrock_models first and use the confirmed model_id in api_hints.

═══════════════════════════════════════════════════════════
NOTES FORMAT (write_notes)
═══════════════════════════════════════════════════════════

The content you pass to write_notes MUST follow this structure:

```
# [Feature Name]

**Task ID**: {task_id}
**Region**: us-east-1
**Account**: (use STS get-caller-identity if available, otherwise N/A)
**Source URL**: {url}
**Started**: {ISO 8601 timestamp}

## 1. Assessment Conclusion

Verdict: go/skip
Reason: one-line summary
Task Type: api_call / infrastructure / mixed
Task Type Reason: one-line explanation
Complexity: S / M / L
Estimated Execution: X minutes
Estimated Cost: $X.XX USD

## 2. Deep Research

[Technical details, API analysis, regional availability, pricing, known limitations]

## 3. Test Design

[Test matrix details, rationale for each test, boundary conditions]
```

═══════════════════════════════════════════════════════════
OUTPUT FORMAT — strict JSON, no markdown fencing
═══════════════════════════════════════════════════════════

Return ONLY a JSON object (no ```json wrapper, no extra text):

{
  "verdict": "go",
  "task_type": "api_call",
  "task_type_reason": "All tests use InvokeModel API, no resource creation needed",
  "notes_path": "s3://...",
  "test_matrix": [
    {
      "id": "T1",
      "name": "...",
      "description": "one-line summary of what this test verifies",
      "priority": "P0",
      "type": "api_call",
      "prerequisites": [],
      "api_hints": {
        "service": "...",
        "operation": "...",
        "request_body": {}
      },
      "infrastructure_hints": {},
      "validation_criteria": {
        "type": "response_structure",
        "expected": "..."
      }
    }
  ],
  "iam_policy": {
    "Version": "2012-10-17",
    "Statement": [...]
  },
  "services": ["bedrock-runtime"],
  "estimated_execution_minutes": 10
}

If verdict is "skip", return:
{
  "verdict": "skip",
  "task_type": "api_call",
  "task_type_reason": "...",
  "notes_path": "",
  "test_matrix": [],
  "iam_policy": {},
  "services": [],
  "estimated_execution_minutes": 0
}
"""

TOOLS = [
    aws_knowledge_read,
    aws_knowledge_region,
    list_bedrock_models,
    write_notes,
    memory_search,
]

_DEFAULTS = {
    "verdict": "skip",
    "task_type": "api_call",
    "task_type_reason": "",
    "notes_path": "",
    "test_matrix": [],
    "iam_policy": {},
    "services": [],
    "estimated_execution_minutes": 0,
}


def _parse_json_response(text: str) -> dict:
    """Extract JSON from agent text response, handling preamble text and markdown fences."""
    # Try to find a fenced JSON block first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    # Find the first top-level JSON object in the text
    brace_pos = text.find("{")
    if brace_pos == -1:
        raise ValueError("No JSON object found in response")

    # Walk forward to find the matching closing brace
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
                return json.loads(text[brace_pos : i + 1])

    # Fallback: try from the first brace to end
    return json.loads(text[brace_pos:])


def run_research(task_id: str, url: str) -> dict:
    """Run the Research Agent to evaluate an AWS What's New URL.

    Args:
        task_id: Unique task identifier.
        url: AWS What's New announcement URL.

    Returns:
        ResearchResult dict with verdict, test_matrix, iam_policy, etc.

    Raises:
        RuntimeError: If the agent response cannot be parsed as JSON.
    """
    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-opus-4-6-v1",
        boto_client_config=BEDROCK_CONFIG,
    )
    agent = Agent(
        model=bedrock_model,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )

    prompt = (
        f"Task ID: {task_id}\n"
        f"AWS What's New URL: {url}\n\n"
        "Please evaluate this announcement following the workflow steps. "
        "Return your final answer as a JSON object."
    )

    result = agent(prompt)
    response_text = str(result)

    try:
        parsed = _parse_json_response(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse Research Agent JSON response: %s", exc)
        raise RuntimeError(f"Research Agent returned unparseable response: {exc}") from exc

    # Apply defaults for any missing fields
    for key, default in _DEFAULTS.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
