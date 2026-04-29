# Phase 3 — Archie 六步闭环内化

## Why

aws-lab-autopilot 当前的 Agent prompt 已经翻译了 `workspace-solutions/skills/aws-article/SKILL.md`（Archie 的六步闭环）约 90% 的**显性规则**：evidence-first、双轮执行、7 红线质量门、踩坑分类、aws-knowledge 校准 ≥3 次、中文写作、代码脱敏等。

但在真实执行中，autopilot 仍暴露三类问题：

1. **Research 阶段计划不够细** → Execute 阶段反复失败（因为 Research 跳过了 list_bedrock_models / quota 预检等关键动作）
2. **Execute 阶段盲目重试** → 报错不查文档、SKIP 无结构化记录、进度不增量持久化
3. **Publish 阶段口吻出戏** → 内部流程语（"已核实官方文档"、"sub-agent 秒退"等）泄漏进对外文章

根因是：Archie 的六步闭环中有 7 条"每次都在用"的隐性规则没有被翻译成 autopilot 的**工具约束**或 **automated check**，只靠 prompt 自由度要求 Agent 遵守，LLM 会频繁偷工。

参考：附录 A（[aws-lab-autopilot 复活路线图](https://my.feishu.cn/docx/U5RTdqlTTojGOzxsVFqcIEtfnZb)）。

## Scope

将以下 7 条 Archie 隐性规则从"prompt 自觉"升级为"工具强制 + 自动化校验"：

| # | 规则 | 补丁形式 | 优先级 |
|---|---|---|---|
| 1 | 做一步写一步 × 每步前回读 | 新增 `append_progress` / `read_progress` 工具 | P0 |
| 2 | Checkpoint 机制（不允许跳步） | 新增 `mark_phase_complete` 工具 + 前置条件校验 | P0 |
| 3 | 报错先查文档再下结论 | Prompt + evidence schema 要求 `doc_checked` 字段 | P0 |
| 4 | SKIP 结构化记录 | `test_results` schema 升级为 dict of dict | P0 |
| 5 | 写作口吻 automated check | `quality_check` 新增第 8 条 `voice_external` | P0 |
| 6 | Quota / Limit 前置检查 | 新增 `aws_service_quotas_read` 工具 | P1 |
| 7 | Calibration 逐条声明 traceability | Publish 新增 `calibration_log.md` S3 产出物 + quality gate | P1 |

## Out of Scope

- 架构层（Heartbeat / TTL / DAG 并行 / Slack webhook）由 Phase 2 (`phase2-architecture-upgrade`) 承担
- Test matrix 合并策略反哺到 Archie SKILL.md（workspace-solutions 侧改动，不在 autopilot 仓库）
- AgentCore Memory 真正集成（跨阶段，独立排期）

## Dependencies

- **Phase 2 Stage 1**（DynamoDB tasks 表）→ 必须先完成，#1 `append_progress` 需要 schema 已就位
- **Phase 2 Stage 2**（AgentCore Runtime 上线）→ #1 与 heartbeat 共用数据源，建议同期实现
- **Phase 2 Stage 5** 原计划 2-3 天，吸纳本 change 后上调到 **4-5 天**

## Success Criteria

使用 #114（Aurora Serverless v2 PV4）作为回放任务，必须满足：

- [ ] Execute Agent 强杀后重启，能从 `current_step` 跳过已完成测试项（依赖 #1 + Phase 2 Gap 8）
- [ ] Research Agent 对 infrastructure 类任务强制产出 `quota_verified` 字段
- [ ] 任何 `exit_code != 0` 命令的 evidence log 有 `doc_checked: true, doc_url: ...` 字段
- [ ] `test_results` 所有 skip 项包含 `reason` 且 reason 在 acceptable 白名单内
- [ ] 文章任意位置出现禁用词 → `quality_check` 返回 `failed: voice_external`
- [ ] `s3://.../tasks/{id}/calibration_log.md` 存在且 ✅ 行数 ≥ 3
- [ ] Research Agent 8 个 Phase / Execute Agent 4 个 Phase 全部通过 `mark_phase_complete` 打点
