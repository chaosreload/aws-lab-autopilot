# Phase 1 MVP Tasks

## Infrastructure
- [ ] 1.1 SAM/CDK 模板：API Gateway + Lambda + DynamoDB + S3 + SQS
- [ ] 1.2 Step Functions ASL 定义（基于已验证的 17-state 状态机）
- [ ] 1.3 AgentCore Runtime 部署（3 个 agent containers）
- [ ] 1.4 IAM Role 和 Permission 配置

## Research Agent
- [x] 2.1 Strands Agent 骨架 + AgentCore Runtime 接入
- [x] 2.2 aws_knowledge_read tool
- [x] 2.3 aws_knowledge_region tool
- [x] 2.4 write_notes tool（S3）
- [x] 2.5 memory_search tool（AgentCore Memory）— stub，返回空结果
- [x] 2.6 Research Agent 提示词调优

## Execute Agent
- [x] 3.1 Strands Agent 骨架 + Shell Command API 接入
- [x] 3.2 aws_cli_execute tool（含 Safety Guard pre/post）
- [x] 3.3 iam_add_permission tool（动态调权）— stub，记录但不执行
- [x] 3.4 track_resource + cleanup_resource tools
- [x] 3.5 write_execute_log tool（S3 测试日志）
- [ ] 3.6 memory_create tool（踩坑记录）
- [x] 3.7 双轮执行流程（explore → cleanup → verify → cleanup）

## Publish Agent
- [ ] 4.1 Strands Agent 骨架 + AgentCore Runtime 接入
- [ ] 4.2 aws_knowledge_read tool（校准用）
- [ ] 4.3 read_s3 tool
- [ ] 4.4 write_article tool
- [ ] 4.5 quality_check tool（7 条红线）
- [ ] 4.6 git_push tool（GitHub 发布）
- [ ] 4.7 memory_search tool

## Orchestration
- [ ] 5.1 SQS Handler Lambda（Step Functions ↔ AgentCore 桥接）
- [ ] 5.2 IncrementRework Lambda
- [ ] 5.3 API Handler Lambda（HTTP API）

## Safety & IAM
- [x] 6.1 SafetyGuard（pre_execute + post_execute + check_iam_action）
- [x] 6.2 IAM Manager（Scoped Role + 动态调权 + 三层模型）
- [x] 6.3 Resource Tracker

## End-to-End Validation
- [ ] 7.1 选取 1 篇真实 What's New URL
- [ ] 7.2 端到端跑通（Research → Execute → Publish）
- [ ] 7.3 验证文章发布到 GitHub Pages
- [ ] 7.4 验证所有 AWS 资源已清理


---

## Audit Summary

**审计时间：** 2026-04-04（覆盖 2026-04-02 审计）
**审计范围：** src/ 全部 Python 源文件，基于实际 `git ls-files` + 代码审查

### 统计

| 状态 | 数量 | 比例 |
|------|------|------|
| ✅ 完整实现 | 13 | 43% |
| 🔧 部分实现（stub） | 2 | 7% |
| ❌ 未实现 | 15 | 50% |
| **总计** | **30** | — |

### 2026-04-04 审计变更说明

前次审计（2026-04-02）将多个未实现项标记为 [x]。本次基于实际代码逐项核实后修正：

**Infrastructure (1.1~1.4)：全部改为 [ ]**
- `infra/__init__.py` 存在但为空文件，无 `stack.py`、无 CDK/SAM 代码
- 无 Step Functions ASL 定义文件
- 无 AgentCore Runtime 部署代码
- 无 IAM Role CloudFormation/CDK 配置

**Research Agent (2.1~2.6)：维持 [x]，均已实现**
- `src/agents/research/agent.py` — `run_research()` 完整实现，BedrockModel + system_prompt 对齐 spec
- `src/agents/research/tools.py` — 4 个 tool 全部实现（memory_search 为 stub）

**Execute Agent (3.1~3.7)：大部分维持 [x]，3.6 改为 [ ]**
- `src/agents/execute/agent.py` — `run_execute()` 完整实现（2026-04-04 新建）
- `src/agents/execute/tools.py` — 5 个 tool 实现（2026-04-04 新建）
- `src/agents/execute/safety_guard.py` — SafetyGuard 完整实现
- 3.5 重命名：原为 `write_notes`，实际实现为 `write_execute_log`
- 3.6 `memory_create` — 未实现，execute/tools.py 中无此 tool

**Publish Agent (4.1~4.7)：全部改为 [ ]**
- `src/agents/publish/` 仅有空 `__init__.py`，无 agent.py 或 tools.py

**Orchestration (5.1~5.3)：全部改为 [ ]**
- `src/orchestrator/` 和 `src/api/` 仅有空 `__init__.py`

**Safety & IAM (6.1~6.3)：维持 [x]**
- `safety_guard.py`、`iam_manager.py`、`resource_tracker.py` 均有完整实现

### 推荐下一步优先级

1. 实现 Publish Agent（4.1~4.7）— 补齐三 Agent 链路
2. 实现 Orchestrator + API Handler（5.1~5.3）— 补齐 Lambda 入口
3. 实现 Infrastructure CDK（1.1~1.4）— 部署到 AWS
4. 跑 E2E 验证（7.1~7.4）
