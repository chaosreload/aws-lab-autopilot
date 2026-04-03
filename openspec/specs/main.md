# aws-lab-autopilot — Main Spec

_基于 100 篇实战经验的标准化技术文章生产流水线_
_状态机已通过 12 场景 / 17 状态 100% 覆盖验证_
_Design Doc v3.2 → OpenSpec 整理版_

---

## Overview

自动化将 AWS What's New 转成 Hands-on Lab 技术文章的 AWS 原生流水线。

**输入**：AWS What's New URL（HTTP API 提交）
**输出**：标准化的 Hands-on Lab 技术文章（Markdown，含实测数据、踩坑记录、IAM Policy、费用明细、清理脚本）

### Requirement: Pipeline Input/Output
The system MUST accept an AWS What's New URL via HTTP POST API.
The system MUST produce a standardized Hands-on Lab article (Markdown) with test data, pitfalls, IAM policy, and cost breakdown.
The system MUST persist all intermediate outputs (research notes, execution evidence, articles) to S3.

### Requirement: Technology Stack
The system SHALL use the following technology choices:

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Agent framework | Strands Agents (Python) | @tool decorator, Structured Output, native Bedrock support |
| Agent runtime | AgentCore Runtime | Session Storage + Shell Command API + isolated containers |
| Agent memory | AgentCore Memory | Semantic strategy, cross-task knowledge accumulation |
| Orchestrator | Step Functions | State machine mapping, built-in retry/catch/timeout, visualization |
| Async decoupling | SQS | Agent-to-agent messaging, buffering, retry |
| Task state | DynamoDB | Low-latency state queries, GSI for state/date filtering |
| File storage | S3 | Notes, evidence, articles, test data |
| HTTP API | API Gateway + Lambda | HTTP interface, async task submission |
| Publishing | GitHub (MkDocs) | Git push triggers GitHub Actions auto-deploy |

---

## Architecture

### Requirement: Three-Agent Architecture
The system MUST implement three specialized Strands Agents:
- **Research Agent** (Claude Opus 4.6): evaluation, document research, test matrix design, IAM policy derivation
- **Execute Agent** (Claude Sonnet 4.6): AWS CLI execution, IAM dynamic permission adding, dual-round testing
- **Publish Agent** (Claude Sonnet 4.6): calibration, article writing, quality checks, GitHub publish

Agent responsibilities are split by **cognitive boundary**, not by step:

| Agent | Model | Steps | Core Capability | AWS Permissions |
|-------|-------|-------|-----------------|-----------------|
| Research | Opus 4.6 | ①②③ + IAM derivation | Understand new features, design experiments | Read-only (aws-knowledge) |
| Execute | Sonnet 4.6 | IAM create + ④explore + cleanup + ④verify | Command execution, error handling, dynamic IAM | Read-write (Scoped Role) |
| Publish | Sonnet 4.6 | ④.5 calibrate + ⑤write/publish + ⑥archive | Calibration, writing, publishing | Read-only (aws-knowledge) + Git |

### Requirement: Step Functions Orchestration
The system MUST use AWS Step Functions as the deterministic orchestrator with exactly 17 states.
The system MUST NOT use LLM for orchestration decisions.

### Requirement: SQS Integration
The system MUST use SQS queues (research/execute/publish) to decouple Step Functions from AgentCore Runtime.
Each queue MUST have a Dead Letter Queue with 14-day retention.

### Requirement: AgentCore Runtime
Each agent MUST run inside AgentCore Runtime containers.
The Execute Agent MUST use Shell Command API for AWS CLI execution.

### Requirement: SQS-to-AgentCore Integration Pattern
The system MUST implement the following bridge pattern:

```
Step Functions (waitForTaskToken)
  → SQS Queue (research / execute / publish)
  → Lambda (sqs_handler)
      1. Parse SQS message (contains TaskToken)
      2. Invoke agent directly (run_research / run_execute / run_publish)
      3. Wait for agent completion
      4. sfn.send_task_success(token, output)
  → Agent completes → Lambda callbacks Step Functions
```

---

## Research Agent

