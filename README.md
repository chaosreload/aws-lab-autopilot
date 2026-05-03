# aws-lab-autopilot

自动化将 AWS What's New 转成 Hands-on Lab 技术文章的流水线。基于 [Strands Agents](https://strandsagents.com/) + Amazon Bedrock，由 [openspec](./openspec/) 规格驱动。

> 这是 **Phase 2 重写版**（2026-04 开始）。Phase 1（CDK + Step Functions + SQS 架构）已归档，不再维护。

- **输入**：AWS What's New URL（HTTP `POST /tasks`）
- **输出**：一篇标准化的 Hands-on Lab 技术文章（Markdown，含实测数据、踩坑记录、IAM Policy、费用明细、清理脚本）
- **发布目的地**：[chaosreload/aws-hands-on-lab](https://github.com/chaosreload/aws-hands-on-lab)（GitHub Pages）

## 架构概览

```
POST /tasks  ─►  Research Agent  ─►  Execute Agent  ─►  Publish Agent  ─►  PR
              (spec §5 decision   (真跑 + safety    (评估 + 文章      (chaosreload/
               interception)        guard + cleanup)  生成 + S3 上传)   aws-hands-on-lab)
```

- **API**：FastAPI (`src/autopilot/api.py`)，状态存 DynamoDB（本地跑用 DynamoDB Local，prod 用真 DDB）
- **Agent 运行时**：Strands Agents + Amazon Bedrock（默认 `global.anthropic.claude-opus-4-7`）
- **规格驱动**：`openspec/specs/` 是权威；`openspec/changes/` 是提案 + 验收矩阵

## 当前进度（Stage 1 Phase 2）

| 任务 | 状态 | 说明 |
|------|------|------|
| Stage 0 — FastAPI + DDB + AWS session 工厂 | ✅ | `7d9a3c7` |
| B1 — `TestEnvironment` pydantic schema | ✅ | `36e228c` |
| B2 — Research Agent 输出 `environment` + API 透传 `aws_identity` | ✅ | `f706ef4` |
| B3 — post-parser decision interception（spec §5.1–§5.3 + §8 downgrade）| ✅ | `9c20f03` |
| B4 — TestEnvironment validation（自然跑 + 严格 V7 unit test） | ✅ | `642894a` |
| B5 — `aws_knowledge_region` filter bug 修复（service→display_name 映射）| 🔜 | 路线 A |
| Stage 1 Execute Agent 改造（V2/V3/mock server） | ⏳ | 未开始 |
| Stage 2+（Phase 3 Archie internalization 等） | ⏳ | 已有 openspec/changes 排队 |

验收证据：每个已完成 task 对应 `memory/<tag>-validation/` 目录下的 README + 证据文件。例：[`memory/b4-validation/README.md`](memory/b4-validation/README.md)。

## 本地起服务

需要 Python 3.12、[`uv`](https://docs.astral.sh/uv/)、Docker。

```bash
# 1. venv
uv venv
uv pip install -r requirements.txt

# 2. DynamoDB Local（container，-inMemory 数据不持久化）
docker run -d --name ddb-local -p 8001:8000 amazon/dynamodb-local

# 3. 建表（一次性）
AWS_ACCESS_KEY_ID=dummy AWS_SECRET_ACCESS_KEY=dummy \
  aws dynamodb create-table \
  --table-name autopilot-tasks \
  --attribute-definitions AttributeName=task_id,AttributeType=S \
  --key-schema AttributeName=task_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --endpoint-url http://localhost:8001 --region us-east-1

# 4. 起 API
export AWS_PROFILE=<你的 profile>      # 走真 Bedrock 要有 bedrock:InvokeModel 权限
.venv/bin/python -m uvicorn src.autopilot.api:app \
  --host 127.0.0.1 --port 8000 --reload
```

健康检查：`curl http://127.0.0.1:8000/healthz` → `{"status":"ok"}`

提任务：
```bash
curl -sS -X POST http://127.0.0.1:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"url":"https://aws.amazon.com/about-aws/whats-new/2026/02/structured-outputs-available-amazon-bedrock/"}'
```

轮询：`curl http://127.0.0.1:8000/tasks/<task_id>` 直到 `status=research_done`。

## 目录结构

```
src/
├── autopilot/        # FastAPI API + DDB schema + AWS session 工厂
├── agents/
│   ├── research/     # Research Agent（spec-compliant，含 post-parser §5）
│   ├── execute/      # Execute Agent（Stage 1 Phase 2 改造中）
│   └── publish/      # Publish Agent（未重构）
├── aws/              # AWS 工具层（knowledge MCP client、resource tracker）
├── common/           # 共享 pydantic models（TestEnvironment 等）
├── api/              # 旧 Lambda handler 骨架（废弃中）
└── orchestrator/     # 旧 SFN/SQS 骨架（废弃中）

openspec/
├── specs/            # 权威规格（main.md / agent-io.md / phase2-gap-analysis-v2.md）
└── changes/          # 变更提案 + 验收矩阵
    ├── phase1-mvp-implementation/
    ├── phase2-stage1-test-environment/   # 当前在做
    └── phase3-archie-internalization/

memory/               # 每个 task 的验收证据（只增不改）
└── b4-validation/    # B4 验收（示例）

run_local*.py         # 单 agent 本地手跑入口（debug 用）
templates/article.md  # Publish Agent 文章模板
```

## License

Apache 2.0。见 [LICENSE](./LICENSE)。

## 归档

Phase 1（CDK/Step Functions/SQS 架构，2026-03）实现已归档到 dev-server `/data/projects/archives/aws-lab-autopilot-phase1-2026-04-01.tar.gz`。当前仓库是全新 Phase 2 实现，与 Phase 1 无共同 git history。
