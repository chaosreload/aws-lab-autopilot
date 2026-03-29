# AWS Hands-on Workflow — 设计文档 v3.2

_基于 100 篇实战经验的标准化技术文章生产流水线_
_状态机已通过 12 场景 / 17 状态 100% 覆盖验证_

> **v3.2 更新**：
> - 修复 `CheckPublishResult` 引用不存在的 `HandleRework`（已改名为 `IncrementRework`）
> - 明确 Publish Agent 输出约定：无 rework 时不返回 `rework_needed` 字段（`IsPresent` 语义精确）
>
> **v3.1 更新（码虾 Review 修复）**：
> - Bug 1 修复：CheckReworkLimit Choice state 补充 `Default: NeedsHuman`
> - Bug 2 修复：HandleRework 从 Pass+MathAdd 改为 Lambda 递增（Pass state 无法做 scalar 原地递增）
> - Bug 3 修复：Publish state 补充完整 Parameters（QueueUrl + TaskToken + payload）
> - Bug 4 修复：Step Functions 启动 input 初始化 `rework_count: 0`
> - 新增：SQS → Lambda → AgentCore 集成模式（设计文档原缺此层）
> - 更新：AgentCore 已 GA（2025-10），8 个核心服务全部 GA，9 个 Region

---

## 🎯 项目定位

**输入**：AWS What's New URL（HTTP API 提交）
**输出**：标准化的 Hands-on Lab 技术文章（Markdown，含实测数据、踩坑记录、IAM Policy、费用明细、清理脚本）

**核心价值**：把 12 天 100 篇的六步闭环经验，固化成一个 AWS 原生的独立服务——自己就是自己最好的 demo。

### 技术选型概览

| 组件 | 选型 | 理由 |
|------|------|------|
| Agent 框架 | Strands Agents (Python) | @tool 装饰器、Structured Output、原生 Bedrock 支持 |
| Agent 运行时 | AgentCore Runtime | Session Storage + Shell Command API + 隔离容器 |
| Agent 记忆 | AgentCore Memory | Semantic 策略自动提取事实，跨任务知识积累 |
| 编排 | Step Functions | 状态机天然映射、内置 retry/catch/timeout、可视化 |
| 异步解耦 | SQS | Agent 间消息传递、削峰、重试 |
| 任务状态 | DynamoDB | 低延迟状态查询、GSI 按状态/日期过滤 |
| 数据存储 | S3 | 笔记、测试数据、文章内容、证据 |
| API | API Gateway + Lambda | HTTP 接口、异步任务提交 |
| 发布 | GitHub（MkDocs） | Git push 触发 GitHub Actions 自动部署 |

---

## 🏗️ AWS 架构

```
                    ┌─────────────┐
                    │  API Gateway │
                    │  /tasks      │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Lambda     │
                    │  (API Handler│───────────┐
                    │  + 任务入队) │           │
                    └──────┬──────┘           │
                           │                  ▼
                    ┌──────▼──────┐    ┌──────────┐
                    │   DynamoDB   │    │ Webhook  │
                    │  (tasks +    │    │ Callback │
                    │   resources) │    └──────────┘
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Step       │
                    │  Functions   │◄──── Orchestrator 状态机
                    │  (编排)      │      (17 states, 已验证)
                    └──┬───┬───┬──┘
                       │   │   │
            ┌──────────┘   │   └──────────┐
            ▼              ▼              ▼
    ┌──────────────┐ ┌──────────┐ ┌──────────────┐
    │  SQS:        │ │  SQS:    │ │  SQS:        │
    │  research    │ │  execute │ │  publish     │
    └──────┬───────┘ └────┬─────┘ └──────┬───────┘
           │              │              │
    ┌──────▼───────┐ ┌────▼─────┐ ┌──────▼───────┐
    │  AgentCore   │ │ AgentCore│ │  AgentCore   │
    │  Research    │ │ Execute  │ │  Publish     │
    │  (Opus 4.6)  │ │(Sonnet)  │ │ (Sonnet)     │
    │              │ │          │ │              │
    │  Strands     │ │ Strands  │ │  Strands     │
    │  Agent       │ │ Agent    │ │  Agent       │
    └──────┬───────┘ └────┬─────┘ └──────┬───────┘
           │              │              │
           └──────────────┼──────────────┘
                          ▼
              ┌───────────────────────┐
              │   AgentCore Memory    │
              │ 短期: 任务上下文       │
              │ 长期: 踩坑知识库       │
              └───────────────────────┘
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │    S3     │   │   IAM    │   │  GitHub   │
    │  笔记/证据│   │ Scoped   │   │  文章发布 │
    │  测试数据 │   │  Roles   │   │          │
    └──────────┘   └──────────┘   └──────────┘
```

---

