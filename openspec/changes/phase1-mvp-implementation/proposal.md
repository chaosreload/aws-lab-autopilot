# Phase 1 MVP — 端到端单篇验证

## Why
现有 DESIGN.md 已完成完整系统设计（v3.2），状态机已验证（17 states, 12 scenarios, 100% coverage）。
Phase 1 目标：将设计落地为可运行代码，完成一篇真实 What's New 的端到端验证。

## Scope
- AWS 基础设施（CDK/SAM）
- 3 个 Strands Agent 实现
- Step Functions ASL 定义
- SQS → AgentCore 桥接 Lambda
- Safety Guard + IAM Manager
- HTTP API
- 端到端测试（1 篇真实 URL）

## Out of Scope
- AgentCore Memory 集成（Phase 2）
- 批量 API（Phase 2）
- Web UI（Phase 4）
