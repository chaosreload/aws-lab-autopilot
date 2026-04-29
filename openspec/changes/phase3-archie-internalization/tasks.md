# Phase 3 Archie Internalization Tasks

## 1. 做一步写一步 + Checkpoint（P0，依赖 Phase 2 Stage 1/2）

- [ ] 1.1 扩展 DynamoDB `handson-tasks` schema：
      - `progress_log` (list) — append-only，每项 `{step_id, event, data, ts}`
      - `completed_phases` (set) — 已打点的 Phase 名
      - `current_step` (str) — 当前执行步骤（与 Phase 2 Gap 8 heartbeat 共用）
- [ ] 1.2 新增 `append_progress(task_id, step_id, event, data)` 工具
      - 每条追加到 DynamoDB `progress_log` + S3 `tasks/{id}/progress.jsonl`
      - 要求：单次调用 <100ms，失败不阻塞主流程（best-effort）
- [ ] 1.3 新增 `read_progress(task_id)` 工具
      - 返回 `{completed_phases, current_step, last_events}`
      - Agent 启动必调一次，用于 resume-from-checkpoint
- [ ] 1.4 新增 `mark_phase_complete(task_id, phase_name)` 工具
      - 校验 phase 前置条件（见每 Agent 的 Phase 依赖表）
      - 前置条件不满足 → 抛 `PhasePreconditionError`，Agent 必须修正
      - 通过则写入 `completed_phases` set
- [ ] 1.5 Research Agent prompt 改造：8 个 Step 显式化
      - 每个 Step 末尾必须调 `mark_phase_complete("step_N")`
      - Step 4（list_bedrock_models）的 precondition：输出 `services` 包含 `bedrock` 时必须调用
      - Step 5（test matrix）的 precondition：`step_1` 和 `step_2` 已完成
- [ ] 1.6 Execute Agent prompt 改造：4 个 Phase 显式化
      - Phase A 末尾必须调 `mark_phase_complete("phase_a_discovery")`
      - Phase B 的 precondition：`phase_a_discovery` 已完成
      - Phase C 的 precondition：`phase_b_infra` 已完成（api_call 任务直接跳过 Phase B，但必须显式打点 `phase_b_skipped`）
      - 每条 `aws_cli_execute` 执行后必须调 `append_progress`
- [ ] 1.7 Agent 入口改造：`run_research` / `run_execute` / `run_publish` 入口先调 `read_progress`，已有 `completed_phases` 的跳过不重复执行
- [ ] 1.8 测试：模拟 Execute Agent 执行到 Phase C 第 3 条命令后被强杀，重启后从第 4 条继续

## 2. 报错先查文档（P0，prompt + evidence schema 改动）

- [ ] 2.1 Execute Agent prompt 改造：
      - 任何 `exit_code != 0` 命令，在重试/mark fail 前必须至少调用一次 `aws_knowledge_read`
      - evidence log 对应条目必须包含 `doc_checked: true` + `doc_url: "..."` 字段
- [ ] 2.2 扩展 Evidence Record schema（explore-log.json / verify-log.json）：
      ```json
      {
        "tool": "aws_cli_execute",
        "command": "...",
        "exit_code": 255,
        "doc_checked": true,
        "doc_url": "https://docs.aws.amazon.com/...",
        "doc_conclusion": "usage_error|aws_limitation|unknown"
      }
      ```
- [ ] 2.3 Publish Agent 校验：`pitfalls[]` 里每一条 `verified=true` 的记录，必须在 evidence log 能匹配到对应的 `doc_checked=true` 条目；否则降级为 `verified=false` 或丢弃
- [ ] 2.4 测试：构造故意失败的 T（例如 invalid region），验证 doc_checked 字段被正确填充

## 3. SKIP 结构化记录（P0，output schema 变更）

- [ ] 3.1 升级 `models.py` 中 `ExecuteResult.test_results` 类型：
      - 从 `dict[str, str]` → `dict[str, TestResult]`
      - `TestResult = {result: "pass"|"fail"|"skip", reason?: str, detail?: str, key_measurement?: str}`
- [ ] 3.2 Execute Agent prompt 扩展 SKIP 规范：
      - acceptable reasons 白名单：`quota_limit` / `region_unavailable` / `prerequisite_failed` / `service_preview` / `cost_budget`
      - 禁止 reason：`too_complex` / `similar_to_previous` / `not_enough_time`
- [ ] 3.3 双轮 merge 规则同步更新：verify 轮权威，explore skip → verify 必须重新尝试
- [ ] 3.4 Publish Agent 读取 SKIP 时，在文章 `## 测试结果` 表格中用 "未执行（原因：...）" 显式表达，不隐藏
- [ ] 3.5 Step Functions CheckPublishResult 新增校验：skip 率 >50% → 触发 `rework_type: redesign`