## 🤖 三个 Agent + Orchestrator

### 为什么 3 个 Agent？

按**认知边界**拆分，不按步骤拆分。步骤间传递 context 太大会浪费 token，同一认知域内的步骤共享上下文更高效。

| Agent | 模型 | 负责步骤 | 核心能力 | AWS 权限 |
|-------|------|---------|---------|---------|
| Research | Opus 4.6 | ①②③ + IAM 推导 | 理解新功能、设计实验 | 只读（aws-knowledge） |
| Execute | Sonnet 4.6 | IAM 创建 + ④探索 + 清理 + ④复测 | 命令执行、报错处理、动态调权 | **读写**（Scoped Role） |
| Publish | Sonnet 4.6 | ④.5校准 + ⑤撰写/发布 + ⑥归档 | 校准、写作、发布 | 只读（aws-knowledge）+ Git |

### Research Agent（Opus 4.6）

**Strands Tools**：
- `aws_knowledge_read` — 读 AWS 官方文档
- `aws_knowledge_region` — 查 Region 可用性
- `write_notes` — 写入 S3 笔记文件
- `memory_search` — 查 AgentCore Memory 中的历史踩坑经验

**输入**：What's New URL
**输出**：
```json
{
  "evaluation": { "verdict": "go", "complexity": "M", "estimated_cost": 2.0 },
  "notes_path": "s3://bucket/tasks/{id}/notes.md",
  "test_matrix": [
    {"id": "T1", "name": "核心功能验证", "priority": "P0"},
    {"id": "T2", "name": "A/B 对比", "priority": "P0"},
    {"id": "T3", "name": "边界条件", "priority": "P0"},
    {"id": "T4", "name": "错误处理", "priority": "P1"}
  ],
  "iam_policy": { "Version": "2012-10-17", "Statement": [...] },
  "services": ["s3tables", "glue", "athena"]
}
```

**特点**：
- 无 AWS 写操作——安全
- 执行时间短（30min~2h）
- 可高并发（无资源冲突）

### Execute Agent（Sonnet 4.6）

**Strands Tools**：
- `aws_cli_execute` — 在 AgentCore 容器内执行 AWS CLI（Shell Command API）
- `aws_knowledge_read` — 报错时查文档
- `iam_add_permission` — 动态追加 IAM 权限
- `track_resource` — 注册创建的资源
- `cleanup_resource` — 删除资源
- `write_notes` — 写入测试日志到 S3
- `memory_create` — 记录踩坑到 AgentCore Memory

**输入**：Research 输出（笔记路径 + 测试矩阵 + IAM Policy）
**输出**：
```json
{
  "test_results": {"T1": "pass", "T2": "pass", "T3": "pass"},
  "final_iam_policy": { ... },
  "permissions_added": ["s3:CreateBucket", "s3:PutObject"],
  "pitfalls": [{"desc": "...", "verified": true}],
  "performance_data": { ... },
  "evidence_path": "s3://bucket/tasks/{id}/evidence/",
  "cost_actual": 0.82
}
```

**报错处理链**：
```
报错 → ① 记录到 S3 笔记
     → ② aws_knowledge_read 查文档
     → ③ LLM 判断：操作错误？AWS 限制？
     → ④a 操作错误 → 修正重试
     → ④b AWS 限制 → memory_create 记录 → 标注继续
     → ④c 不确定 → 返回 NEEDS_HUMAN
```

**双轮执行流程**：
```
探索轮 → AccessDenied → iam_add_permission → 重试 → 全部通过
  ↓
cleanup_resource（保留 IAM）
  ↓
复测轮 → 干净环境 + 正确权限从头跑 → 最终数据
  ↓
cleanup_resource（含 IAM）
```

### Publish Agent（Sonnet 4.6）

**Strands Tools**：
- `aws_knowledge_read` — 逐条校准技术声明
- `read_s3` — 读取研究笔记 + 测试结果
- `write_article` — 生成文章 Markdown
- `git_push` — 推送到 GitHub
- `quality_check` — 7 条红线自检
- `memory_search` — 查历史校准经验

**输入**：Research 笔记 + Execute 结果 + 最终 IAM Policy
**输出**：
```json
{
  "calibration": {"verified": 8, "corrected": 1, "undocumented": 2},
  "article_path": "docs/storage/s3-tables-iam.md",
  "published_url": "https://chaosreload.github.io/...",
  "quality_passed": true
}
```

> **输出约定**：如果需要打回重测，返回 `rework_needed: true` + `rework_type`。
> 如果不需要打回，**不返回 `rework_needed` 字段**（Step Functions 用 `IsPresent` 判断）。
> 打回时的输出示例：
> ```json
> { "rework_needed": true, "rework_type": "retest_specific", "reason": "T2 数据与文档矛盾" }
> ```