### Requirement: Research Agent Tools
The Research Agent MUST expose the following Strands tools:
- `aws_knowledge_read` — search and read AWS official documentation
- `aws_knowledge_region` — query service regional availability
- `list_bedrock_models` — list available Bedrock foundation models (verify model IDs before use)
- `write_notes` — write research notes to S3
- `memory_search` — query AgentCore Memory for historical pitfall records

### Requirement: Research Agent Input/Output
The Research Agent MUST accept a `(task_id, url)` input and return the following JSON:

```json
{
  "verdict": "go",
  "notes_path": "s3://bucket/tasks/{id}/notes.md",
  "test_matrix": [
    {"id": "T1", "name": "核心 API 调用验证", "priority": "P0", "api_hints": {...}},
    {"id": "T2", "name": "对比测试", "priority": "P0", "api_hints": {...}},
    {"id": "T3", "name": "边界条件", "priority": "P0", "api_hints": {...}},
    {"id": "T4", "name": "错误处理（合并）", "priority": "P1", "api_hints": {...}}
  ],
  "iam_policy": {"Version": "2012-10-17", "Statement": [...]},
  "services": ["bedrock-runtime", "cloudwatch", "s3"]
}
```

### Requirement: Research Agent Verdict Logic
The Research Agent MUST give "go" verdict when the announcement involves any of:
1. New AWS service or feature (has API/CLI)
2. New model, algorithm, or data format (callable API)
3. New service configuration, parameter, or permission pattern (can create resources to verify)
4. Important update to existing service (can compare old vs new behavior)

The Research Agent MUST give "skip" verdict ONLY for:
1. Pure regional expansion (no new features)
2. Pure pricing change (no functional change)
3. Pure console UI improvement (no API/CLI change)
4. Service deprecation notice
5. Pure documentation update

### Requirement: Research Agent Test Matrix Design
The Research Agent MUST design a test matrix with total ≤ 8 items:
- **Type A (Valid value tests)**: max 3 items — core API call, comparison test, enum coverage merged into 1
- **Type B (Invalid value tests)**: merged into 1 item — all invalid values combined
- **Type C (Boundary tests)**: merged into 1 item — empty input, oversized input, type errors combined

The Research Agent MUST call `list_bedrock_models` to verify actual model IDs before including them in `api_hints.model_id`. Documentation model IDs may be outdated.

### Requirement: Research Agent IAM Derivation
The Research Agent MUST derive a minimal IAM policy containing only the actions needed for the test matrix.
The derived policy MUST be included in the output as `iam_policy`.

---

## Execute Agent

### Requirement: Execute Agent Tools
The Execute Agent MUST expose the following Strands tools:
- `aws_cli_execute` — execute AWS CLI commands via subprocess with SafetyGuard pre-check
- `python_execute` — execute Python code snippets via subprocess
- `iam_add_permission` — dynamically add IAM permission to scoped role after safety validation
- `track_resource` — register created AWS resources for tracking and cleanup
- `cleanup_resources` — mark all tracked resources for a task as deleted
- `write_execute_log` — write execution log (Markdown + JSON evidence) to S3
- `memory_create` — record pitfall to AgentCore Memory (stub in Phase 1)
- `aws_knowledge_read` — read AWS documentation when errors occur

### Requirement: Execute Agent Input/Output
The Execute Agent MUST accept `(task_id, research_result)` and return:

```json
{
  "test_results": {"T1": "pass", "T2": "fail"},
  "final_iam_policy": {"Version": "2012-10-17", "Statement": [...]},
  "permissions_added": ["s3:CreateBucket", "s3:PutObject"],
  "pitfalls": [{"desc": "...", "verified": true}],
  "cost_actual": 0.82
}
```

### Requirement: Dual-Round Execution
The Execute Agent MUST perform two execution rounds:

```
Round 1 (Explore):
  Execute test matrix → AccessDenied? → iam_add_permission → retry
  Errors → aws_knowledge_read → diagnose → fix or record as pitfall
  → write_execute_log (checkpoint)
  → cleanup_resources (keep IAM Role)

Round 2 (Verify):
  Fresh agent context + final IAM policy
  Re-execute all tests from scratch in clean environment
  → write_execute_log (final evidence)
  → cleanup_resources (including IAM Role)

Merge: verify round is authoritative for test_results
```

