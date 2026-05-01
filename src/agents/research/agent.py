"""Research Agent — evaluates AWS What's New announcements and designs test matrices."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

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
Step 8: Build the TEST ENVIRONMENT object (see section below). Required when verdict=go.
Step 9: Call write_notes to save your complete research notes to S3.

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
TEST ENVIRONMENT — required when verdict="go"
═══════════════════════════════════════════════════════════

The "environment" object fully describes WHERE and HOW the test will run.
It is consumed by the Execute Agent (Stage 2) to enforce region, account,
tags, budget, cleanup TTL, and prerequisites.

You will receive the caller's AWS identity in the prompt as
  AWS Identity: {"Account": "...", "Arn": "...", "UserId": "..."}
You MUST copy Account verbatim into environment.account_id — do NOT invent
or guess an account id. If the identity is absent or incomplete, emit
verdict="skip" and explain in task_type_reason.

--- Region decision procedure ---
1. Read the announcement body for region restrictions (e.g. "available in
   us-east-1 only", "launching in 5 regions"). If present, pick one of those.
2. If the announcement names concrete services/APIs/CFN types, call
   aws_knowledge_region to cross-verify supported regions.
3. Decision:
   - Announcement restricts region  -> use a restricted region
   - Announcement has no restriction -> default to us-east-1
   - aws_knowledge_region errors / returns empty -> default to us-east-1,
     and note "tool returned no data; defaulting" in region_reason
4. region MUST be in the whitelist {us-east-1, us-west-2, ap-southeast-1}.
   If the only allowed region falls outside the whitelist, set
   region="us-east-1" and region_reason must explain the fallback.

region_reason format: ONE line, <=200 chars, MUST cite evidence.
  Good: "announcement §2 says available in us-west-2 only"
  Good: "aws_knowledge_region returned [us-east-1, us-west-2, eu-west-1]; picked us-east-1 as default"
  Bad: "selected us-east-1" (no evidence)
  Bad: "" (empty)

--- tag_strategy (exact 3 keys are MANDATORY) ---
You MUST emit these three keys verbatim:
  "autopilot:task_id": "<task_id from prompt>"
  "autopilot:stage":   "execute"
  "autopilot:owner":   "archie"
You MAY add extra descriptive keys (e.g. "autopilot:service": "bedrock")
but the 3 above are non-negotiable.

--- budget_limit_usd (USD, static estimate) ---
  task_type=api_call          -> budget <= 1
  task_type=infrastructure    -> budget <= 20
  task_type=mixed             -> budget <= 20  (treat as infrastructure)
  estimated_execution_minutes > 180 (long-running) -> budget <= 100
If your estimate would exceed the ceiling, you MUST shrink the plan
(fewer tests, smaller instance class, shorter TTL) and re-emit.

--- cleanup_policy ---
  ttl_hours by task_type:
    api_call         -> ttl_hours <= 0.5
    infrastructure   -> ttl_hours <= 4
    long-running     -> ttl_hours <= 8 (must also note justification in task_type_reason)
  on_failure: "terminate_all" (default) | "preserve_for_debug" | "ask_human"
  orphan_scan: true (default, let Gap 9 scanner see it) | false

--- prerequisites (list; may be empty) ---
Only these 3 types are supported in Stage 1. Emit exactly these shapes:
  {"type": "bedrock_model_access", "description": "Claude Opus 4.7 available",
   "params": {"model_id": "global.anthropic.claude-opus-4-7"}}
  {"type": "service_quota", "description": "Running On-Demand G instances quota",
   "params": {"service": "ec2", "quota_code": "L-DB2E81BA", "required": 4}}
  {"type": "bucket_exists", "description": "artifacts bucket reused across tasks",
   "params": {"bucket_name": "autopilot-artifacts-us-east-1"}}
If the test touches Bedrock, MUST include at least one bedrock_model_access.

--- vpc_preference ---
  "none"              -> pure API calls, no VPC
  "default_vpc"       -> use the account's default VPC
  "lab_vpc_required"  -> Execute will create a purpose-built lab VPC

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

CRITICAL: All values in the JSON output MUST be valid JSON string literals. Never write Python
expressions (no + concatenation, no * repetition, no f-strings, no code). For boundary test cases
that would require very long strings, use a short descriptive placeholder like "<65530_char_string>"
or "<command_65536_chars>" instead.

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
  "estimated_execution_minutes": 10,
  "environment": {
    "region": "us-east-1",
    "region_reason": "announcement has no region restriction; defaulting per whitelist policy",
    "account_id": "<copy from AWS Identity in prompt>",
    "vpc_preference": "none",
    "tag_strategy": {
      "autopilot:task_id": "<task_id from prompt>",
      "autopilot:stage": "execute",
      "autopilot:owner": "archie"
    },
    "budget_limit_usd": 1.0,
    "cleanup_policy": {
      "ttl_hours": 0.5,
      "on_failure": "terminate_all",
      "orphan_scan": true
    },
    "prerequisites": [
      {
        "type": "bedrock_model_access",
        "description": "Claude Opus 4.7 on-demand in us-east-1",
        "params": {"model_id": "global.anthropic.claude-opus-4-7"}
      }
    ]
  }
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
  "estimated_execution_minutes": 0,
  "environment": null
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
    # Stage 1: environment is required when verdict=go, null when skip.
    # Post-parser in api layer is responsible for downgrading verdict=go
    # with missing environment into needs_human (spec §8).
    "environment": None,
}


_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ ")


def _strip_log_lines_and_parse(text: str) -> dict:
    """Remove interleaved Python log lines from JSON text and parse."""
    lines = text.splitlines()
    cleaned = [line for line in lines if not _LOG_LINE_RE.match(line)]
    return json.loads("\n".join(cleaned))


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
                candidate = text[brace_pos : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try stripping interleaved Python log lines
                    return _strip_log_lines_and_parse(candidate)

    # Fallback: try from the first brace to end, with log-line stripping
    try:
        return json.loads(text[brace_pos:])
    except json.JSONDecodeError:
        return _strip_log_lines_and_parse(text[brace_pos:])


def run_research(
    task_id: str,
    url: str,
    aws_identity: Optional[dict] = None,
) -> dict:
    """Run the Research Agent to evaluate an AWS What's New URL.

    Args:
        task_id: Unique task identifier.
        url: AWS What's New announcement URL.
        aws_identity: sts:GetCallerIdentity-shaped dict (Account/Arn/UserId
            at minimum). When None, falls back to aws_session.who_am_i().
            The Account field is injected into the Agent prompt and MUST
            end up verbatim in environment.account_id; the Agent is NOT
            allowed to pick its own account.

    Returns:
        ResearchResult dict with verdict, test_matrix, iam_policy, and (when
        verdict=go) a complete environment sub-object.

    Raises:
        RuntimeError: If the agent response cannot be parsed as JSON.
    """
    if aws_identity is None:
        # Lazy import to avoid circular dependency at module load time.
        from src.autopilot.aws_session import who_am_i
        aws_identity = who_am_i()

    bedrock_model = BedrockModel(
        model_id="global.anthropic.claude-opus-4-6-v1",
        boto_client_config=BEDROCK_CONFIG,
    )
    agent = Agent(
        model=bedrock_model,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )

    identity_line = json.dumps(
        {
            "Account": aws_identity.get("Account", ""),
            "Arn": aws_identity.get("Arn", ""),
            "UserId": aws_identity.get("UserId", ""),
        },
        separators=(",", ":"),
    )

    prompt = (
        f"Task ID: {task_id}\n"
        f"AWS What's New URL: {url}\n"
        f"AWS Identity: {identity_line}\n\n"
        "Please evaluate this announcement following the workflow steps. "
        "Return your final answer as a JSON object. "
        "If verdict=go, the 'environment' field is REQUIRED and its account_id "
        "MUST equal the Account value above verbatim."
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