### Orchestrator = Step Functions

确定性状态机，不用 LLM。我们验证过的 17 个状态直接映射到 ASL：

```json
{
  "StartAt": "Research",
  "States": {
    "Research": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
      "Parameters": {
        "QueueUrl": "${ResearchQueueUrl}",
        "MessageBody": { "task_id.$": "$.task_id", "url.$": "$.url", "token.$": "$$.Task.Token" }
      },
      "ResultPath": "$.research_result",
      "Next": "CheckResearchVerdict",
      "TimeoutSeconds": 7200
    },
    "CheckResearchVerdict": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.research_result.verdict", "StringEquals": "skip", "Next": "Skipped" }
      ],
      "Default": "Execute"
    },
    "Execute": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
      "Parameters": {
        "QueueUrl": "${ExecuteQueueUrl}",
        "MessageBody": { "task_id.$": "$.task_id", "research.$": "$.research_result", "token.$": "$$.Task.Token" }
      },
      "ResultPath": "$.execute_result",
      "Next": "Publish",
      "TimeoutSeconds": 28800,
      "Retry": [{ "ErrorEquals": ["TransientError"], "MaxAttempts": 1 }]
    },
    "Publish": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
      "Parameters": {
        "QueueUrl": "${PublishQueueUrl}",
        "MessageBody": {
          "task_id.$": "$.task_id",
          "TaskToken.$": "$$.Task.Token",
          "research_result.$": "$.research_result",
          "execute_result.$": "$.execute_result"
        }
      },
      "ResultPath": "$.publish_result",
      "Next": "CheckPublishResult",
      "TimeoutSeconds": 7200
    },
    "CheckPublishResult": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.publish_result.rework_needed", "IsPresent": true, "Next": "IncrementRework" }
      ],
      "Default": "UpdateCompleted"
    },
    "IncrementRework": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${IncrementReworkFn}",
        "Payload": { "task_id.$": "$.task_id" }
      },
      "ResultPath": "$.rework_info",
      "Next": "CheckReworkLimit"
    },
    "CheckReworkLimit": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.rework_info.rework_count", "NumericGreaterThan": 2, "Next": "NeedsHuman" },
        { "Variable": "$.publish_result.rework_type", "StringEquals": "redesign", "Next": "Research" },
        { "Variable": "$.publish_result.rework_type", "StringEquals": "retest_all", "Next": "Execute" },
        { "Variable": "$.publish_result.rework_type", "StringEquals": "retest_specific", "Next": "Execute" }
      ],
      "Default": "NeedsHuman"
    },
    "UpdateCompleted": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:update-task-status",
      "Parameters": { "task_id.$": "$.task_id", "state": "completed" },
      "Next": "NotifyComplete"
    },
    "NotifyComplete": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": { "TopicArn": "${NotifyTopic}", "Message.$": "$.publish_result" },
      "Next": "Completed"
    },
    "Completed": { "Type": "Succeed" },
    "Skipped": { "Type": "Succeed" },
    "NeedsHuman": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": { "TopicArn": "${AlertTopic}", "Message": "Task needs human review" },
      "Next": "Failed"
    },
    "Failed": { "Type": "Fail" }
  }
}
```

---

## 🔄 完整 Workflow 流程

```
POST /tasks { url: "..." }
        │
        ▼
  Lambda → DynamoDB (创建任务) → Step Functions (启动, input 含 rework_count: 0)
        │
        ▼ 返回 { task_id, status: "queued" }

Step Functions 编排:
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Research Agent (Opus 4.6)                               │
  │  SQS → AgentCore Runtime                                │
  │  ① 评估: 可实操？有 API/CLI？                            │
  │  ② 研究: aws-knowledge 查文档 + Memory 查历史踩坑        │
  │  ③ 测试设计: 矩阵 + A/B 对比 + 边界条件                  │
  │  IAM 推导: 从 services + actions 生成初始 Policy          │
  │  → 输出写入 S3, 完成后 SendTaskSuccess                   │
  └────────────────────────┬────────────────────────────────┘
                           │
                    verdict=skip? → Skipped (DynamoDB 更新)
                           │ go
                           ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Execute Agent (Sonnet 4.6)                              │
  │  SQS → AgentCore Runtime (Shell Command API)             │
  │                                                          │
  │  创建 Scoped IAM Role (基于 Research 推导)               │
  │           │                                              │
  │  探索轮 (Round 1)                                        │
  │    执行测试矩阵 → AccessDenied? → iam_add_permission     │
  │    报错? → aws_knowledge → 判断 → 修正/标注              │
  │    踩坑 → memory_create (长期记忆)                        │
  │           │                                              │
  │  清理 R1 (保留 IAM Role/Policy)                          │
  │           │                                              │
  │  复测轮 (Round 2)                                        │
  │    干净环境 + 最终权限从头跑 → 最终数据                    │
  │           │                                              │
  │  清理 R2 (含 IAM Role/Policy)                            │
  │  → 输出写入 S3, 完成后 SendTaskSuccess                   │
  └────────────────────────┬────────────────────────────────┘
                           │
                           ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Publish Agent (Sonnet 4.6)                              │
  │  SQS → AgentCore Runtime                                │
  │                                                          │
  │  ④.5 校准: aws-knowledge 逐条核实 + Memory 查历史        │
  │    发现矛盾? → ReworkRequest → SendTaskSuccess(rework)   │
  │           │ OK                                           │
  │  ⑤ 撰写文章 (含最小 IAM Policy)                          │
  │  ⑤ 质量红线 7 条自检                                      │
  │    不过? → 自修复 or ReworkRequest                        │
  │           │ All pass                                     │
  │  ⑤ git push → GitHub Actions 自动部署                    │
  │  ⑥ 归档: DynamoDB 更新 + Memory 沉淀                     │
  │  → SendTaskSuccess                                       │
  └────────────────────────┬────────────────────────────────┘
                           │
                    有 rework? ──→ Step Functions 回跳
                           │ No        (max 2 次, 超限 → SNS 通知人工)
                           ▼
                    ✅ Completed
                    DynamoDB 更新 + Webhook 回调 + SNS 通知
```