## 4. 写作口吻 automated check（P0，quality_check 扩展）

- [ ] 4.1 `quality_check` 新增第 8 条规则 `voice_external`：
      ```python
      BLOCKLIST = [
          r"已核实官方文档", r"经 aws[- ]knowledge 核实",
          r"sub[- ]agent", r"workspace", r"SOUL\.md", r"MEMORY\.md",
          r"剧透", r"坑爹", r"AWS 骗", r"真的(很|快)",
          r"可能会", r"也许", r"\bmight\b", r"\bmay cause\b",
      ]
      ```
- [ ] 4.2 命中任一 regex → `passed=false, failure="internal_voice_leaked", hits=[{pattern, line, snippet}]`
- [ ] 4.3 Publish Agent 系统提示增加"校准依据的正确表述方式"示例（✅/❌ 对照，来自 Archie SKILL.md 写作口吻章节）
- [ ] 4.4 注意：`可能会 / 也许` 是全文禁用，但 `## 结论与建议` 章节允许 "建议" / "推荐" 等软建议词，regex 需精确
- [ ] 4.5 测试：用 Aurora PV4 首轮被打回的文章（含 "AWS 骗" 类用词）跑 quality_check，必须 `passed=false`

## 5. Quota / Limit 前置检查（P1）

- [ ] 5.1 新增工具 `aws_service_quotas_read(service_code, quota_code)`
      - 底层调用 `aws service-quotas get-service-quota` + `get-aws-default-service-quota`
      - 返回 `{current_limit, default_limit, adjustable, unit}`
- [ ] 5.2 Research Agent prompt 扩展：
      - 对 `task_type ∈ {infrastructure, mixed}`，test_matrix 每条涉及资源创建的测试必须在 `api_hints` 附：
        ```json
        "quota_verified": {
          "quota_code": "L-XXXXXXX",
          "current_limit": 20,
          "required": 4
        }
        ```
      - 若 `required > current_limit` → verdict 仍 `go` 但 test_matrix 标注 `skip_reason: quota_limit`，不要让 Execute 阶段撞墙
- [ ] 5.3 测试：构造一个需要 80 vCPU 但配额 20 的场景，Research Agent 应输出 quota_verified + 自动 skip

## 6. Calibration 逐条声明 traceability（P1）

- [ ] 6.1 新增工具 `write_calibration_log(task_id, claims)`
      - `claims = [{statement, doc_quote, doc_url, verdict: "match"|"contradict"|"undocumented"}, ...]`
      - 写入 `s3://.../tasks/{id}/calibration_log.md`
- [ ] 6.2 Publish Agent prompt 改造（Step B Calibration 扩展）：
      - 对文章每条技术声明（API 名、limit、model ID、region 可用性）调用 `aws_knowledge_read_publish` 取证
      - 取证结果聚合后调 `write_calibration_log` 一次性写入
      - 最低要求：`verdict=match` 的声明数 ≥ 3
- [ ] 6.3 `quality_check` 新增第 9 条 `calibration_traceable`：
      - 从 S3 读 `calibration_log.md`
      - 解析表格，校验 `match` 行数 ≥ 3
      - 若存在 `contradict` 且文章未修正 → `passed=false`
- [ ] 6.4 Step Functions CheckPublishResult 增加前置：`quality_passed=true` 且 calibration_log 存在才能进 UpdateCompleted

## 7. 文档与反哺

- [ ] 7.1 在 `openspec/specs/main.md` 补一段 "Archie Six-Step Loop Internalization" requirement（引用本 change）
- [ ] 7.2 README.md 更新：列出 Agent prompt 层的工具清单（新增 4 个工具）
- [ ] 7.3 用 #114 Aurora PV4 作为验收回放任务，产出回放报告 `memory/phase3-replay-report.md`
- [ ] 7.4 反哺 Archie SKILL.md 的 4 条（autopilot → Archie）：
      - test_matrix 合并策略（Type A≤3 + Type B/C 各合 1）
      - Dual-round explore+verify
      - IAM 三层模型 + 按任务 scoped role
      - Bedrock 强制 list_foundation_models
      - 这 4 条不在本 change 的代码范围内，在 workspace-solutions 侧提 PR

## 验收清单

- [ ] 所有新增工具有 pytest 覆盖
- [ ] 修改的 prompt 通过本地 `run_local.py` 单轮跑通不报错
- [ ] #114 回放任务满足 proposal.md 的 Success Criteria 全部 7 条
- [ ] openspec/specs/main.md 已同步更新