Each round MUST have a 420-second timeout. The system MUST call `write_execute_log` before returning even if not all tests completed.

### Requirement: Execute Agent Error Handling
When an error occurs, the Execute Agent MUST follow this 4-step chain:
1. Record error to S3 evidence log
2. Call `aws_knowledge_read` to check documentation
3. LLM judges: operational error? AWS limitation? Unknown?
4a. Operational error → fix and retry
4b. AWS limitation → call `memory_create` to record → mark as pitfall and continue
4c. Unknown → return `NEEDS_HUMAN`

---

## Publish Agent

### Requirement: Publish Agent Tools
The Publish Agent MUST expose the following Strands tools:
- `read_research_notes` — read research notes from S3
- `read_execute_results` — read verify-log.md + verify-log.json evidence from S3
- `aws_knowledge_read_publish` — search AWS documentation for calibration (minimum 3 calls required)
- `quality_check` — run 7 quality red-line checks on article
- `write_article` — write article to S3, optionally update DynamoDB title
- `generate_preview_url` — generate 24-hour pre-signed S3 URL
- `git_push` — push article to GitHub via REST API (called only from /tasks/{id}/approve endpoint)
- `memory_search` — query AgentCore Memory for historical calibration records

### Requirement: Publish Agent Input/Output
The Publish Agent MUST accept `(task_id, research_result, execute_result)` and return:

```json
{
  "quality_passed": true,
  "article_path": "s3://bucket/tasks/{id}/article.md",
  "preview_url": "https://...",
  "published_url": null,
  "calibration": {"verified": 3, "corrected": 1, "undocumented": 0}
}
```

### Requirement: Publish Agent Rework Output Convention
If rework is needed, the Publish Agent MUST return `rework_needed: true` with `rework_type`.
If no rework is needed, the Publish Agent MUST NOT include the `rework_needed` field in output.
Step Functions uses `IsPresent` semantics to check for rework.

Rework output example:
```json
{"rework_needed": true, "rework_type": "retest_specific", "reason": "T2 data contradicts documentation"}
```

### Requirement: Publish Agent Workflow
The Publish Agent MUST follow this strict workflow:
1. **Step A (Read materials)**: Call `read_research_notes` then `read_execute_results`
2. **Step B (Calibrate)**: Call `aws_knowledge_read_publish` at minimum 3 times (one each for: model ID/ARN, API request/response schema, service limits)
3. **Step C (Write article)**: Follow the article template structure strictly
4. **Step D (Quality check)**: Call `quality_check`, fix blocking issues (max 1 self-fix round)
5. **Step E (Save and preview)**: Call `write_article` then `generate_preview_url`. Do NOT call `git_push`.

### Requirement: Article Structure (Mandatory Sections)
Every article MUST contain ALL of these sections or quality_check will fail:
1. `!!! info Lab 信息` admonition at article start (difficulty/time/cost/region/last-verified)
2. `## 核心概念` section (parameter overview table or version comparison table)
3. Step 1 MUST have AWS CLI bash code block (CLI before Python)
4. Each Step MUST end with `**发现:**` line (minimum 3 Steps = minimum 3 discovery blocks)
5. `## 测试结果` section (summary table, one row per Step)
6. `!!! warning 踩坑 N: ...` admonition (each pitfall as warning admonition, production-level only)
7. `## 费用明细` section (table)
8. `## 清理资源` section (with `!!! danger` admonition)
9. `## 结论与建议` section (scenario-based recommendation table)

### Requirement: Publish Agent Evidence-First Principle
ALL of the following content in the article MUST come from `read_execute_results` actual stdout/stderr data:
- API response format and JSON key paths
- Error message text
- Performance latency numbers
- Model IDs

The Publish Agent MUST NOT generate these from LLM memory. If `read_execute_results` returns empty, the article MUST mark content as "未验证数据".

---

## Step Functions State Machine

### Requirement: 17-State Workflow
The system MUST implement exactly these 17 Step Functions states:

| State | Type | Responsibility |
|-------|------|----------------|
| Research | Task (SQS waitForTaskToken) | Send to research queue, wait for Research Agent result |
| CheckResearchVerdict | Choice | Route: skip → Skipped, go → StartExecute |
| StartExecute | Task (Lambda) | Invoke AgentCore Bridge (start action) |
| WaitForExecute | Wait | Wait 60s for Execute Agent |
| CheckExecuteStatus | Task (Lambda) | Invoke AgentCore Bridge (check action) |
| IsExecuteDone | Choice | Route: completed → Publish, else → WaitForExecute |
| Publish | Task (SQS waitForTaskToken) | Send to publish queue, wait for Publish Agent result |
| CheckPublishResult | Choice | Route: rework_needed IsPresent → IncrementRework, else → UpdateCompleted |
| IncrementRework | Task (Lambda) | Increment rework_count in DynamoDB |
| CheckReworkLimit | Choice | Route: count>2 → NeedsHuman, redesign → Research, retest_* → Execute |
| UpdateCompleted | Task (DynamoDB) | Update task state to "completed" |
| NotifyComplete | Task (SNS) | Publish completion notification |
| Completed | Succeed | Terminal success state |
| Skipped | Succeed | Terminal skip state (verdict=skip) |
| NeedsHuman | Task (SNS) | Publish alert for human review |
| Failed | Fail | Terminal failure state |
| UpdateSkipped | Task (DynamoDB) | Update task state to "skipped" |

### Requirement: Rework Loop
The system MUST support up to 2 rework loops.
The system MUST escalate to human review (SNS alert) when rework_count > 2.

Three rework paths MUST be supported:

| Rework Type | Step Functions Routing | Real-world Example |
|-------------|----------------------|---------------------|
| `retest_specific` | CheckReworkLimit → Execute | AgentCore pitfall was actually an operational error |
| `retest_all` | CheckReworkLimit → Execute (with cleanup) | DMS initial test data volume too small |
| `redesign` | CheckReworkLimit → Research | Bedrock Benchmark didn't use latest model |

### Requirement: Step Functions State Machine Definition
The state machine MUST be defined using the following parameters (with variable substitutions):
- `${ResearchQueueUrl}` — Research SQS queue URL
- `${AgentCoreBridgeFn}` — AgentCore Bridge Lambda ARN
- `${PublishQueueUrl}` — Publish SQS queue URL
- `${IncrementReworkFn}` — IncrementRework Lambda ARN
- `${NotifyTopic}` — SNS notifications topic ARN
- `${AlertTopic}` — SNS alerts topic ARN

---

## IAM Three-Layer Model

### Requirement: IAM Three-Layer Model
The system MUST implement three IAM layers for lab execution:

**Layer 1 — Base Policy (shared across all tasks, constant):**
```json
{
  "Statement": [
    {"Sid": "BaseCloudWatchLogs", "Effect": "Allow",
     "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLog*"],
     "Resource": "arn:aws:logs:*:*:*"},
    {"Sid": "BaseCloudWatchMetrics", "Effect": "Allow",
     "Action": ["cloudwatch:PutMetricData", "cloudwatch:GetMetricData", "cloudwatch:ListMetrics"],
     "Resource": "*"},
    {"Sid": "BaseSTSSelf", "Effect": "Allow", "Action": ["sts:GetCallerIdentity"], "Resource": "*"},
    {"Sid": "BaseDenyEscalation", "Effect": "Deny",
     "Action": ["iam:CreateUser", "iam:CreateLoginProfile", "iam:CreateAccessKey",
                "iam:AttachUserPolicy", "iam:PutUserPolicy", "iam:AddUserToGroup",
                "organizations:*", "account:*"],
     "Resource": "*"}
  ]
}
```

**Layer 2 — Service Policy (LLM-derived + dynamically added on AccessDenied):**
- Research Agent derives initial ~80% accurate policy from test matrix
- Execute Agent appends permissions dynamically when `AccessDenied` is encountered
- `iam_add_permission` tool checks SafetyGuard before each addition

**Layer 3 — Safety Deny (hardcoded, never overridable):**
- Block `ec2:AuthorizeSecurityGroupIngress` where CidrIp = `0.0.0.0/0` or `::/0`
- Block IAM user creation: `iam:CreateUser`, `iam:AttachUserPolicy`, `iam:CreateLoginProfile`
- Block EC2 instance launch: `ec2:RunInstances`, `ec2:RequestSpotInstances`, `ec2:StartInstances`
- Block RDS instance creation: `rds:CreateDBInstance`, `rds:RestoreDBInstanceFromDBSnapshot`
- Block destructive actions matching `.*:Delete*`, `.*:Terminate*`