### 三种打回路径

| 打回类型 | Step Functions 行为 | 真实案例 |
|---------|-------------------|---------|
| `retest_specific` | Choice → Execute | #2 AgentCore 踩坑实际是操作错误 |
| `retest_all` | Choice → Execute（含清理） | DMS 初测数据量太小 |
| `redesign` | Choice → Research | Bedrock Benchmark 没用最新模型 |

**安全阀**：rework_count > 2 → SNS 通知人工 → Failed

---

## 🔐 IAM 最小权限 + 双轮验证

### 三层权限模型

```yaml
Layer 1 — Base Policy（所有任务共享，只读）:
  - cloudtrail:LookupEvents
  - cloudwatch:GetMetric*
  - sts:GetCallerIdentity
  - pricing:*

Layer 2 — Service Policy（LLM 推导 + 动态追加）:
  - Research Agent 推导初始 actions（~80% 准确）
  - Execute Agent 遇到 AccessDenied 时追加

Layer 3 — Safety Deny（硬编码，不可覆盖）:
  - Deny ec2:AuthorizeSecurityGroupIngress where CidrIp=0.0.0.0/0
  - Deny iam:CreateUser / iam:AttachUserPolicy
  - Deny iam:CreateLoginProfile
```

### 动态调权流程

```python
@tool
def aws_cli_execute(command: str) -> str:
    """Execute AWS CLI with scoped role, auto-add permissions on AccessDenied."""
    safety_guard.pre_execute(command)
    try:
        return shell_execute(command, role=scoped_role)
    except AccessDenied as e:
        missing_action = parse_missing_action(e)
        safety_guard.check_iam_action(missing_action)  # Layer 3 check
        iam_add_permission(missing_action)
        return shell_execute(command, role=scoped_role)  # retry
```

### 副产品：文章自带 IAM Policy

```markdown
## 前置条件
<details>
<summary>最小 IAM Policy（点击展开）</summary>

此 Policy 由 workflow 双轮验证产出，是本 Lab 实测所需的最小权限：

{自动插入 final_iam_policy JSON}
</details>
```

### 跨服务隐性依赖表（存入 AgentCore Memory 长期记忆）

| 主服务 | 隐性依赖 | 原因 |
|-------|---------|------|
| S3 Tables | glue, athena, s3 | Athena 需要 s3 结果桶 |
| AgentCore | ecr, iam:PassRole, vpc, logs | 容器部署 |
| Lambda MI | ec2:Describe*, iam:PassRole | Managed Instances |
| Bedrock KB | s3, opensearch/neptune, iam:PassRole | 数据源 + 向量存储 |
| DMS | rds, s3, iam:PassRole, kms | 源/目标 + 日志 + 加密 |
| EKS | ec2, iam, eks, ecr, logs | 集群 + 节点组 + 插件 |

---

## 🧠 AgentCore Memory 使用策略

### 短期记忆（任务级）

每个任务的 AgentCore Session 内自动维护：
- 当前测试进度
- 已追加的 IAM 权限
- 已创建的资源列表
- 报错处理决策历史

### 长期记忆（跨任务）

通过 Semantic 策略自动提取事实，积累跨任务知识：

