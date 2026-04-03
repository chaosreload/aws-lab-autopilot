# Phase 2 — Design Gap Spec

_基于 openclaw-solutions 真实操作记录（Aurora 任务，365 次工具调用，~2h）的设计偏差分析_
_对照文件：`content/evidence/aurora-data-export-to-s3/full-operation-log.md`_

---

## 概述

Phase 1 实现的三个 Agent 能跑通 Bedrock API 类型的简单任务（直接调用，无需创建 AWS 资源）。
但对照真实操作记录，存在以下根本性设计缺陷，需要在 Phase 2 中系统性修复。

---

## Gap 1：Execute Agent 运行时间限制

### 问题

Lambda 最长运行 900s（15 分钟）。真实 Execute 任务需要 60-90 分钟：
- Aurora 集群启动等待：~7 分钟
- EC2 创建 + 数据灌入：~5 分钟
- AWS 服务端任务运行（Export Task、CDC 同步）：~15-30 分钟
- 踩坑调试迭代：~20 分钟

### Requirement: Execute Agent Must Run Outside Lambda

The Execute Agent MUST NOT run inside a Lambda function.
The Execute Agent MUST run in a long-lived compute environment without execution time limits.

**Accepted implementations (in priority order):**
1. AgentCore Runtime（原始设计，首选）
2. ECS Fargate Task（备选）
3. EC2 长跑进程（最后手段）

**Phase 2 target**: AgentCore Runtime

### Requirement: Publish Agent Timeout Mitigation

The Publish Agent running in Lambda MUST complete within 900 seconds.
To ensure this:
- `aws_knowledge_read_publish` calls MUST be capped at 5 via code-level counter（已实现）
- Article writing MUST complete within a single LLM call (no self-repair loops exceeding 1 round)
- If quality check fails after 1 repair round, MUST call `write_article` with current version and return（NOT retry indefinitely）

---

## Gap 2：Execute Agent 缺少基础设施生命周期管理

### 问题

真实任务中 Execute Agent 需要：
1. **环境探测**：`describe-vpcs`, `describe-subnets`, `describe-db-clusters` 了解账户现状
2. **资源创建**：Aurora cluster, EC2, S3, KMS, IAM role, Redshift Serverless 等
3. **等待就绪**：`rds wait db-cluster-available`，轮询 Integration 状态
4. **测试执行**：在资源就绪后运行测试命令
5. **结果验证**：对比源和目标的行数/数据，确认一致性
6. **资源清理**：测试完成后删除所有创建的资源

当前 Execute Agent system_prompt 只关注「执行测试矩阵」，没有覆盖基础设施全生命周期。

### Requirement: Execute Agent Infrastructure Lifecycle

The Execute Agent MUST follow this execution lifecycle:

```
Phase A: Environment Discovery
  → Probe existing AWS resources (describe-*)
  → Identify VPC, subnets, security groups available
  → Note resource quotas and service availability in current region

Phase B: Infrastructure Setup
  → Create required resources based on test_matrix.services
  → Tag all created resources with task_id for tracking
  → Register each resource via track_resource tool immediately after creation
  → Wait for resources to reach ready state before proceeding

Phase C: Test Execution (dual-round: explore + verify)
  → Execute each test item in test_matrix
  → For each test: run command → capture stdout/stderr → validate result
  → Validation MUST include data correctness check (not just exit_code=0)
  → On failure: follow error handling chain (see Gap 3)

Phase D: Result Verification
  → Cross-check: compare source vs destination counts/checksums
  → Record quantitative evidence: row counts, latency measurements, API response structure
  → Each test MUST have a pass/fail determination based on actual data, not just command success

Phase E: Resource Cleanup
  → Explore round: cleanup data resources, keep IAM role
  → Verify round: cleanup everything including IAM role
  → Verify cleanup complete (no lingering ENIs, security groups, etc.)
```

### Requirement: Execute Agent Parallel Execution Awareness

When AWS service operations have long wait times (e.g., cluster startup takes 7 minutes),
the Execute Agent SHOULD execute other independent test setup steps in parallel
rather than blocking sequentially.

---

