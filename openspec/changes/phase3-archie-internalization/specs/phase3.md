# Phase 3 — Archie Internalization Spec

_将 workspace-solutions/skills/aws-article/SKILL.md 的隐性规则从 prompt 自觉升级为工具强制 + automated check_

---

## 1. Progress Tracking（做一步写一步）

### Requirement: append_progress Tool
系统 MUST 提供 `append_progress(task_id, step_id, event, data)` 工具：
- MUST 在 <100ms 内返回（best-effort，失败不阻塞 Agent 主流程）
- MUST 同时写入 DynamoDB `handson-tasks.progress_log` 和 S3 `tasks/{id}/progress.jsonl`
- 每条记录 MUST 包含 `{step_id, event, data, ts (ISO 8601 UTC)}`

### Requirement: read_progress Tool
系统 MUST 提供 `read_progress(task_id)` 工具：
- MUST 返回 `{completed_phases: set[str], current_step: str, last_events: list[dict]}`
- Agent 入口（run_research / run_execute / run_publish）MUST 作为第一个动作调用

### Requirement: Incremental Progress Logging (Execute Agent)
Execute Agent MUST 在每条 `aws_cli_execute` 完成后立即调 `append_progress`：
- 禁止累积到 Phase 末尾再一次性写入
- 每条记录 `event` 取值范围：`cli_success` / `cli_failure` / `iam_added` / `resource_created` / `resource_cleaned` / `test_decision`

---

## 2. Phase Checkpoint（不允许跳步）

### Requirement: mark_phase_complete Tool
系统 MUST 提供 `mark_phase_complete(task_id, phase_name)` 工具：
- MUST 校验该 phase 的 precondition；不满足 → 抛 `PhasePreconditionError`
- 通过校验 → 写入 `completed_phases` set 并 append 到 `progress_log`

### Requirement: Research Agent Phase Definitions
Research Agent MUST 将 SYSTEM_PROMPT 的 8 个 Step 显式化为 8 个 Phase，每个 Phase 有 precondition：

| Phase | Precondition |
|---|---|
| `step_1_doc_search` | 无 |
| `step_2_region_check` | 仅 verdict=skip 路径 required |
| `step_3_verdict` | `step_1_doc_search` completed |
| `step_4_model_check` | `step_3_verdict`=go 且 services 含 `bedrock*`（否则跳过但需显式调 `mark_phase_complete("step_4_skipped")`） |
| `step_5_test_matrix` | `step_3_verdict` completed |
| `step_6_iam_derive` | `step_5_test_matrix` completed |
| `step_7_time_estimate` | `step_5_test_matrix` completed |
| `step_8_write_notes` | 以上所有 completed |

### Requirement: Execute Agent Phase Definitions

| Phase | Precondition |
|---|---|
| `phase_a_discovery` | 无 |
| `phase_b_infra` | `phase_a_discovery` completed；task_type=api_call 时直接 `phase_b_skipped` |
| `phase_c_explore` | `phase_b_infra` 或 `phase_b_skipped` completed |
| `phase_c_verify` | `phase_c_explore` completed |
| `phase_d_cleanup` | `phase_c_verify` completed |

### Requirement: Resume from Checkpoint
Agent 进程被强杀后重启时 MUST：
- 先 `read_progress(task_id)`
- 跳过 `completed_phases` 中已完成的 Phase
- 从 `current_step` 恢复执行

---

## 3. Error Must Check Docs（报错先查文档）

### Requirement: Mandatory Doc Check on Non-Zero Exit
Execute Agent MUST 对任何 `exit_code != 0` 的命令，在重试或 mark fail 前至少调用一次 `aws_knowledge_read`。

### Requirement: Extended Evidence Schema
Evidence log 的每条失败记录 MUST 包含以下扩展字段：
- `doc_checked: bool`
- `doc_url: str`（调用的文档 URL，多个时取第一个）
- `doc_conclusion: "usage_error" | "aws_limitation" | "unknown"`

### Requirement: Pitfall Traceability
Publish Agent MUST 校验：`execute_result.pitfalls[]` 里每条 `verified=true` 的记录，必须在 evidence log 能匹配到 `doc_checked=true` 的条目；无法匹配的自动降级为 `verified=false` 或丢弃不引用。

---

## 4. Structured Skip（SKIP 结构化）

### Requirement: test_results Schema Upgrade
`ExecuteResult.test_results` 类型 MUST 从 `dict[str, str]` 升级为 `dict[str, TestResult]`：

```python
@dataclass
class TestResult:
    result: Literal["pass", "fail", "skip"]
    reason: Optional[str] = None      # required when result=skip
    detail: Optional[str] = None
    key_measurement: Optional[str] = None
```

### Requirement: Skip Reason Whitelist
`result=skip` 时 `reason` MUST 取自白名单：`quota_limit` / `region_unavailable` / `prerequisite_failed` / `service_preview` / `cost_budget`。
禁止值：`too_complex` / `similar_to_previous` / `not_enough_time`。

