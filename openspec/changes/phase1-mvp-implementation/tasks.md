# Phase 1 MVP Tasks

## Infrastructure
- [x] 1.1 SAM/CDK 模板：API Gateway + Lambda + DynamoDB + S3 + SQS
- [x] 1.2 Step Functions ASL 定义（基于已验证的 17-state 状态机）
- [x] 1.3 AgentCore Runtime 部署（3 个 agent containers）
- [x] 1.4 IAM Role 和 Permission 配置

## Research Agent
- [x] 2.1 Strands Agent 骨架 + AgentCore Runtime 接入
- [x] 2.2 aws_knowledge_read tool
- [x] 2.3 aws_knowledge_region tool
- [x] 2.4 write_notes tool（S3）
- [x] 2.5 memory_search tool（AgentCore Memory）
- [x] 2.6 Research Agent 提示词调优

## Execute Agent
- [x] 3.1 Strands Agent 骨架 + Shell Command API 接入
- [x] 3.2 aws_cli_execute tool（含 Safety Guard pre/post）
- [x] 3.3 iam_add_permission tool（动态调权）
- [x] 3.4 track_resource + cleanup_resource tools
- [x] 3.5 write_notes tool（S3 测试日志）
- [x] 3.6 memory_create tool（踩坑记录）
- [x] 3.7 双轮执行流程（explore → cleanup → verify → cleanup）

## Publish Agent
- [x] 4.1 Strands Agent 骨架 + AgentCore Runtime 接入
- [x] 4.2 aws_knowledge_read tool（校准用）
- [x] 4.3 read_s3 tool
- [x] 4.4 write_article tool
- [x] 4.5 quality_check tool（7 条红线）
- [x] 4.6 git_push tool（GitHub 发布）
- [x] 4.7 memory_search tool

## Orchestration
- [x] 5.1 SQS Handler Lambda（Step Functions ↔ AgentCore 桥接）
- [x] 5.2 IncrementRework Lambda
- [x] 5.3 API Handler Lambda（HTTP API）

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

**审计时间：** 2026-04-02
**审计范围：** src/ 全部 Python 源文件 + infra/stack.py + sfn_definition.json

### 统计

| 状态 | 数量 | 比例 |
|------|------|------|
| ✅ 完整实现 | 22 | 73% |
| 🔧 部分实现（stub / 架构偏离） | 4 | 13% |
| ❌ 未实现 | 4（全为 E2E 验证） | 13% |
| **总计** | **30** | — |

### 🔧 部分实现说明（4 项）

1. **1.3 AgentCore Runtime** — 只有 Execute Agent 真正接入 AgentCore（EXECUTE_AGENT_ARN 已硬编码），Research 和 Publish 直接在 Lambda 内运行，RESEARCH/PUBLISH_AGENT_ARN 为空
2. **2.5 / 4.7 memory_search** — stub，返回空结果，注释说明 Phase 2 接 AgentCore Memory，Phase 1 不阻塞
3. **3.6 memory_create** — stub，只打 log，不持久化，Phase 1 不阻塞
4. **4.6 git_push 使用方式** — 工具实现完整，但 Publish Agent system_prompt 明确 "不调用 git_push"，改为人工 approve 触发（POST /tasks/{id}/approve）

### 🚨 关键 Gap（阻塞 E2E 跑通）

**P0 — 立刻需要解决：**

1. **CDK 未部署** — DynamoDB / S3 / SQS / Lambda / Step Functions 都还没有部署到 AWS 账户，是所有 E2E 测试的前提条件
2. **PUBLISH_PIPELINE_ARN 未配置** — stack.py 里 sqs_handler 没有注入此变量，publish 阶段会抛 `ValueError: PUBLISH_PIPELINE_ARN environment variable not set`；需要在 stack.py 中把 sfn_publish_pipeline.json 对应的 State Machine ARN 注入进去
3. **sfn_publish_pipeline.json 的 State Machine 未创建** — 文件存在但未在 CDK stack 中定义，需要补 CDK 代码或手动创建

**P1 — 部署后需要配置：**

4. **GitHub Secret 未创建** — Secrets Manager 中需要手动创建 secret（GITHUB_TOKEN / GITHUB_REPO / GITHUB_BRANCH），否则 approve 接口报错
5. **Knowledge MCP 可达性** — Lambda 需要能访问 `knowledge-mcp.global.api.aws`（公网出口或 VPC endpoint），未在 stack.py 中配置网络

**P2 — 后续优化：**

6. **memory_search / memory_create stubs** — Phase 2 接 AgentCore Memory，不影响 Phase 1 E2E

### 推荐下一步优先级

1. `cdk deploy` 先把基础设施跑起来（P0）
2. 补 stack.py 中的 Publish Pipeline State Machine 定义（P0）
3. 手动创建 GitHub Secret（P1）
4. 选一篇 What's New URL 跑 E2E（7.1 → 7.4）