## Gap 3：Execute Agent 错误处理决策链不完整

### 问题

真实操作中遇到 Resource Policy 错误时，openclaw-solutions 的决策逻辑是：
```
失败 → 记录失败原因 → 读相关文档 → 生成假设 → 修改方案版本（v2/v3/v4）→ 验证假设
```

关键行为：
- 失败后会**主动切换方案**（Glue Managed Catalog CLI 不支持 → 改用 Redshift Serverless）
- 生成多个版本的配置文件，逐步逼近正确答案
- 每次失败都会更新对「为什么失败」的理解

当前 Execute Agent 的「报错处理链」只有：
`报错 → 查文档 → LLM 判断 → 修正/标注/NEEDS_HUMAN`

缺少「路径切换」和「迭代假设验证」的机制。

### Requirement: Execute Agent Enhanced Error Handling

The Execute Agent MUST implement this enhanced error handling decision tree:

```
Command failure:
  1. Record: exact command, stdout, stderr, exit_code
  2. Classify error type:
     a. ACCESS_DENIED → use iam_add_permission and retry once
     b. RESOURCE_NOT_READY → wait and retry with backoff
     c. UNSUPPORTED_OPERATION → pivot to alternative approach (see below)
     d. CONFIGURATION_ERROR → read documentation, generate hypothesis, retry with modified config
     e. UNKNOWN → escalate to NEEDS_HUMAN after 2 failed attempts

  For UNSUPPORTED_OPERATION:
  → Check aws_knowledge_read for alternative APIs or approaches
  → Record decision: "Original approach X not supported, pivoting to Y because Z"
  → Continue with alternative approach

  For CONFIGURATION_ERROR (e.g., wrong policy format):
  → Read official documentation for exact format
  → Generate a versioned configuration (v2, v3...) with hypothesis noted
  → Maximum 3 configuration iterations before escalating to NEEDS_HUMAN
```

### Requirement: Execute Agent Failure Evidence Quality

