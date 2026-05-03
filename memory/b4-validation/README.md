# B4 TestEnvironment validation — Stage 1 Phase 2

Acceptance evidence for Stage 1 Phase 2 task B4 (verify `TestEnvironment`
pydantic schema + Research Agent post-parser behaviour end-to-end).

Scope fixed with weichao on 2026-05-03 (Slack thread `1777025191.262519`):

- **B4.a** — natural run of a real AWS What's New URL through
  Research Agent (Bedrock `claude-opus-4-6-v1`), cover spec §9 V1 / V4 /
  V5 / V6 / V7 (weak).
- **B4.b** — strict V7 unit test (pure local): synthesise parsed payload
  with a forged `account_id`, call `_apply_post_parser`, confirm the
  forged value is overwritten to caller identity and a warning is
  surfaced.

## B4.a — Natural run (2026-05-03)

| Field          | Value                                                                          |
|----------------|--------------------------------------------------------------------------------|
| URL            | https://aws.amazon.com/about-aws/whats-new/2026/02/structured-outputs-available-amazon-bedrock/ |
| `task_id`      | `task-724d5338b820`                                                            |
| Model          | `claude-opus-4-6-v1` (via Strands → Bedrock)                                   |
| Elapsed        | ~115s end-to-end (POST /tasks → verdict=go)                                    |
| Cost           | ≈ $0.10–0.20 (single research task)                                            |
| Result file    | `task-724d5338b820-result.json` (reconstructed from uvicorn log)              |
| Raw server log | `task-724d5338b820-uvicorn.log`                                                |

### Assertion results — spec §9

| Gate | Rule (spec §9)                                                              | Result     | Evidence                                                                                                |
|------|-----------------------------------------------------------------------------|------------|---------------------------------------------------------------------------------------------------------|
| V1   | URL with no region restriction → region=us-east-1 & reason mentions it      | ✅ pass    | `environment.region = "us-east-1"`, `region_reason` contains `"defaulting to us-east-1 per whitelist"` |
| V4   | `tag_strategy` contains the 3 mandatory keys                                | ✅ pass    | keys: `autopilot:task_id`, `autopilot:stage`, `autopilot:owner`, `autopilot:service`                   |
| V5*  | `task_type=infrastructure` → `budget_limit_usd ∈ (0, 20]` & `ttl_hours ≤ 4` | ✅ soft    | Agent chose `task_type=api_call` (not infrastructure); values budget=$1.0, ttl=0.5h still under caps. weichao 2026-05-03: "算 V5 通过". |
| V6   | Task uses Bedrock → `prerequisites` has ≥ 1 `bedrock_model_access`          | ✅ pass    | 1 entry: `{type: "bedrock_model_access", description: "Claude Sonnet 4.5 on-demand access in us-east-1"}` |
| V7w  | Weak V7: `environment.account_id == aws_identity.Account`                   | ✅ pass    | both equal `595842667825`                                                                               |

Observed side-effect (not in scope): `region_reason` contains
`aws_knowledge_region errored` — the Research Agent's MCP region tool
fired but returned an error and fell back to the whitelist default.
Diagnosed on 2026-05-03 as a code bug in `src/agents/research/tools.py`:
`filters=[service]` sends raw service codes like `"bedrock"`, but the
`aws___get_regional_availability` endpoint expects display names
(`"Amazon Bedrock"`, `"AWS Lambda"`, …). Fix tracked separately
(service→display_name mapping, route A).

## B4.b — Strict V7 unit test (2026-05-03)

Pure local test covering spec §5.1 (decision interception: `account_id`
is bound to the caller identity, not whatever the Agent emits).

- **Test file**: `test_v7_strict.py`
- **Target**: `src.agents.research.agent._apply_post_parser`
- **Runner**: `.venv/bin/python -m pytest memory/b4-validation/test_v7_strict.py -v`
- **Evidence**: `pytest-output.txt`

| # | Case                                    | Input `account_id` | Expected              | Result |
|---|-----------------------------------------|---------------------|------------------------|--------|
| 1 | Agent forges wrong account              | `"000000000000"`    | rewritten + warning    | ✅ pass |
| 2 | Agent already emits the correct account | `"595842667825"`    | unchanged, no warning  | ✅ pass |
| 3 | Agent omits account (empty string)      | `""`                | filled + warning       | ✅ pass |

All 3 pass; together they prove §5.1 is enforced regardless of whether
the Agent is malicious (case 1), well-behaved (case 2), or silent (case 3).

## How to reproduce

### B4.a natural run

```bash
# On dev-server
cd /data/projects/chaosreload/study/repo/chaosreload/aws-lab-autopilot

# Prereqs (one-time):
#   - docker container `ddb-local` running DynamoDB Local on :8001
#   - aws profile `weichaol-testenv2-awswhatsnewtest` (account 595842667825)
#   - Bedrock us-east-1 access to claude-opus-4-6-v1
source .venv/bin/activate
export AWS_PROFILE=weichaol-testenv2-awswhatsnewtest
nohup .venv/bin/python -m uvicorn src.autopilot.api:app \
    --host 127.0.0.1 --port 8000 \
    > /tmp/autopilot-uvicorn.log 2>&1 &
# wait for /healthz 200, then:
curl -sS -X POST http://127.0.0.1:8000/tasks \
     -H "Content-Type: application/json" \
     -d '{"url":"https://aws.amazon.com/about-aws/whats-new/2026/02/structured-outputs-available-amazon-bedrock/"}'
# poll GET /tasks/{id} until status=research_done (~2 min)
```

### B4.b unit test

```bash
cd /data/projects/chaosreload/study/repo/chaosreload/aws-lab-autopilot
.venv/bin/python -m pytest memory/b4-validation/test_v7_strict.py -v
```

## Files

| File                                         | Purpose                                  |
|----------------------------------------------|------------------------------------------|
| `README.md`                                  | This document                            |
| `task-724d5338b820-result.json`              | Cleaned `ResearchResult` (B4.a evidence) |
| `task-724d5338b820-uvicorn.log`              | Raw uvicorn server log (B4.a trace)      |
| `test_v7_strict.py`                          | Pytest suite (B4.b)                      |
| `pytest-output.txt`                          | Pytest run output (B4.b evidence)        |

## Known follow-ups (not in B4)

1. **aws_knowledge_region bug** — fix filter format in
   `src/agents/research/tools.py`. Route A (service→display_name mapping)
   chosen by weichao 2026-05-03. Tracked as a separate task.
2. **aws_knowledge_read** — not checked; may have similar format issue.
3. Strict V5 (infrastructure-type task hits budget/ttl caps) — deferred
   until we run an infrastructure-shaped URL through the pipeline.
