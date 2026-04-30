# Phase 2 Stage 1 — TestEnvironment Spec

_Research Agent 的输出必须完整描述"测试环境"，不只是测试矩阵_

---

## 1. TestEnvironment Model

### Requirement: TestEnvironment Pydantic Model MUST Exist

系统 MUST 在 `src/common/models.py` 定义 `TestEnvironment` 模型：

```python
class VpcPreference(str, Enum):
    NONE = "none"                      # 测试全程无 VPC（纯 API 调用）
    DEFAULT_VPC = "default_vpc"        # 使用 account 默认 VPC
    LAB_VPC_REQUIRED = "lab_vpc_required"  # 必须由 Execute 创建 lab VPC

class Prerequisite(BaseModel):
    type: str                 # bedrock_model_access | service_quota | bucket_exists | ...
    description: str          # 一行可读说明
    params: dict = Field(default_factory=dict)  # type-specific

class CleanupPolicy(BaseModel):
    ttl_hours: float          # 资源最长存活时间（Gap 9 EventBridge 兜底用）
    on_failure: str = "terminate_all"   # terminate_all | preserve_for_debug | ask_human
    orphan_scan: bool = True  # 是否参与 orphan scanner

class TestEnvironment(BaseModel):
    region: str                                    # us-east-1
    region_reason: str                             # 一行决策说明
    account_id: str                                # 来自 task.aws_identity.Account
    vpc_preference: VpcPreference = VpcPreference.NONE
    tag_strategy: dict[str, str]                   # 强制 key：autopilot:task_id, autopilot:stage, autopilot:owner
    budget_limit_usd: float                        # 静态估算值；Execute 超额必停
    cleanup_policy: CleanupPolicy
    prerequisites: list[Prerequisite] = Field(default_factory=list)
```

### Requirement: ResearchResult MUST Include environment Field

`ResearchResult` MUST 增加字段：

```python
class ResearchResult(BaseModel):
    ...
    environment: TestEnvironment  # Stage 1 新增，Research Agent 必填
```

- verdict == "go" → `environment` MUST 完整填写（所有字段非默认值）
- verdict == "skip" → `environment` MAY 缺省，但 `region` 仍 MUST 非空（即便是默认 us-east-1）

---

## 2. Region Decision Procedure

### Requirement: Research Agent MUST Determine Region via Documented Procedure

Research Agent MUST 按以下顺序决定 `environment.region`：

1. **读公告正文**：检查是否包含 region 限制表述（如 "available in us-east-1 only", "launching in 5 regions"）
2. **调 `aws_knowledge_region` 工具交叉验证**：如公告含具体服务名/API/CFN 资源，调用该工具确认 supported regions
3. **决策优先级**：
   - 公告明确限制 → 必须选限制内的一个 region
   - 公告无限制 + `aws_knowledge_region` 返回广泛 region 列表 → 默认 `us-east-1`
   - 公告无限制 + `aws_knowledge_region` 返回空/错误 → 默认 `us-east-1`，`region_reason` 注明 "tool returned no data; defaulting"
4. **Region 白名单兜底**：若选中 region 不在 `{us-east-1, us-west-2, ap-southeast-1}` 内，Agent MUST 回滚到 `us-east-1` 并在 `region_reason` 说明原因（autopilot 目前只在这三个 region 支持 Execute）

### Requirement: region_reason Traceability

`environment.region_reason` MUST 满足：

- 一行字符串（≤ 200 字符）
- 必须引用**证据**：`"announcement §2 paragraph 3"` 或 `"aws_knowledge_region returned [us-east-1, us-west-2, ...]"`
- 不能是空洞的 `"selected us-east-1"`，必须给出**为什么**

---

## 3. Account ID Binding

### Requirement: account_id MUST Match Calling Identity

Research Agent MUST 从 input context 读取 `task.aws_identity.Account` 并填入 `environment.account_id`。

- **禁止** Agent 自行选 account id
- Dispatcher / API 层 MUST 在调 Research Agent 时把 `aws_identity` 作为 context 传入（Stage 0 已有 `task_store.create_task(..., aws_identity=...)`，Stage 1 只需把它透传给 Research Agent）

---

## 4. Tag Strategy

### Requirement: Minimum Mandatory Tag Keys

`environment.tag_strategy` MUST 至少包含以下 3 个 key：

| Key | Value 规则 | 用途 |
|---|---|---|
| `autopilot:task_id` | `task.task_id` | Gap 9 orphan scanner 过滤 |
| `autopilot:stage` | `research` / `execute` / `publish`（Agent 填时用 `execute`） | 按阶段清理 |
| `autopilot:owner` | `archie`（固定值） | 多 owner 环境下隔离 |