```python
# Execute Agent 踩坑时写入
memory.create_event(
    content="S3 Tables: API create-table 不带 schema 会报错，必须用 Athena DDL 或带 schema 参数",
    strategy="semantic"
)

# Research Agent 设计测试时查询
pitfalls = memory.search(query="S3 Tables 已知问题")
# → 返回历史踩坑，Research 在测试设计中规避

# Publish Agent 校准时查询
history = memory.search(query=f"{service} 文档与实际行为差异")
# → 提高校准效率，已知差异不需要重复验证
```

### 记忆增长模型

随着文章数量增加，Memory 中的知识越来越丰富：
- 10 篇后：常见踩坑经验
- 50 篇后：跨服务依赖图谱、区域限制速查
- 100 篇后：接近完整的 AWS 服务实测知识库

**这就是"越用越好"的飞轮效应。**

---

## 💾 数据存储设计

### S3（文件数据）

```
s3://handson-workflow-{account}/
├── tasks/{task-id}/
│   ├── notes.md                    # 研究笔记
│   ├── test-matrix.json            # 测试矩阵
│   ├── iam-policy-initial.json     # 初始推导 Policy
│   ├── iam-policy-final.json       # 最终验证 Policy
│   ├── evidence/
│   │   ├── explore-log.md          # 探索轮日志
│   │   ├── verify-log.md           # 复测轮日志
│   │   ├── cloudtrail.json
│   │   └── metrics/
│   ├── article.md                  # 文章草稿
│   └── cost.json
├── templates/
│   ├── article.md
│   ├── comparison.md
│   └── benchmark.md
└── prompts/
    ├── evaluate.md
    ├── research.md
    ├── test_design.md
    ├── iam_derive.md
    ├── error_diagnose.md
    ├── article_draft.md
    └── calibrate.md
```

### DynamoDB（结构化状态）

```
Table: handson-tasks
  PK: task_id (string)
  Attributes:
    url              - What's New URL
    state            - TaskState enum
    rework_count     - int
    created_at       - ISO timestamp
    updated_at       - ISO timestamp
    research_verdict - go | skip
    complexity       - S | M | L
    services         - string set (e.g. ["s3tables", "glue"])
    test_passed      - int (通过的测试数)
    test_total       - int (总测试数)
    iam_role_arn     - 创建的 IAM Role ARN
    cost_estimated   - number
    cost_actual      - number
    published_url    - 发布后的文章 URL
    error_message    - 如果 failed
    callback_url     - Webhook 回调地址
    sfn_execution    - Step Functions execution ARN

  GSI: state-index
    PK: state
    SK: updated_at
    用途: 查询进行中 / 失败 / 需人工的任务

  GSI: date-index
    PK: created_date (YYYY-MM-DD)
    SK: created_at
    用途: 按日期查询

Table: handson-resources
  PK: task_id (string)
  SK: resource_arn (string)
  Attributes:
    resource_type    - e.g. "opensearch:domain", "lambda:function"
    region           - AWS Region
    status           - active | cleaned | pending_eni | failed
    created_at       - ISO timestamp
    cleaned_at       - ISO timestamp (nullable)
    error            - 清理失败原因 (nullable)
```

---

## 🌐 HTTP API 设计

### 端点

```
POST   /tasks              创建任务
POST   /tasks/batch        批量创建
GET    /tasks              列出任务（支持 ?state= 过滤）
GET    /tasks/{id}         查询任务状态
GET    /tasks/{id}/result  获取任务结果
DELETE /tasks/{id}         取消任务 + 清理资源
```

### 创建任务

```http
POST /tasks
Content-Type: application/json

{
  "url": "https://aws.amazon.com/about-aws/whats-new/2026/03/...",
  "callback_url": "https://your-server/webhook",    // 可选
  "notify_slack": "#channel",                        // 可选
  "config_override": {                               // 可选
    "region": "us-west-2",
    "budget": 5.0
  }
}
```

```http
HTTP/1.1 202 Accepted

{
  "task_id": "abc123",
  "state": "queued",
  "created_at": "2026-03-28T22:00:00Z",
  "estimated_duration": "2-4 hours"
}
```

### 查询状态

```http
GET /tasks/abc123
```

```http
{
  "task_id": "abc123",
  "url": "https://aws.amazon.com/about-aws/whats-new/...",
  "state": "executing_verify",
  "rework_count": 0,
  "progress": {
    "research": { "status": "completed", "duration": "45min" },
    "explore":  { "status": "completed", "tests_passed": 7, "permissions_added": 2 },
    "verify":   { "status": "in_progress", "tests_completed": "5/7" },
    "publish":  { "status": "pending" }
  },
  "cost": { "estimated": 2.0, "actual": 0.82 },
  "created_at": "2026-03-28T22:00:00Z",
  "updated_at": "2026-03-28T23:15:00Z"
}
```

### 获取结果