### Requirement: Dynamic Permission Adding
When `AccessDenied` is encountered during Execute Agent:
1. SafetyGuard.check_iam_action(action) — verify action is not in Layer 3 deny list
2. If allowed: `iam_add_permission(role_name, action, resource)` — attach inline policy to scoped role
3. Retry the failed command

### Requirement: Scoped IAM Role Lifecycle
The system MUST create a per-task scoped IAM role with naming pattern `handson-lab-{task_id[:32]}`.
The scoped role MUST be cleaned up after the verify round completes (including all inline policies).

---

## Safety Guard

### Requirement: Safety Guard Pre-Execution
The SafetyGuard MUST validate every AWS CLI command before execution:
- Extract service name from `aws <service> ...` pattern
- Verify service is in the allowed service allowlist
- Block commands containing open CIDR patterns (`0.0.0.0/0` or `::/0`)

### Requirement: Safety Guard IAM Action Check
Before adding any IAM permission dynamically, SafetyGuard.check_iam_action MUST:
- Match action against deny patterns:
  - `iam:CreateUser`, `iam:CreateLoginProfile`, `iam:CreateAccessKey`
  - `iam:AttachUserPolicy`, `iam:PutUserPolicy`, `iam:AddUserToGroup`
  - `organizations:.*`, `account:.*`
  - `ec2:RunInstances`, `ec2:RequestSpotInstances`, `ec2:StartInstances`
  - `rds:CreateDBInstance`, `rds:RestoreDBInstanceFromDBSnapshot`
  - `.*:Delete*`, `.*:Terminate*`
- Block if matched, allowing only if no pattern matches

### Requirement: Safety Guard Service Allowlist
The SafetyGuard MUST maintain an explicit allowlist of permitted AWS services. Any service not in the allowlist MUST be blocked. The allowlist covers major compute/storage/AI/data services including but not limited to: bedrock, bedrock-runtime, bedrock-agent, bedrock-agentcore, s3, dynamodb, lambda, ec2, ecs, eks, sqs, sns, stepfunctions, iam, cloudwatch, logs, rds, opensearch, sagemaker, etc.

---

## Quality Gate (7 Checks)

### Requirement: Quality Gate (7 Red Lines)
The Publish Agent MUST pass ALL 7 quality checks before publishing. Failing any check MUST block publication.

| # | Check | Pass Criteria |
|---|-------|---------------|
| 1 | **reproducible** | ≥2 code blocks AND at least one AWS CLI bash block |
| 2 | **has_data** | Has Markdown table AND precision numbers (≥3 decimal places) AND no placeholder text (`...` / `预期输出` / `TBD`) |
| 3 | **has_boundary** | Contains boundary/limit/ValidationException/overflow keywords |
| 4 | **has_cost** | Contains cost/cleanup/$ keywords |
| 5 | **has_pitfall** | Pitfall section exists AND has error evidence keywords AND uses `!!! warning` admonition format AND no speculative language |
| 6 | **calibrated** | Has `!!! info Lab 信息` admonition AND `## 核心概念` section AND ≥3 `**发现:**` blocks |
| 7 | **has_iam** | Contains IAM/policy/permission keywords |

### Requirement: Pitfall Classification Rules
Pitfalls MUST be classified before writing:
- **Include in article** (as `!!! warning` admonition): AWS product limitations, undocumented official behaviors, issues all users of the feature would encounter
- **Internal log only** (not in article): Code KeyError/TypeError, ImportError, environment configuration issues
- **Decision rule**: Would all users of this AWS feature potentially hit this? → article; else → internal log only

---

## Data Storage

### Requirement: S3 Storage Structure
The system MUST use the following S3 path structure:

```
s3://handson-workflow-{account}-{region}/
├── tasks/{task-id}/
│   ├── notes.md                    # Research notes
│   ├── evidence/
│   │   ├── explore-log.md          # Explore round Markdown log
│   │   ├── explore-log.json        # Explore round structured evidence
│   │   ├── verify-log.md           # Verify round Markdown log
│   │   └── verify-log.json         # Verify round structured evidence (authoritative)
│   └── article.md                  # Article draft
├── templates/
│   └── article.md                  # Article template
└── prompts/                        # LLM prompt templates
```

