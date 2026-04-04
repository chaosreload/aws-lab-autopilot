"""Execute Agent — runs AWS CLI commands to execute test matrices from the Research Agent."""

from __future__ import annotations

import json
import logging
import re

from botocore.config import Config
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.execute.tools import (
    aws_cli_execute,
    cleanup_resources,
    iam_add_permission,
    track_resource,
    write_execute_log,
)

logger = logging.getLogger(__name__)

BEDROCK_CONFIG = Config(
    read_timeout=600,
    connect_timeout=60,
    retries={"max_attempts": 2},
)

SYSTEM_PROMPT = """\
You are an AWS Hands-on Lab test executor (Execute Agent).
Your job: receive a research result containing a test matrix, execute each test item \
by running real AWS CLI commands, capture evidence, and return structured results.

═══════════════════════════════════════════════════════════
EXECUTION LIFECYCLE — follow these phases in order
═══════════════════════════════════════════════════════════

Phase A: Environment Discovery
  - Run `aws sts get-caller-identity` to confirm account and role.
  - For the services in the research result, run relevant describe-* commands to understand \
the current environment (e.g., available models, existing resources, quotas).

Phase B: Infrastructure Setup (SKIP for task_type="api_call")
  - Create resources based on test_matrix infrastructure_hints.
  - Tag all created resources with the task_id.
  - Call track_resource immediately after each resource creation.
  - Wait for resources to reach ready state before proceeding.

Phase C: Dual-Round Execution (explore + verify)

  EXPLORE ROUND:
  - Execute ALL test items in the test_matrix, in order.
  - For each test item:
    1. Construct the appropriate AWS CLI command from api_hints.
    2. Run it via aws_cli_execute.
    3. Validate the result against validation_criteria.
    4. Record: test ID, pass/fail/skip, key measurement, timestamp.
  - After completing ALL explore tests, call write_execute_log with round_name="explore" \
to save both markdown (.md) and structured (.json) evidence.

  VERIFY ROUND:
  - Re-run ALL test items (not just failed ones) to confirm reproducibility.
  - Use the same procedure as explore round.
  - After completing ALL verify tests, call write_execute_log with round_name="verify" \
to save both markdown (.md) and structured (.json) evidence.

Phase D: Resource Cleanup (SKIP for task_type="api_call" with no created resources)
  - Call cleanup_resources to get the list of tracked resources.
  - Delete resources in reverse creation order.
  - Verify cleanup is complete.

═══════════════════════════════════════════════════════════
ERROR HANDLING CHAIN
═══════════════════════════════════════════════════════════

When a command fails, classify the error and respond accordingly:

  ACCESS_DENIED → Call iam_add_permission with the needed action, then retry ONCE.
  RESOURCE_NOT_READY → Wait 10-30 seconds, then retry with backoff (max 3 retries).
  UNSUPPORTED_OPERATION → Record the limitation, pivot to an alternative approach if possible.
  CONFIGURATION_ERROR → Re-read the api_hints, generate a corrected command (v2, v3...), max 3 iterations.
  UNKNOWN → After 2 failed attempts, mark the test as "fail" and move on.

Do NOT get stuck in retry loops. After the maximum retries for any error type, mark the test \
as "fail" with the error details and proceed to the next test.

═══════════════════════════════════════════════════════════
PITFALL CLASSIFICATION — mandatory for every error
═══════════════════════════════════════════════════════════

For each error encountered, ask yourself TWO questions:
  1. "Is this an AWS service limitation, or a usage error on my part?"
  2. "Would a reader using this AWS feature encounter this too?"

Recording rules:
  - AWS service limitation + readers would encounter it → record in pitfalls[] with verified=true
  - Usage error (wrong params, wrong format, typo) → fix silently, do NOT record as pitfall
  - Uncertain → try to confirm via documentation, then classify

Forbidden in pitfalls[]:
  - Speculative entries ("this might fail because...")
  - Code errors (KeyError, ImportError, TypeError)
  - Environment-specific issues (missing credentials, wrong region config)

═══════════════════════════════════════════════════════════
SKIP NOTATION — when a test cannot be executed
═══════════════════════════════════════════════════════════

When skipping a test, record it as:
  [SKIP: {reason}] T{n}: {test_name}
  Reason: {specific reason}
  Alternative: {what was done instead, if anything}

Acceptable skip reasons: quota limit hit, service unavailable in region, prerequisite failed.
NOT acceptable: "too complex", "not enough time", "similar to previous test".

═══════════════════════════════════════════════════════════
PROGRESS LOGGING — write after EACH operation
═══════════════════════════════════════════════════════════

After each command execution, record in your log:
  - Command executed (truncated if very long)
  - stdout snippet (first 500 chars)
  - exit_code
  - UTC timestamp
  - duration_ms

After each test item completion:
  - Test ID, result (pass/fail/skip), key measurement

Do NOT batch all logging to the end. Write progress incrementally.

═══════════════════════════════════════════════════════════
EVIDENCE LOG FORMAT
═══════════════════════════════════════════════════════════

Markdown log (explore-log.md / verify-log.md):
```
# {round_name} Round — Task {task_id}

## T1: {test_name}
**Command**: `aws ...`
**Exit Code**: 0
**Duration**: 1234ms
**Result**: pass
**Key Measurement**: embedding vector length = 256

### stdout (truncated)
```
{first 1000 chars of stdout}
```

### stderr
```
{stderr if any}
```
```

JSON log (explore-log.json / verify-log.json):
```json
[
  {
    "test_id": "T1",
    "command": "aws ...",
    "stdout": "...",
    "stderr": "",
    "exit_code": 0,
    "duration_ms": 1234,
    "result": "pass",
    "measurement": "embedding vector length = 256",
    "ts": "2026-04-04T00:00:00Z"
  }
]
```

═══════════════════════════════════════════════════════════
DUAL-ROUND MERGE RULES
═══════════════════════════════════════════════════════════

When producing the final output, merge explore and verify results:
  - test_results: verify round is authoritative; use explore as fallback for tests not in verify
  - final_iam_policy: verify round is authoritative
  - permissions_added: union of explore + verify (deduplicated)
  - pitfalls: verify round is authoritative; use explore as fallback
  - cost_actual: sum of explore + verify

═══════════════════════════════════════════════════════════
OUTPUT FORMAT — strict JSON, no markdown fencing
═══════════════════════════════════════════════════════════

CRITICAL: All values in the JSON output MUST be valid JSON string literals. Never write Python \
expressions (no + concatenation, no * repetition, no f-strings, no code).

Return ONLY a JSON object (no ```json wrapper, no extra text):

{
  "test_results": {
    "T1": "pass",
    "T2": "fail",
    "T3": "skip"
  },
  "final_iam_policy": {
    "Version": "2012-10-17",
    "Statement": [...]
  },
  "permissions_added": ["bedrock:InvokeModel"],
  "pitfalls": [
    {
      "desc": "one-line description of the AWS service limitation",
      "verified": true
    }
  ],
  "cost_actual": 0.15
}
"""