```http
GET /tasks/abc123/result
```

```http
{
  "task_id": "abc123",
  "state": "completed",
  "article": {
    "title": "S3 Tables IAM-only 权限实战",
    "url": "https://chaosreload.github.io/aws-hands-on-lab/storage/s3-tables-iam/",
    "path": "docs/storage/s3-tables-iam.md"
  },
  "iam_policy": { "Version": "2012-10-17", "Statement": [...] },
  "test_results": {
    "T1": { "name": "Create Table Bucket", "status": "pass", "duration": "12s" },
    "T2": { "name": "IAM-only Athena query", "status": "pass", "duration": "8s" },
    "T3": { "name": "Boundary: uppercase name", "status": "pass", "duration": "5s" }
  },
  "pitfalls": [
    { "desc": "API 建的表缺 metadata_location", "verified": true, "source": "aws-knowledge" }
  ],
  "calibration": { "verified": 8, "corrected": 1, "undocumented": 2 },
  "cost": { "llm": 2.70, "aws": 0.82, "total": 3.52 }
}
```

### Webhook 回调

任务完成/失败时主动 POST：

```http
POST https://your-server/webhook
Content-Type: application/json

{
  "event": "task.completed",
  "task_id": "abc123",
  "state": "completed",
  "published_url": "https://chaosreload.github.io/...",
  "cost_total": 3.52,
  "timestamp": "2026-03-28T23:45:00Z"
}
```

---

## 🛡️ 安全设计

### Safety Guard

在 Execute Agent 的每个 AWS 命令前后执行：

```python
class SafetyGuard:
    FORBIDDEN_PATTERNS = [
        r"0\.0\.0\.0/0",
        r"::/0",
        r"--publicly-accessible",
        r"rm\s+-rf\s+/",
    ]
    
    BLOCKED_IAM_ACTIONS = [
        "iam:CreateUser",
        "iam:AttachUserPolicy",
        "iam:CreateLoginProfile",
        "iam:PutUserPolicy",
    ]
    
    def pre_execute(self, command):
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command):
                raise SecurityViolation(f"Blocked: {pattern}")
    
    def check_iam_action(self, action):
        if action in self.BLOCKED_IAM_ACTIONS:
            raise SecurityViolation(f"Blocked IAM action: {action}")
    
    def post_execute(self, resources):
        for r in resources:
            if r.type == "security-group":
                rules = describe_sg_rules(r.id)
                for rule in rules:
                    if rule.cidr in ("0.0.0.0/0", "::/0"):
                        delete_sg_rule(r.id, rule)
                        log.warning(f"Auto-removed {rule.cidr} from {r.id}")
```

### 质量红线（7 条）

Publish Agent 发布前自检，全部通过才允许发布：

1. **可复现** — 读者照着命令能跑通
2. **有数据** — 至少一张实测数据对比表
3. **有边界** — 验证了至少一个边界条件
4. **有成本** — 明确告知费用和清理方法
5. **有踩坑** — 记录实测中的非预期行为（且经过校准）
6. **经校准** — aws-knowledge 核实过每条技术声明
7. **有 IAM** — 包含最小权限 Policy

---

## 🔗 SQS → AgentCore 集成模式

Step Functions 通过 SQS + Lambda 触发 AgentCore Runtime：

```
Step Functions
  │ (waitForTaskToken)
  ▼
SQS Queue (research / execute / publish)
  │ (SQS trigger)
  ▼
Lambda (sqs_handler)
  │ 1. 解析 SQS message（含 TaskToken）
  │ 2. 调用 AgentCore Runtime.invoke()
  │ 3. 等待 agent 完成
  │ 4. sfn.send_task_success(token, output)
  ▼
AgentCore Runtime
  │ (Strands Agent 在容器内执行)
  │ (Shell Command API 执行 AWS CLI)
  │ (Session Storage 持久化中间状态)
  ▼
完成 → Lambda 回调 Step Functions
```

```python
# sqs_handler.py — SQS → AgentCore 桥接 Lambda
def handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])
        task_token = body["TaskToken"]
        task_id = body["task_id"]
        agent_type = body.get("agent_type", "research")
        
        try:
            # 调用 AgentCore Runtime
            result = agentcore.invoke(
                agent_name=f"handson-{agent_type}",
                session_id=task_id,
                input=body,
            )
            
            # 成功 → 回调 Step Functions
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps(result),
            )
        except Exception as e:
            sfn.send_task_failure(
                taskToken=task_token,
                error=type(e).__name__,
                cause=str(e),
            )
```

---

## 📊 模型配置 + 成本估算

### 模型选择