Each recorded pitfall MUST include:
- The exact command that failed
- The exact error message (from stderr)
- The hypothesis for why it failed
- The fix that resolved it (or that it's unresolvable)
- Evidence that the fix worked (subsequent stdout showing success)

Speculative pitfalls ("this might fail because...") are NOT acceptable.

---

## Gap 4：Research Agent 测试设计缺少基础设施感知

### 问题

当前 Research Agent 设计的 test_matrix 只包含 API 调用（适合 Bedrock 类任务），
但对于需要创建 AWS 资源的任务，test_matrix 必须包含：
- 需要创建的基础设施（Aurora cluster, Redshift Serverless, EC2 等）
- 数据准备步骤（灌测试数据）
- 等待时间估算
- 验证标准（行数对比、CDC 延迟等）

openclaw-solutions 在 Phase 2（任务规划）阶段，笔记里已经包含了完整的测试设计，
包括预期结果和验证方式，不只是「调用什么 API」。

### Requirement: Research Agent Test Matrix Must Include Verification Criteria

Each test item in test_matrix MUST include:

```json
{
  "id": "T1",
  "name": "测试名称",
  "priority": "P0",
  "type": "api_call | infrastructure | data_validation | cdc",
  "prerequisites": ["T0_infra_setup"],
  "api_hints": {
    "service": "bedrock-runtime",
    "operation": "invoke_model",
    "request_body": {}
  },
  "infrastructure_hints": {
    "resources_needed": ["aurora:cluster", "s3:bucket"],
    "estimated_wait_minutes": 10,
    "cleanup_on_failure": true
  },
  "validation_criteria": {
    "type": "count_match | latency_bound | response_structure | data_integrity",
    "expected": "row_count_matches_source",
    "tolerance": null
  }
}
```

### Requirement: Research Agent Must Estimate Total Execution Time

The Research Agent MUST estimate total execution time for the test matrix,
including infrastructure setup wait times.
If estimated time > 30 minutes, the Agent MUST note this in notes and include
infrastructure prerequisites explicitly in the test_matrix.

---

## Gap 5：Publish Agent 证据锚定不够严格

### 问题

openclaw-solutions 的文章数字来自**亲手执行的命令输出**：
- Aurora Export: 实际 Parquet 文件大小、导出耗时
- Redshift: 实际行数对比（8810 行 source = 8810 行 target）
- CDC: 插入 3 条记录后验证同步（< 2 分钟延迟）

当前 Publish Agent 的 Evidence-First 原则虽然存在，但 system_prompt 对「如何提取和使用 evidence 数字」不够具体，LLM 可能用模糊表述代替精确数字。

### Requirement: Publish Agent Evidence Extraction Must Be Explicit

The Publish Agent MUST explicitly extract and cite the following from verify-log.json
before writing the article:

```
For each successful test:
  - Exact API response structure (field names and types)
  - Exact numeric measurements (vector dimensions, latency in ms, row counts)
  - Exact error messages from failed tests (for pitfall section)

Forbidden patterns in article:
  - "约 XXX ms" (approximate) → MUST use exact value from evidence
  - "类似 {...}" (similar to) → MUST use exact JSON from evidence
  - "通常 XXX" (usually) → MUST use measured value or omit claim
```

### Requirement: Publish Agent Calibration Must Verify Execute Evidence

In addition to calibrating against AWS documentation, the Publish Agent
MUST cross-check execute results against expected behavior:
- If execute evidence shows unexpected API behavior (e.g., different field names than documented),
  MUST note this as a pitfall
- If execute evidence is empty or incomplete, MUST mark article sections as "待验证" and NOT hallucinate data

---

## Gap 6：三个 Agent 的 Prompt 对当前任务类型感知不足

### 问题

当前三个 Agent 的 system_prompt 是为「Bedrock API 调用类任务」优化的（短任务，直接调 API）。
对于「基础设施类任务」（需要创建 AWS 资源、等待、验证），prompt 完全没有指引。

openclaw-solutions 在加载 `aws-article` SKILL.md 后，能根据任务类型自动选择合适的执行策略。

### Requirement: Task Type Classification

The Research Agent MUST classify the task type in its output:

```json
{
  "verdict": "go",
  "task_type": "api_call | infrastructure | mixed",
  "task_type_rationale": "需要创建 Aurora 集群，预计总执行时间 60-90 分钟",
  ...
}
```

Task type definitions:
- **api_call**: Tests only call existing AWS APIs, no resource creation needed. Expected execution time < 15 minutes.
- **infrastructure**: Tests require creating, waiting for, and cleaning up AWS resources. Expected execution time 30-120 minutes.
- **mixed**: Some tests are direct API calls, others require infrastructure setup.

### Requirement: Execute Agent Adapts to Task Type

Based on `task_type` from research_result:
- **api_call**: Current behavior (direct API call, dual-round, 840s max)
- **infrastructure**: Enable infrastructure lifecycle management (Gap 2), no time limit (requires AgentCore Runtime)
- **mixed**: Split tests: run api_call tests first (in Lambda), then hand off infrastructure tests to AgentCore

---

## 实现路径规划

### Phase 2a: AgentCore Runtime 接入（解决 Gap 1）

**目标**：Execute Agent 和 Publish Agent 在 AgentCore Runtime 中运行，不受 Lambda 时间限制。

**工作内容**：
1. 打包 Execute Agent 为 Docker 镜像，推 ECR，创建 AgentCore Runtime
2. 打包 Publish Agent 为 Docker 镜像，推 ECR，创建 AgentCore Runtime
3. 修改 SQS handler：触发 AgentCore Runtime（fire-and-forget）
4. 修改状态检查：轮询 S3 status marker
5. 修改 Agent 入口：完成后写 S3 status marker + `send_task_success`

**估计工作量**：M（2-3天）

### Phase 2b: 基础设施生命周期管理（解决 Gap 2、3）

**目标**：Execute Agent 能处理需要创建 AWS 资源的任务。

**工作内容**：
1. 修改 Execute Agent system_prompt：添加 Phase A-E 生命周期指引
2. 补充工具：`wait_for_resource`（封装 `aws rds wait` 等命令）
3. 强化 `track_resource`：记录资源状态、等待条件
4. 增强 `cleanup_resources`：实际调用 AWS API 删除资源（当前只是标记）

**估计工作量**：L（4-5天）

### Phase 2c: Research Agent 测试设计增强（解决 Gap 4）

**目标**：test_matrix 包含基础设施感知和验证标准。

**工作内容**：
1. 修改 test_matrix 数据结构（补充 type、prerequisites、infrastructure_hints、validation_criteria）
2. 更新 Research Agent system_prompt
3. 更新 TestItem dataclass（models.py）

**估计工作量**：S（1天）

### Phase 2d: Publish Agent 证据提取增强（解决 Gap 5、6）

**目标**：文章数字 100% 来自 evidence，不 hallucinate。

**工作内容**：
1. 修改 Publish Agent system_prompt：明确 evidence 提取步骤和禁止模式
2. 增加 evidence 预处理工具：`extract_key_measurements`（从 verify-log.json 提取关键数字）

**估计工作量**：S（1天）

---

## Gap 7：Agent Behavior Standards — 来自 aws-article SKILL.md 的缺失规范

_来源：openclaw-solutions/skills/aws-article/SKILL.md 对比分析_

### 问题

openclaw-solutions 能稳定产出合格文章，依赖 aws-article SKILL.md 中一套明确的操作规范（SOP）。
当前三个 Agent 的 system_prompt 缺少这套 SOP 的核心约束，导致执行行为不稳定。

**重要说明**：以下规范中涉及 AWS 凭证/Region 的部分，
openclaw-solutions 需要手动指定 profile 是因为在本地机器执行。
在 aws-lab-autopilot 中，Execute Agent 运行在 AgentCore Runtime 里，IAM Role 由基础设施配置注入，
Agent 不需要感知 profile，直接调用 AWS SDK/CLI 即可。Region 通过环境变量 AWS_DEFAULT_REGION 提供。

---

### Requirement: Research Agent Must Follow Structured Note Format

Research notes written to S3 MUST contain the following header:

```markdown
# [功能名称]

**Task ID**: {task_id}
**Region**: us-east-1
**Account**: (from STS get-caller-identity)
**Source URL**: {url}
**Started**: {ISO timestamp}
```

Research notes MUST contain sections in this order:
1. **① 评估结论**：verdict (go/skip) + one-line reason + complexity (S/M/L) + estimated cost
2. **② 深度研究**：technical details, API analysis, regional availability, pricing, known limitations
3. **③ 测试设计**：test matrix with at minimum 3 items, including 1 boundary test

### Requirement: Research Agent Test Design Principles

The Research Agent MUST apply these three principles when designing the test matrix:

1. **对比实验 > 单一验证**: Design A/B comparison tests, not just "it works" tests
2. **边界条件必测 (Mandatory)**: At least one test MUST cover limits, quotas, or boundary conditions
3. **Dry-run 先行**: If the AWS service supports dry-run or simulate mode, MUST include it as the first test

### Requirement: Research Agent Must Check Resource Quotas Before Test Design

Before finalizing the test matrix, the Research Agent MUST:
- Query service quotas for the relevant AWS services
- Note any limits that could block test execution (e.g., max cluster count, API rate limits)
- Include quota checks as prerequisites in infrastructure_hints if limits are likely to be hit

---

### Requirement: Execute Agent Must Write Progress After Each Step

The Execute Agent MUST write to the execution log after each individual operation, not only at the end.
This is the primary mechanism for resumability and debugging.

Specifically:
- After each AWS resource creation: log resource ID, ARN, region, creation time
- After each command execution: log command, stdout snippet, exit code, UTC timestamp
- After each test item completion: log test ID, result (pass/fail/skip), key measurement

**Why this matters**: If the agent is interrupted or times out, the log contains enough context
to resume or debug without re-running everything.

### Requirement: Execute Agent Skip Notation

When a test item cannot be executed, the Execute Agent MUST record:
```
[SKIP: {reason}] T{n}: {test_name}
Reason: {specific reason why skipped}
Alternative: {what was done instead, if anything}
```

Acceptable skip reasons: quota limit hit, service unavailable in region, prerequisite failed.
NOT acceptable: "too complex", "not enough time", "similar to previous test".

### Requirement: Execute Agent Evidence Directory Structure

The Execute Agent MUST create the following evidence structure in S3:

```
s3://bucket/tasks/{task_id}/evidence/
├── explore-log.md          # (already implemented)
├── explore-log.json        # (already implemented)
├── verify-log.md           # (already implemented)
├── verify-log.json         # (already implemented)
├── resources.md            # NEW: all created resources with ID/ARN/region/status
└── cost.json               # NEW: estimated vs actual cost per resource
```

**resources.md format**:
```markdown
# Created Resources

| Resource Type | ID/ARN | Region | Created At | Status |
|---------------|--------|--------|------------|--------|
| bedrock:inference | - | us-east-1 | 2026-04-02T14:00:00Z | cleaned |
```

**cost.json format**:
```json
{
  "estimated_usd": 0.5,
  "actual_usd": 0.15,
  "breakdown": [
    {"service": "bedrock", "operation": "InvokeModel x7", "cost_usd": 0.15}
  ]
}
```

### Requirement: Execute Agent VPC Resource Cleanup Check

After any test that creates VPC-attached resources (Lambda in VPC, RDS, ElastiCache, etc.),
the Execute Agent MUST explicitly verify ENI cleanup:

```bash
aws ec2 describe-network-interfaces --filters Name=group-id,Values={sg-id}
```

If residual ENIs exist, the Execute Agent MUST wait for them to detach before deleting the security group.
This prevents the common "dependent object" error in VPC cleanup.

### Requirement: Execute Agent Error Analysis — Pitfall Classification

For each error encountered during execution, the Execute Agent MUST classify it before recording:

**Classification questions (mandatory)**:
1. Is this an AWS service limitation, or a usage error on my part?
2. Would a reader encounter this too, or is it specific to my test setup?

**Recording rules**:
- AWS service limitation + readers would encounter it → record as pitfall in pitfalls[] with verified=true
- Usage error (wrong API params, wrong format) → fix silently, do NOT record as pitfall
- Uncertain → query aws_knowledge_read to confirm, then classify

This replaces the current speculative pitfall filter, which only blocks speculative language
but doesn't enforce evidence-based classification.

---

### Requirement: Publish Agent Pitfall Interrogation

For each pitfall from execute_result.pitfalls, the Publish Agent MUST apply this interrogation:

1. "Would ALL readers using this AWS feature encounter this?" → Yes: include in article
2. "Is this documented behavior or undocumented?" → Undocumented: must note "实测发现，官方未记录"
3. "Do I have stdout/stderr evidence?" → No evidence: cannot include in article

This is distinct from quality_check's speculative language filter.
The interrogation happens BEFORE writing, not after.

### Requirement: Publish Agent Post-Publish Checklist

After writing the article (before returning the result), the Publish Agent MUST verify:

- [ ] Article H1 title matches the article_path slug (consistent naming)
- [ ] All numeric values in the article have corresponding evidence in verify-log.json
- [ ] Cost section includes actual measured costs (not just estimates)
- [ ] Cleanup section lists the specific resources that were cleaned up (with resource types)

If any item fails, the Publish Agent MUST fix it before calling write_article.

---

### Implementation Notes

**Directly applicable to current system_prompts (no architecture change needed)**:
- Gap 7.1: Research note format → Research Agent system_prompt
- Gap 7.2: Test design principles → Research Agent system_prompt
- Gap 7.3: Quota pre-check → Research Agent system_prompt
- Gap 7.4: Step-by-step progress writing → Execute Agent system_prompt
- Gap 7.5: Skip notation → Execute Agent system_prompt
- Gap 7.6: Pitfall classification → Execute Agent system_prompt
- Gap 7.7: Pitfall interrogation → Publish Agent system_prompt
- Gap 7.8: Post-publish checklist → Publish Agent system_prompt

**Requires Phase 2 infrastructure (Gap 2b)**:
- Gap 7.9: Evidence directory (resources.md + cost.json) → needs file writing tools in Execute Agent
- Gap 7.10: VPC ENI cleanup check → needs aws_cli_execute to run check commands
