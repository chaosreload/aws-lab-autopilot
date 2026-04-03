# Agent I/O Spec — aws-lab-autopilot

_每个 Agent 的输入、输出、内部数据模型完整规范_
_对齐 src/common/models.py + 各 agent.py 实际代码行为_

---

## Research Agent

### 函数签名

```python
def run_research(task_id: str, url: str) -> dict
```

### Input

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `task_id` | `str` | Step Functions execution | 全局唯一任务 ID（UUID） |
| `url` | `str` | SQS message body `.url` | AWS What's New 公告页面 URL |

SQS message body（来自 Step Functions Research state）：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "url": "https://aws.amazon.com/about-aws/whats-new/2026/03/...",
  "token": "<Step Functions TaskToken>"
}
```

### Output（`ResearchResult`）

```python
@dataclass
class ResearchResult:
    verdict: Verdict                  # "go" | "skip"
    notes_path: str                   # s3://bucket/tasks/{id}/notes.md
    test_matrix: list[TestItem]       # 测试矩阵，总量 ≤ 8 项
    iam_policy: dict                  # 最小权限 IAM Policy JSON
    services: list[str]               # 涉及的 AWS CLI service 名列表
    # 以下字段在 models.py 中定义但 agent 实际输出不包含（Phase 1 已简化）
    # complexity: Complexity          # "S" | "M" | "L"（暂未使用）
    # estimated_cost: float           # 预估成本（暂未使用）
```

**实际 JSON 输出（agent LLM 返回格式）**：
```json
{
  "verdict": "go",
  "notes_path": "s3://handson-workflow-595842667825-us-east-1/tasks/550e8400/notes.md",
  "test_matrix": [
    {
      "id": "T1",
      "name": "核心 API 调用验证",
      "priority": "P0",
      "api_hints": {
        "service": "bedrock-runtime",
        "operation": "invoke_model",
        "model_id": "amazon.nova-2-multimodal-embeddings-v1:0",
        "request_body": {
          "schemaVersion": "nova-multimodal-embed-v1",
          "taskType": "SINGLE_EMBEDDING",
          "singleEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX",
            "embeddingDimension": 256,
            "text": {"truncationMode": "END", "value": "test"}
          }
        }
      }
    },
    {
      "id": "T2",
      "name": "对比测试（不同 embeddingDimension）",
      "priority": "P0",
      "api_hints": {
        "service": "bedrock-runtime",
        "operation": "invoke_model",
        "model_id": "amazon.nova-2-multimodal-embeddings-v1:0",
        "request_body": {}
      }
    },
    {
      "id": "T3",
      "name": "所有有效 embeddingPurpose 枚举",
      "priority": "P0",
      "api_hints": {}
    },
    {
      "id": "T4",
      "name": "无效值合并测试（embeddingDimension=512/768/0/-1）",
      "priority": "P1",
      "api_hints": {}
    },
    {
      "id": "T5",
      "name": "边界值合并测试（空输入、超长、类型错误）",
      "priority": "P1",
      "api_hints": {}
    }
  ],
  "iam_policy": {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel", "bedrock:ListFoundationModels"],
        "Resource": "*"
      }
    ]
  },
  "services": ["bedrock-runtime", "bedrock"]
}
```

**verdict = "skip" 时的最简输出**：
```json
{
  "verdict": "skip",
  "notes_path": "",
  "test_matrix": [],
  "iam_policy": {},
  "services": []
}
```

### TestItem 数据结构

```python
@dataclass
class TestItem:
    id: str           # "T1", "T2", ...
    name: str         # 测试项名称（中文）
    priority: str     # "P0" | "P1" | "P2"
    api_hints: dict   # 可选：具体 API 调用提示（service/operation/request_body 等）
                      # 注意：models.py 当前无 api_hints 字段，需补充
```

### 默认值（agent 输出缺失时的 fallback）

```python
defaults = {
    "verdict": "skip",
    "notes_path": "",
    "test_matrix": [],
    "iam_policy": {},
    "services": [],
}
```

### S3 产出物

| 文件 | 路径 | 内容 |
|------|------|------|
| 研究笔记 | `tasks/{task_id}/notes.md` | 技术分析、测试设计、IAM 推导、注意事项 |

---

## Execute Agent

### 函数签名

```python
def run_execute(task_id: str, research_result: dict) -> dict
```

### Input

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `task_id` | `str` | Step Functions execution | 全局唯一任务 ID |
| `research_result` | `dict` | Research Agent 输出 | ResearchResult JSON（见上） |

SQS message body（来自 Step Functions Execute state，当前实现走 AgentCore Bridge + polling）：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_type": "execute",
  "research": {
    "verdict": "go",
    "test_matrix": [...],
    "iam_policy": {...},
    "services": [...]
  },
  "token": "<Step Functions TaskToken>"
}
```

> **注意**：SQS message 里的 research 字段 key 是 `"research"`，但 sqs_handler.py 传给 `run_execute` 时用的是 `body.get("research", {})`，而非 `body.get("research_result", {})`。这与 Publish 阶段不一致（见 P0 Gap 文档）。