| Agent | 模型 | 理由 |
|-------|------|------|
| Research | Claude Opus 4.6 (Bedrock) | 高认知任务：理解新功能、设计 A/B 实验、IAM 推导 |
| Execute | Claude Sonnet 4.6 (Bedrock) | 命令执行 + 报错判断，Sonnet 够用且可靠 |
| Publish | Claude Sonnet 4.6 (Bedrock) | 写作质量已验证，校准主要是文档对比 |

### 单篇成本估算

| 项目 | 预估 |
|------|------|
| Research Agent (Opus, ~50K in + ~10K out) | ~$1.50 |
| Execute Agent (Sonnet, ~100K in + ~30K out, 双轮) | ~$0.70 |
| Publish Agent (Sonnet, ~80K in + ~20K out) | ~$0.50 |
| **LLM 小计** | **~$2.70** |
| AWS 资源 (双轮执行) | ~$1-2 |
| AgentCore Runtime | ~$0.10 |
| DynamoDB + S3 + SQS | ~$0.01 |
| **单篇总计** | **~$4-5** |

### 并行约束

| 组件 | 并行度 | 瓶颈 |
|------|--------|------|
| Research Agent | 高（只读） | LLM API 并发 |
| Execute Agent | 2-3 | AWS 资源 quota + 成本控制 |
| Publish Agent | 串行 | Git push 冲突 |
| Step Functions | 无限 | 无 |
| SQS | 无限 | 无 |

---

## 📦 项目结构

```
aws-handson-workflow/
├── README.md
├── template.yaml                     # SAM/CloudFormation
├── Dockerfile                        # AgentCore 容器镜像
│
├── src/
│   ├── api/                          # API Lambda
│   │   ├── handler.py                # API Gateway handler
│   │   └── models.py                 # Request/Response models
│   │
│   ├── agents/                       # 3 个 Strands Agent
│   │   ├── research/
│   │   │   ├── agent.py              # Strands Agent 定义
│   │   │   └── tools.py              # @tool 函数
│   │   ├── execute/
│   │   │   ├── agent.py
│   │   │   ├── tools.py
│   │   │   └── safety_guard.py
│   │   └── publish/
│   │       ├── agent.py
│   │       └── tools.py
│   │
│   ├── orchestrator/                 # Step Functions + SQS 胶水
│   │   ├── sfn_definition.json       # ASL 定义（已修复 Bug 1-4）
│   │   ├── sqs_handler.py            # SQS → AgentCore 桥接 Lambda
│   │   ├── increment_rework.py       # rework_count 递增 Lambda
│   │   └── callback.py               # SendTaskSuccess/Failure
│   │
│   ├── aws/                          # AWS 交互层
│   │   ├── iam_manager.py            # Scoped Role + 动态调权
│   │   ├── resource_tracker.py       # 资源追踪
│   │   ├── knowledge.py              # aws-knowledge MCP 封装
│   │   └── cost_tracker.py
│   │
│   └── common/
│       ├── config.py
│       ├── models.py                 # 共享数据模型
│       └── s3_utils.py
│
├── prompts/                          # LLM Prompt 模板
│   ├── evaluate.md
│   ├── research.md
│   ├── test_design.md
│   ├── iam_derive.md
│   ├── error_diagnose.md
│   ├── article_draft.md
│   └── calibrate.md
│
├── templates/                        # 文章模板
│   ├── article.md
│   ├── comparison.md
│   └── benchmark.md
│
├── tests/
│   ├── test_orchestrator.py          # 12 场景（✅ 已通过）
│   ├── test_safety_guard.py
│   ├── test_iam_manager.py
│   └── fixtures/
│
└── config/
    └── default.yaml
```

---

## ⚙️ 配置

```yaml
# config.yaml
workflow:
  max_parallel_execute: 3
  max_rework: 2
  budget_per_task: 10.0
  budget_monthly: 500.0

aws:
  region: "us-east-1"
  account_id: "123456789012"

agents:
  research:
    model: "anthropic.claude-opus-4-6-v1"
    agentcore_runtime: "research-agent"
    timeout: 7200
  execute:
    model: "anthropic.claude-sonnet-4-6-v1"
    agentcore_runtime: "execute-agent"
    timeout: 28800
    explore_timeout: 14400
    verify_timeout: 14400
  publish:
    model: "anthropic.claude-sonnet-4-6-v1"
    agentcore_runtime: "publish-agent"
    timeout: 7200

memory:
  namespace: "handson-workflow"
  strategy: "semantic"

storage:
  s3_bucket: "handson-workflow-{account}"
  dynamodb_tasks_table: "handson-tasks"
  dynamodb_resources_table: "handson-resources"

queues:
  research: "handson-research-queue"
  execute: "handson-execute-queue"
  publish: "handson-publish-queue"

publisher:
  type: "mkdocs"
  repo: "chaosreload/aws-hands-on-lab"
  language: "zh"
  auto_push: true

safety:
  forbidden_sg_cidrs: ["0.0.0.0/0", "::/0"]
  blocked_iam_actions: ["iam:CreateUser", "iam:AttachUserPolicy", "iam:CreateLoginProfile"]
  require_cleanup: true
  track_eni_cleanup: true

notify:
  sns_topic: "handson-workflow-notifications"
  on_events: ["completed", "failed", "needs_human"]

pipeline:
  evaluate:
    auto_skip: ["region-expansion-only", "pricing-change-only", "console-only-no-api"]
  research:
    min_knowledge_queries: 2
  test_design:
    min_test_items: 3
    require_boundary_test: true
  calibrate:
    min_claims_verified: 5
  quality:
    require_all_7_checks: true
```