### Requirement: DynamoDB Tasks Table
The system MUST implement `handson-tasks` table with the following schema:

```
Table: handson-tasks
  PK: task_id (string)
  Attributes:
    url              - What's New URL
    state            - TaskState: queued | researching | executing | publishing | completed | skipped | failed | needs_human | cancelled
    rework_count     - int (default 0)
    created_at       - ISO timestamp
    created_date     - YYYY-MM-DD (for GSI)
    updated_at       - ISO timestamp
    article_title    - string (set by Publish Agent)
    preview_url      - S3 pre-signed URL (24h)
    published_url    - GitHub Pages URL (set on approve)
    publish_result   - map (full publish agent output)
    callback_url     - Webhook callback URL (optional)
    notify_slack     - Slack channel (optional)
    config_override  - map (optional overrides)

  GSI: state-index
    PK: state, SK: updated_at
    Purpose: query in-progress / failed / needs-human tasks

  GSI: date-index
    PK: created_date, SK: created_at
    Purpose: query by date
```

### Requirement: DynamoDB Resources Table
The system MUST implement `handson-resources` table with the following schema:

```
Table: handson-resources
  PK: task_id (string)
  SK: resource_arn (string)
  Attributes:
    resource_type    - e.g. "s3:bucket", "lambda:function"
    region           - AWS region
    status           - active | deleted
    created_at       - ISO timestamp
    deleted_at       - ISO timestamp (nullable)
```

---

## HTTP API

### Requirement: HTTP API Endpoints
The system MUST expose the following REST endpoints via API Gateway HTTP API:

```
POST   /tasks              Create task (async, returns 202)
POST   /tasks/batch        Batch create tasks
GET    /tasks              List tasks (supports ?state= filter)
GET    /tasks/{id}         Query task status
GET    /tasks/{id}/result  Get full task result
POST   /tasks/{id}/approve Approve completed task: push article to GitHub
DELETE /tasks/{id}         Cancel task (soft-delete, state → cancelled)
```

### Requirement: Create Task Request/Response
```
Request:
{
  "url": "https://aws.amazon.com/about-aws/whats-new/...",  // required
  "callback_url": "https://your-server/webhook",             // optional
  "notify_slack": "#channel",                                // optional
  "config_override": {"region": "us-west-2", "budget": 5.0} // optional
}

Response (202):
{
  "task_id": "abc123",
  "state": "queued",
  "created_at": "2026-03-28T22:00:00Z",
  "estimated_duration": "~30 min"
}
```

### Requirement: Task Status Response
```json
{
  "task_id": "abc123",
  "url": "https://...",
  "state": "executing",
  "rework_count": 0,
  "progress": {
    "current_stage": "executing",
    "stages": ["queued", "researching", "executing", "publishing", "completed"],
    "percent": 50,
    "rework_count": 0
  },
  "created_at": "...",
  "updated_at": "..."
}
```

### Requirement: Task Result Response
```json
{
  "task_id": "abc123",
  "state": "completed",
  "url": "https://...",
  "research_result": {...},
  "execute_result": {...},
  "publish_result": {...},
  "preview_url": "https://s3-presigned...",
  "published_url": "https://chaosreload.github.io/...",
  "test_results": {"T1": "pass", "T2": "pass"},
  "rework_count": 0,
  "created_at": "...",
  "updated_at": "..."
}
```

### Requirement: Human Approval Flow
The `/tasks/{id}/approve` endpoint MUST:
1. Verify task state is "completed"
2. Verify task is not already published
3. Read article from S3 (`tasks/{id}/article.md`)
4. Push to GitHub via REST API using Secrets Manager token
5. Update DynamoDB with `published_url`
6. Return 200 with `published_url`

---

## AgentCore Memory

### Requirement: AgentCore Memory Strategy
The system MUST use AgentCore Memory with Semantic strategy for cross-task knowledge accumulation.

**Short-term memory (task-level, automatic per AgentCore Session):**
- Current test progress
- Appended IAM permissions
- Created resource list
- Error diagnosis decision history