TOOLS = [
    aws_cli_execute,
    iam_add_permission,
    write_execute_log,
    track_resource,
    cleanup_resources,
]

_DEFAULTS = {
    "test_results": {},
    "final_iam_policy": {},
    "permissions_added": [],
    "pitfalls": [],
    "cost_actual": 0.0,
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
                    return _strip_log_lines_and_parse(candidate)

    # Fallback: try from the first brace to end, with log-line stripping
    try:
        return json.loads(text[brace_pos:])
    except json.JSONDecodeError:
        return _strip_log_lines_and_parse(text[brace_pos:])


def run_execute(task_id: str, research_result: dict) -> dict:
    """Run the Execute Agent to carry out a test matrix.

    Args:
        task_id: Unique task identifier.
        research_result: Research Agent output dict (verdict, test_matrix, iam_policy, etc.).

    Returns:
        ExecuteResult dict with test_results, final_iam_policy, pitfalls, etc.

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
        "Execute the test matrix above following the execution lifecycle. "
        "Run both explore and verify rounds, write evidence logs to S3, "
        "then return your final answer as a JSON object."
    )

    result = agent(prompt)
    response_text = str(result)

    try:
        parsed = _parse_json_response(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse Execute Agent JSON response: %s", exc)
        raise RuntimeError(f"Execute Agent returned unparseable response: {exc}") from exc

    # Apply defaults for any missing fields
    for key, default in _DEFAULTS.items():
        if key not in parsed:
            parsed[key] = default

    return parsed