### Output（`ExecuteResult`）

```python
@dataclass
class ExecuteResult:
    test_results: dict[str, str]      # {"T1": "pass", "T2": "fail", "T3": "pass"}
    final_iam_policy: dict            # 双轮验证后的最终 IAM Policy
    permissions_added: list[str]      # ["bedrock:InvokeModel", "s3:PutObject"]
    pitfalls: list[dict]              # 踩坑记录列表
    cost_actual: float                # 实际 AWS 资源消耗成本（USD）
    # 以下字段在 models.py 中定义但 agent 实际不输出
    # performance_data: dict          # 性能数据（暂未使用）
    # evidence_path: str              # 证据路径（通过 write_execute_log 写入，不在 output dict）
```

**实际 JSON 输出**：
```json
{
  "test_results": {
    "T1": "pass",
    "T2": "pass",
    "T3": "pass",
    "T4": "fail",
    "T5": "pass"
  },
  "final_iam_policy": {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "bedrock:InvokeModel",
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModelAvailability"
        ],
        "Resource": "*"
      }
    ]
  },
  "permissions_added": ["bedrock:GetFoundationModelAvailability"],
  "pitfalls": [
    {
      "desc": "embeddingDimension=512 返回 ValidationException，文档未明确列出有效值范围",
      "verified": true
    }
  ],
  "cost_actual": 0.15
}
```

**双轮 merge 规则**：
- `test_results`：verify 轮权威，explore 轮作 fallback
- `final_iam_policy`：verify 轮权威
- `permissions_added`：explore ∪ verify（去重 set union）
- `pitfalls`：verify 轮权威，explore 轮作 fallback
- `cost_actual`：explore + verify 之和

### Pitfall 数据结构

```python
{
    "desc": str,      # 踩坑描述（中文，一句话）
    "verified": bool  # True = 有 stdout/stderr 证据；False = 推测
}
```

**踩坑分类铁律**：
- 进入 pitfalls 列表：AWS 产品限制、官方未记录行为、读者普遍会遇到的问题
- 不进入列表：代码 KeyError/TypeError、ImportError、环境配置问题
- 判断标准：「这个坑是不是所有使用该 AWS 功能的读者都可能遇到？」

### S3 产出物

| 文件 | 路径 | 内容 |
|------|------|------|
| Explore 日志 | `tasks/{task_id}/evidence/explore-log.md` | Explore 轮 Markdown 执行日志 |
| Explore 证据 | `tasks/{task_id}/evidence/explore-log.json` | Explore 轮结构化 evidence（含每条命令的 stdout/stderr/exit_code/duration_ms） |
| Verify 日志 | `tasks/{task_id}/evidence/verify-log.md` | Verify 轮 Markdown 执行日志 |
| Verify 证据 | `tasks/{task_id}/evidence/verify-log.json` | Verify 轮结构化 evidence（权威数据源，Publish Agent 从此读取） |

### Evidence Record 数据结构（verify-log.json 中每条记录）

```json
{
  "tool": "aws_cli_execute",
  "command": "aws bedrock-runtime invoke-model ...",
  "stdout": "{\"embedding\": [...]}",
  "stderr": "",
  "exit_code": 0,
  "duration_ms": 1234,
  "ts": "2026-04-02T05:00:00Z"
}
```

---

## Publish Agent

### 函数签名

```python
def run_publish(task_id: str, research_result: dict, execute_result: dict) -> dict
```

### Input

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `task_id` | `str` | Step Functions execution | 全局唯一任务 ID |
| `research_result` | `dict` | Research Agent 输出 | ResearchResult JSON |
| `execute_result` | `dict` | Execute Agent 输出 | ExecuteResult JSON |

SQS message body（来自 Step Functions Publish state）：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_type": "publish",
  "research_result": { "verdict": "go", "test_matrix": [...], ... },
  "execute_result": { "test_results": {...}, "final_iam_policy": {...}, ... },
  "token": "<Step Functions TaskToken>"
}
```

Publish Pipeline 内部还通过 S3 读取：
- `tasks/{task_id}/notes.md` — 研究笔记（Research Agent 写入）
- `tasks/{task_id}/evidence/verify-log.md` — Verify Markdown 日志
- `tasks/{task_id}/evidence/verify-log.json` — Verify 结构化证据（权威数据源）
- `tasks/{task_id}/evidence/explore-log.json` — Explore 失败记录（踩坑来源）

### Output（`PublishResult`）

```python
@dataclass
class PublishResult:
    quality_passed: bool              # True = 7 条红线全通过
    article_path: str                 # s3://bucket/tasks/{id}/article.md
    preview_url: str                  # 24h S3 pre-signed URL
    published_url: Optional[str]      # GitHub Pages URL（approve 后才有，否则 null）
    calibration: dict                 # {"verified": int, "corrected": int, "undocumented": int}
    # 以下字段仅在需要 rework 时返回
    rework_needed: Optional[bool]     # True 时存在，不 rework 时此字段不出现（IsPresent 语义）
    rework_type: Optional[ReworkType] # "redesign" | "retest_all" | "retest_specific"
    reason: Optional[str]             # rework 原因说明