**Long-term memory (cross-task, explicit writes):**
- Execute Agent writes pitfalls via `memory_create` when encountering AWS limitations
- Research Agent reads via `memory_search` when designing test matrices (avoid known pitfalls)
- Publish Agent reads via `memory_search` when calibrating (known discrepancies don't need re-verification)

Memory MUST be namespaced to `handson-workflow` to isolate from other projects.

> **Phase 1 Note**: `memory_search` and `memory_create` are stub implementations returning empty results. Full AgentCore Memory integration is Phase 2.

---

## Infrastructure

### Requirement: CDK Infrastructure Stack
The system MUST provision all AWS resources via CDK (aws-cdk-lib):
- DynamoDB tables: `handson-tasks`, `handson-resources` (with GSIs)
- S3 bucket: `handson-workflow-{account}-{region}` (versioned, S3-managed encryption)
- SQS queues: research/execute/publish + DLQs (visibility timeout 3600s)
- Lambda functions: ApiHandler, SqsHandler, IncrementRework, AgentCoreBridge
- Step Functions state machine: `handson-workflow` (STANDARD type, 24h timeout)
- API Gateway HTTP API with CORS
- Secrets Manager secret for GitHub config
- SNS topics: notifications + alerts
- IAM roles and policies for all Lambdas

### Requirement: Lambda Configuration
| Lambda | Handler | Timeout | Memory |
|--------|---------|---------|--------|
| ApiHandler | src.api.handler.handler | 900s | 256MB |
| SqsHandler | src.orchestrator.sqs_handler.handler | 900s | 512MB |
| IncrementRework | src.orchestrator.increment_rework.handler | 30s | 128MB |
| AgentCoreBridge | src.orchestrator.bridge.handler | 300s | 256MB |

### Requirement: Secrets Manager for GitHub
The system MUST store GitHub credentials in Secrets Manager with secret name pattern `{stack-name}/{repo-name}`.
The secret MUST contain:
```json
{
  "GITHUB_TOKEN": "ghp_...",
  "GITHUB_REPO": "chaosreload/aws-hands-on-lab",
  "GITHUB_BRANCH": "main",
  "GITHUB_ARTICLE_BASE_PATH": "docs"
}
```

---

## Cost Model

### Requirement: Cost Estimation per Article
The system SHOULD stay within the following per-article cost targets:

| Item | Estimate |
|------|----------|
| Research Agent (Opus 4.6, ~50K in + ~10K out) | ~$1.50 |
| Execute Agent (Sonnet 4.6, ~100K in + ~30K out, dual-round) | ~$0.70 |
| Publish Agent (Sonnet 4.6, ~80K in + ~20K out) | ~$0.50 |
| LLM subtotal | ~$2.70 |
| AWS resources (dual-round execution) | ~$1-2 |
| AgentCore Runtime | ~$0.10 |
| DynamoDB + S3 + SQS | ~$0.01 |
| **Total per article** | **~$4-5** |

---

## Lessons Encoded

### Requirement: Experience-to-Code Mapping
The following real-world lessons from 100 articles MUST be enforced by the system:

| Lesson | Implementation |
|--------|----------------|
| Six-step loop — no skipping | Step Functions enforces state order |
| Write to file after each step | S3 + DynamoDB real-time persistence |
| Errors: check docs before concluding | error_diagnose + aws_knowledge_read |
| aws-knowledge calibration mandatory | Publish Agent min 3 calls to aws_knowledge_read_publish |
| 0.0.0.0/0 absolutely forbidden | SafetyGuard Layer 3 Deny |
| AI tasks: always verify latest model ID | Research Agent calls list_bedrock_models |
| Evidence-first: no fabricated data | Publish Agent Evidence-First Principle |
| A/B comparison > single validation | Test matrix Type A includes comparison tests |
| Boundary conditions mandatory | Test matrix MUST include Type C boundary tests |
| Pitfalls: evidence-based only, not speculative | quality_check has_pitfall check |
| Rework loop max 2 times | CheckReworkLimit → NeedsHuman if count > 2 |
| Cross-task pitfall knowledge reuse | AgentCore Memory long-term memory (Phase 2) |
