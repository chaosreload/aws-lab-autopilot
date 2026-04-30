# Phase 2 Stage 1 — TestEnvironment Schema

## Why

Stage 0 验证了 `POST /tasks → DDB → Research Agent → research_done` 最小链路能本地跑通。但 Research Agent 当前的输出（`ResearchResult`）只覆盖了**测试矩阵本身**（test_matrix / iam_policy / services / task_type），**没有描述运行这些测试需要的"环境"**。

这次 Stage 0 收尾时就踩到了坑：

- 2026-04-30 Stage 0 验收后，我发现 Research Agent 没输出 `region` 字段，觉得这是"Stage 0.f 小补丁"直接加到了 `ResearchResult` + prompt + `_DEFAULTS`（commit `4c65b93`）
- weichao 指出：**region 不是零散字段**，它属于"测试环境描述"这个完整概念，应该和 account / VPC / tag / budget / cleanup policy 一起在 Stage 1 统一设计 schema
- commit 已回滚，但决策沉淀在这个 change 里

换句话说：Research Agent 的输出不只是"**要测什么**"，还必须包含"**在哪测、谁去测、测完谁负责清理、最多花多少钱**"。这些环境维度一旦在 Stage 1 定死 schema，下游 Execute Agent 就有统一的输入契约，Phase 2 的 Gap 9（TTL + orphan scanner）和 Gap 10（SFN Parallel Map）也有了可依赖的 tag/region 策略。

## Scope

Stage 1 交付的**核心产出**：`TestEnvironment` pydantic 模型，挂到 `ResearchResult.environment`，由 Research Agent 负责填充，由 Execute Agent 消费。

### TestEnvironment 字段（Stage 1 要覆盖的维度）

| 字段 | 类型 | 来源 | 例子 / 约束 |
|---|---|---|---|
| `region` | str | Agent 读公告 + 调 `aws_knowledge_region` 判断 | `us-east-1`；若公告限制 → 必须在限制集合内；否则默认 `us-east-1` |
| `region_reason` | str | Agent 推理结果一行总结 | `"announcement limits to us-east-1 only"` / `"no restriction; defaulting to us-east-1"` |
| `account_id` | str | autopilot 启动时 `sts:GetCallerIdentity` 注入 | `595842667825`（由 Research Agent 从 `task.aws_identity` 读出来填，**不是** Agent 自选） |
| `vpc_preference` | enum | Agent 根据 task_type 决定 | `none` / `default_vpc` / `lab_vpc_required`；`none` 表示测试全程无 VPC 网络（例：纯 Bedrock API 调用） |
| `tag_strategy` | dict | 固定模板 + Agent 填占位 | 必须包含 `autopilot:task_id`、`autopilot:stage`、`autopilot:owner=archie`；供 Gap 9 orphan scanner 使用 |
| `budget_limit_usd` | float | Agent 按 task_type + estimated_execution_minutes 估算 | `api_call` ≤ $1、`infrastructure` ≤ $20（Aurora/EC2 类）、`long_running` ≤ $100（压测）；超出须 Agent 降方案 |
| `cleanup_policy` | dict | Agent 根据资源类型声明 | `{ttl_hours: 2, on_failure: "terminate_all", orphan_scan: true}`；Gap 9 按此值兜底 |
| `prerequisites` | list[dict] | Agent 在 test_matrix 之前必须验证 | 例：`[{type: "bedrock_model_access", model_id: "global.anthropic..."}, {type: "service_quota", service: "ec2", quota_code: "L-1216C47A", required: 4}]`；Phase 3 change #6 的 `aws_service_quotas_read` 在这里落点 |

### 与已有字段的关系

- `ResearchResult.services`（已有）→ 改由 `TestEnvironment` 派生，或保持冗余但 `environment.services` 为权威
- `ResearchResult.estimated_execution_minutes`（已有）→ 保持，但 budget_limit_usd 要能从它推导出的粗估下限

### 下游契约变化

- **Execute Agent** MUST 读 `research_result.environment.region` 决定 boto3 session region（不再读 `AUTOPILOT_AWS_REGION` env）
- **Execute Agent** MUST 对所有创建的 AWS 资源强制打上 `environment.tag_strategy` 声明的 tags —— 无 tag 资源视为 bug
- **Execute Agent** MUST 在任何操作前先通过 `prerequisites` 检查；失败 → 回写 `needs_human` 状态，不启动 test_matrix
- **Dispatcher / Stage 3 SFN** MUST 读 `cleanup_policy.ttl_hours` 作为 Step Functions state `TimeoutSeconds` 的硬上限

## Out of Scope

下面这些明确**不在** Stage 1，属于后续 Stage：

- **Stage 2（Execute → AgentCore Runtime）**：`TestEnvironment` 的 Runtime binding（AgentCore 用哪个 session / role 来 run）
- **Stage 3（SFN DAG）**：根据 `environment.cleanup_policy.orphan_scan` 真正注册 EventBridge Rule
- **Stage 4（Slack webhook）**：`environment.owner` / `environment.notification_channel` 等通知维度
- **Phase 3 change `append_progress` / `mark_phase_complete`**：独立范围，但共享 DynamoDB schema

## Dependencies

- **Stage 0 已完成**（prerequisite）：`src/autopilot/` 骨架 + `aws_session.py` 4 级凭证链 + `task_store.aws_identity` 字段
- **Phase 2 Gap 9**（TTL + orphan scanner）：需要 `tag_strategy` 明确后才能实现 EventBridge Rule
- **Phase 3 change #6**（aws_service_quotas_read）：是 `prerequisites` 字段下的一个具体实现，Stage 1 和 Phase 3 可以并行，`prerequisites` schema 先定

## Success Criteria

使用 S3 Files What's New URL 回归：

- [ ] `ResearchResult.environment` 字段存在，以上 8 个子字段全部非空（region_reason 可 1 句话）
- [ ] `environment.account_id` 等于 `task.aws_identity.Account`（Agent 不能擅自换 account）
- [ ] `environment.tag_strategy` 至少包含 `autopilot:task_id` 3 个强制 tag
- [ ] `environment.budget_limit_usd` 对 S3 Files 这类 infrastructure 任务落在 `(0, 20]` 区间
- [ ] `environment.prerequisites` 非空（S3 Files 至少要有 "s3_bucket 可创建" 类前置）
- [ ] 用一条"被限制到 us-east-1 only"的假 What's New URL（mock）回归 → region 必填为 `us-east-1`，`region_reason` 引用限制
- [ ] 用一条"无 region 限制"的 URL 回归 → region = 默认 `us-east-1`，`region_reason` 显示 "no restriction"

## Non-goals / 明确拒绝的诱惑

为了避免 Stage 1 自己再 scope creep，以下诱惑**明确拒绝**：

- ❌ 不做 multi-region 并行测试（Stage 3 SFN Parallel Map 里考虑）
- ❌ 不做跨 account 测试（Organizations / control tower 场景留到未来）
- ❌ 不做动态 IAM policy 生成（`iam_policy` 保持 Research Agent 直接输出整份 policy，不拆细粒度）
- ❌ 不做成本实时监控（只做静态 budget_limit_usd 估算 + Execute 超额硬停，CloudWatch Budgets 集成留后续）