### Requirement: Execute MUST Propagate Tags

Execute Agent（Stage 2 及之后）MUST 在所有 AWS 资源创建调用（`run_instances`、`create_cluster`、`create_function` 等）里强制加上 `environment.tag_strategy` 的 tags。

- 无 tag 创建 → Execute Agent MUST 报 `TaggingComplianceError` 并在清理后停止任务
- 对不支持 tag-on-create 的资源（例如 ECR 镜像），MUST 在创建完成后立即调 `TagResource`

---

## 5. Budget Limit

### Requirement: Budget Estimation by task_type

Research Agent MUST 按以下上限填 `budget_limit_usd`：

| task_type | 上限 ($ USD) | 说明 |
|---|---|---|
| `api_call` | ≤ 1 | 纯 API 调用，无资源驻留 |
| `infrastructure` | ≤ 20 | 含资源创建+2h 级驻留（Aurora、EC2 t3.medium 类） |
| `mixed` | ≤ 20 | 按 infrastructure 上限处理 |
| long-running (estimated_execution_minutes > 180) | ≤ 100 | 压测 / 微调类；Agent MUST 在 notes 额外说明 |

超出上限 → Agent MUST 降方案（减少测试项、缩 TTL、换更小实例），重新填一版 `environment`。

### Requirement: Execute MUST Hard-stop on Budget Exceeded

Execute Agent MUST 在每完成 1 个 test item 后估算累计花费（基于 pricing API 或 static table），累计 ≥ `environment.budget_limit_usd × 0.9` 时：

- 打印警告进 progress_log
- 再超 → 立即调 cleanup + 标任务 `budget_exceeded`

---

## 6. Cleanup Policy

### Requirement: cleanup_policy MUST Declare TTL

`environment.cleanup_policy.ttl_hours` MUST 满足：

- `api_call` 任务：≤ 0.5 小时
- `infrastructure` 任务：≤ 4 小时
- long-running 任务：≤ 8 小时；超过 → Agent MUST 在 notes 写明"长跑授权原因"

### Requirement: SFN Task State MUST Enforce TTL

Stage 3 实施 SFN DAG 时，每个 `Task` state 的 `TimeoutSeconds` MUST ≤ `cleanup_policy.ttl_hours × 3600`。

---

## 7. Prerequisites

### Requirement: prerequisites MUST Be Verified Before test_matrix Runs

`environment.prerequisites` 列表的**所有**项 MUST 在 Execute Agent 启动 test_matrix 之前通过验证。

支持的 type 白名单（Stage 1 至少覆盖 3 类）：

| type | params 必填 | 验证方式 |
|---|---|---|
| `bedrock_model_access` | `model_id` | `bedrock:GetFoundationModelAvailability` |
| `service_quota` | `service`, `quota_code`, `required` | `service-quotas:GetServiceQuota` |
| `bucket_exists` | `bucket_name` | `s3:HeadBucket`（否则创建） |

### Requirement: Prerequisite Failure Handling

- 验证失败 → Execute Agent MUST 标任务 `needs_human`，不启动 test_matrix
- 验证成功 → `append_progress(event: "prereq_verified", data: {...})`（Phase 3 change 提供的 tool）

---

## 8. Backward Compatibility

### Requirement: Old research_result JSON Accepts Defaults

若 upstream 提供的 `research_result` JSON 没有 `environment` 字段（例如存在 Stage 0 时期的 S3 record），pydantic MUST 用 defaults 补齐且 verdict MUST 降级为 `needs_human`，不允许静默继续。

---

## 9. Validation Tests

Stage 1 交付前 MUST 跑通以下回归：

| # | 输入 | 期望 |
|---|---|---|
| V1 | S3 Files What's New URL（无 region 限制） | `region=us-east-1`, `region_reason` 引用 "no restriction" |
| V2 | Mock URL 含 `"available in us-west-2 only"` | `region=us-west-2`, `region_reason` 引用公告限制 |
| V3 | Mock URL 无限制 + `aws_knowledge_region` 返回错误 | `region=us-east-1`, `region_reason` 注明 tool 错误 |
| V4 | 任意 go 任务 | `tag_strategy` 至少有 3 个 mandatory key |
| V5 | `task_type=infrastructure` 任务 | `budget_limit_usd ∈ (0, 20]`, `cleanup_policy.ttl_hours ≤ 4` |
| V6 | 使用 Bedrock 的任务 | `prerequisites` 至少有 1 条 `bedrock_model_access` |
| V7 | 强行把 `environment.account_id` 改成非 `aws_identity.Account` 的值 | Research Agent MUST 重写回正确值（在 post-process 阶段拦截） |