### Requirement: Skip Handling in Publish Agent
Publish Agent 写作 `## 测试结果` 表格时 MUST 显式列出 skip 项，格式 `未执行（原因：{reason 白名单项的中文释义}）`。

### Requirement: Skip Rate Gate
Step Functions `CheckPublishResult` state MUST 在 skip 率 >50% 时触发 `rework_type: redesign` 回到 Research 阶段。

---

## 5. External Voice Check（写作口吻）

### Requirement: quality_check rule #8 — voice_external
`quality_check` MUST 新增第 8 条规则 `voice_external`，regex blocklist 至少包含：

| Category | Patterns |
|---|---|
| 内部流程语 | `已核实官方文档`、`经 aws[- ]knowledge 核实` |
| 内部调试痕迹 | `sub[- ]agent`、`workspace`、`SOUL\.md`、`MEMORY\.md` |
| 情绪化表达 | `剧透`、`坑爹`、`AWS 骗`、`真的(很\|快)` |
| 推测性用词 | `可能会`、`也许`、`\bmight\b`、`\bmay cause\b` |

命中任一 → `passed=false, failure="internal_voice_leaked", hits=[{pattern, line, snippet}]`。

### Requirement: Calibration Phrasing Examples in Publish Prompt
Publish Agent SYSTEM_PROMPT MUST 包含校准依据的 ✅/❌ 对照示例（引用 Archie SKILL.md "写作口吻"章节）。

---

## 6. Quota / Limit Pre-Check（前置配额检查）

### Requirement: aws_service_quotas_read Tool
系统 MUST 提供 `aws_service_quotas_read(service_code, quota_code)` 工具：
- 返回 `{current_limit, default_limit, adjustable, unit}`
- 底层调用 AWS Service Quotas API

### Requirement: Research Agent Quota Verification
Research Agent 对 `task_type ∈ {infrastructure, mixed}` 的任务，test_matrix 中每条涉及资源创建的测试 MUST 在 `api_hints` 包含：

```json
"quota_verified": {
  "quota_code": "L-XXXXXXX",
  "current_limit": 20,
  "required": 4
}
```

若 `required > current_limit` → 该测试项 MUST 在 test_matrix 标注 `skip_reason: quota_limit`，Research verdict 仍 `go`。

---

## 7. Calibration Traceability（逐条声明校准）

### Requirement: write_calibration_log Tool
系统 MUST 提供 `write_calibration_log(task_id, claims)` 工具，写入 S3 `tasks/{id}/calibration_log.md`：

```markdown
| 声明 | aws-knowledge 原文 | 文档 URL | 结论 |
| --- | --- | --- | --- |
| Aurora PG 16.3+ 支持 MinACU=0 | "Aurora Serverless v2 supports minACU=0 for..." | https://... | ✅ match |
| sub-second resume | (未在文档中找到) | - | ⚠️ undocumented |
```

### Requirement: Publish Agent Calibration Workflow Upgrade
Publish Agent 的 Step B（Calibration）MUST：
- 对文章每条技术声明（API 名、limit、model ID、region 可用性）调用 `aws_knowledge_read_publish` 取证
- 取证结果聚合后调 `write_calibration_log`
- 最低要求：`verdict=match` 的行数 ≥ 3

### Requirement: quality_check rule #9 — calibration_traceable
`quality_check` MUST 新增第 9 条规则 `calibration_traceable`：
- 从 S3 读取 `calibration_log.md`
- 校验 `match` 行数 ≥ 3；存在 `contradict` 且文章未修正 → `passed=false`

### Requirement: Step Functions Gate Extension
`CheckPublishResult` state MUST 在 `quality_passed=true` 且 `calibration_log.md` 存在 且 `match` 行数 ≥ 3 时才允许进入 `UpdateCompleted`。

---

## 8. Mapping to Archie SKILL.md

| Archie 铁律（SKILL.md 章节） | 本 Spec 对应条款 |
|---|---|
| 做一步写一步 + 每步前回读（Step ④ 铁律）| §1（Progress Tracking）|
| Checkpoint 机制（六步闭环头部）| §2（Phase Checkpoint）|
| 报错先查文档再下结论（Step ④ 错误处理流程）| §3（Error Must Check Docs）|
| 跳过测试须标注 `[SKIP: 原因]`（Step ④）| §4（Structured Skip）|
| 写作口吻铁律（SKILL.md "写作口吻" 章节）| §5（External Voice Check）|
| 测试前查限制（Step ③ 铁律）| §6（Quota Pre-Check）|
| Step ④.5 逐条声明校准 | §7（Calibration Traceability）|

---

## 9. Non-Goals

- 架构层改动（Heartbeat / TTL / DAG 并行 / Slack push）→ 见 `phase2-architecture-upgrade`
- Test matrix 合并策略反哺 Archie → workspace-solutions 仓库 PR（非本 change）
- AgentCore Memory 真正集成 → 独立排期