---

## 📐 经验到代码的完整映射

| # | 100 篇经验 | Workflow 实现 |
|---|------------|-------------|
| 1 | 六步闭环不跳步 | Step Functions 强制状态顺序 |
| 2 | 每步完成立即写入文件 | S3 + DynamoDB 实时持久化 |
| 3 | Session 会断，文件不会 | AgentCore Session Storage + S3 |
| 4 | 报错先查文档再下结论 | error_diagnose.md prompt + aws-knowledge tool |
| 5 | 踩坑记录要反问 | LLM 诊断 + Memory 长期记录 |
| 6 | aws-knowledge 校准强制 | Publish Agent min_claims_verified=5 |
| 7 | 0.0.0.0/0 绝对禁止 | SafetyGuard Layer 3 Deny |
| 8 | VPC ENI 清理检查 | ResourceTracker.check_eni_residuals |
| 9 | AI 任务先查最新模型 | Research Agent 自动查 Bedrock 最新模型 |
| 10 | 测试至少 5 次采样 | test_design prompt 约束 |
| 11 | A/B 对比 > 单一验证 | comparison.md 模板 |
| 12 | 边界条件必测 | require_boundary_test: true |
| 13 | "有结论无过程" | 双轮模型：探索 → 复测 |
| 14 | 权限反复调试 | IAM 动态调权 + ScopedExecutor |
| 15 | 文章缺前置条件 | 最终 Policy 自动嵌入文章 |
| 16 | 校准发现矛盾 | Rework loop → Step Functions Choice |
| 17 | 质量红线不满足 | 7 条自检 + 自修复/打回 |
| 18 | 超过 2 次打回 | max_rework=2 → SNS 人工升级 |
| 19 | 跨任务踩坑重复 | AgentCore Memory 长期记忆飞轮 |
| 20 | 音频/UI 需人验证 | evaluate auto_skip console-only-no-api |

---

## 🚀 实现路径

### Phase 1: MVP（2 周）— 单篇端到端

- [ ] SAM/CloudFormation 基础设施（API GW + Lambda + DynamoDB + S3 + SQS）
- [ ] Step Functions ASL 定义（✅ 状态机已验证）
- [ ] 3 个 Strands Agent 骨架 + AgentCore Runtime 部署
- [ ] Research Agent tools（aws-knowledge + write_notes）
- [ ] Execute Agent tools（aws_cli_execute + iam_add_permission + track_resource）
- [ ] Publish Agent tools（quality_check + write_article + git_push）
- [ ] Safety Guard
- [ ] IAM Manager（动态调权）
- [ ] HTTP API（POST /tasks + GET /tasks/{id}）
- [ ] 用 1 篇真实 What's New 端到端验证

### Phase 2: 稳定化（2 周）— 批量生产

- [ ] AgentCore Memory 集成（长期记忆）
- [ ] Resource Tracker + 自动清理
- [ ] Cost Tracker + 预算告警
- [ ] Webhook 回调
- [ ] 批量 API（POST /tasks/batch）
- [ ] 并行执行控制
- [ ] SNS 通知
- [ ] 跑 10 篇验证稳定性

### Phase 3: 开源就绪（2 周）

- [ ] 文档 + README + 架构图
- [ ] 一键部署脚本（SAM deploy）
- [ ] 配置模板（多账号、多 Region）
- [ ] 对比/benchmark 文章模板
- [ ] Prompt 调优（基于 Phase 1-2 实际数据）
- [ ] 开源发布

### Phase 4: 进阶（持续）

- [ ] Web UI 看板
- [ ] RSS 自动抓取 + 筛选
- [ ] 多语言支持（中 + 英）
- [ ] 文章质量评分模型
- [ ] 自动选题（基于趋势 + 历史覆盖 gap）

---

_设计基于：100 篇文章、294 条反馈、30K+ 行笔记、12 天持续生产。_
_状态机验证：12 场景、17 状态、100% 覆盖。_
_代码原型：`projects/aws-handson-workflow/orchestrator.py`_