```

**正常完成时的 JSON 输出**：
```json
{
  "quality_passed": true,
  "article_path": "s3://handson-workflow-595842667825-us-east-1/tasks/550e8400/article.md",
  "preview_url": "https://handson-workflow-...s3.amazonaws.com/tasks/550e8400/article.md?X-Amz-...",
  "published_url": null,
  "calibration": {
    "verified": 4,
    "corrected": 1,
    "undocumented": 0
  }
}
```

**需要 rework 时的 JSON 输出**：
```json
{
  "rework_needed": true,
  "rework_type": "retest_specific",
  "reason": "T2 测试数据与 aws-knowledge 文档中的 API 响应格式不符，需重新执行 T2"
}
```

> **Step Functions 关键约束**：CheckPublishResult state 使用 `IsPresent` 检查 `$.publish_result.rework_needed`。不需要 rework 时**绝对不能**在 output 中包含 `rework_needed` 字段（即使值为 `false`），否则会触发错误的 rework 路由。

### Calibration 数据结构

```json
{
  "verified": 4,       // 实际调用了几次 aws_knowledge_read_publish
  "corrected": 1,      // 校准中发现并修正的错误数量
  "undocumented": 0    // 发现的未记录行为数量
}
```

最低要求：`verified >= 3`（系统提示强制要求）

### S3 产出物

| 文件 | 路径 | 内容 |
|------|------|------|
| 文章草稿 | `tasks/{task_id}/article.md` | 最终 Markdown 文章（含所有 9 个必须章节） |

### git_push 调用时机

- **Publish Agent 不调用 git_push**（系统提示明确禁止）
- **git_push 只在 `POST /tasks/{id}/approve` 人工审批接口触发**
- 审批后 `published_url` 写入 DynamoDB，之后可通过 `GET /tasks/{id}/result` 查询

---

## 数据流全链路

```
POST /tasks {url}
    │
    ▼
ApiHandler Lambda
    │  创建 DynamoDB record（state: queued）
    │  start_execution(sfn, input: {task_id, url, rework_count: 0})
    │
    ▼
Step Functions: Research state
    │  SQS message: {task_id, url, token}
    │
    ▼
SqsHandler Lambda → run_research(task_id, url)
    │
    │  Input:  task_id (str), url (str)
    │  Output: ResearchResult {verdict, notes_path, test_matrix, iam_policy, services}
    │  S3:     tasks/{id}/notes.md
    │
    ▼ send_task_success(token, research_result)
Step Functions: CheckResearchVerdict
    │  verdict == "skip" → Skipped
    │  verdict == "go"   → StartExecute
    │
    ▼ (go path)
Step Functions: StartExecute → AgentCore Bridge (start)
Step Functions: WaitForExecute (60s) → CheckExecuteStatus → IsExecuteDone
    │
    ▼
SqsHandler Lambda → run_execute(task_id, research_result)
    │
    │  Input:  task_id (str), research_result (ResearchResult dict)
    │  Output: ExecuteResult {test_results, final_iam_policy, permissions_added, pitfalls, cost_actual}
    │  S3:     tasks/{id}/evidence/explore-log.{md,json}
    │           tasks/{id}/evidence/verify-log.{md,json}
    │
    ▼ send_task_success(token, execute_result)
Step Functions: Publish state
    │  SQS message: {task_id, research_result, execute_result, token}
    │
    ▼
SqsHandler Lambda → run_publish(task_id, research_result, execute_result)
    │  + reads from S3: notes.md, verify-log.{md,json}, explore-log.json
    │
    │  Input:  task_id (str), research_result (dict), execute_result (dict)
    │  Output: PublishResult {quality_passed, article_path, preview_url, published_url, calibration}
    │           OR {rework_needed: true, rework_type, reason}
    │  S3:     tasks/{id}/article.md
    │
    ▼ send_task_success(token, publish_result)
Step Functions: CheckPublishResult
    │  rework_needed IsPresent → IncrementRework → CheckReworkLimit → (rework loop)
    │  not present             → UpdateCompleted → NotifyComplete → Completed
    │
    ▼ (completed path)
DynamoDB: state = "completed", preview_url = ...

    [Human review]
POST /tasks/{id}/approve
    │  read article from S3
    │  git_push to GitHub
    │  DynamoDB: published_url = "https://chaosreload.github.io/..."
    ▼
GET /tasks/{id}/result → published_url available
```

---

## models.py 需要补充的字段（当前 Gap）

| 模型 | 缺失字段 | 说明 |
|------|---------|------|
| `TestItem` | `api_hints: dict = field(default_factory=dict)` | Research Agent 实际输出包含此字段，models.py 未定义 |
| `ResearchResult` | `complexity` 和 `estimated_cost` 是可选的 | 代码未输出，models.py 定义为必填，应加 `Optional` |
| `ExecuteResult` | `performance_data` 和 `evidence_path` 实际未使用 | 可标记为 deprecated 或移除 |
| `PublishResult` | `preview_url: str` | 实际输出有此字段，models.py 未定义 |
