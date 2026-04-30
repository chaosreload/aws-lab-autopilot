# Phase 2 Stage 1 — TestEnvironment Tasks

_标 [S] = small (<30min)、[M] = medium (1-2h)、[L] = large (半天以上)_

## 1. Schema 定义（P0）

- [ ] 1.1 [S] 在 `src/common/models.py` 加 `VpcPreference` enum（NONE / DEFAULT_VPC / LAB_VPC_REQUIRED）
- [ ] 1.2 [S] 加 `Prerequisite` pydantic model（type / description / params）
- [ ] 1.3 [S] 加 `CleanupPolicy` pydantic model（ttl_hours / on_failure / orphan_scan）
- [ ] 1.4 [M] 加 `TestEnvironment` pydantic model（8 字段）+ field_validator 执行 region 白名单、tag_strategy 3 个 mandatory key 校验
- [ ] 1.5 [S] `ResearchResult` 新增 `environment: Optional[TestEnvironment] = None`；verdict=go 时 Post-init 校验非空

## 2. Research Agent 输出升级（P0）

- [ ] 2.1 [M] SYSTEM_PROMPT 新增 "TEST ENVIRONMENT" 段，给出 region 决策程序、tag_strategy 固定模板、budget_limit_usd 上限表
- [ ] 2.2 [M] JSON output schema 的 go 分支加 `environment` 完整示例
- [ ] 2.3 [S] `_DEFAULTS` 加 `environment` 默认值（region=us-east-1 / account_id=空串 / tag 包含 3 key）
- [ ] 2.4 [M] `run_research` 入口接收 `aws_identity` dict，作为 prompt 变量传入（Agent 从这里取 account_id，禁止自选）
- [ ] 2.5 [M] 回归：跑 S3 Files URL（无 region 限制），断言 `environment` 8 字段全部非空且 V4~V6 通过（specs/stage1-test-environment.md 验收表）
- [ ] 2.6 [M] 回归：mock 一个 "us-west-2 only" 的测试 URL / 或用 mock server 返回受限公告，断言 V2

## 3. API 层接入（P0）

- [ ] 3.1 [S] `src/autopilot/api.py::_run_research_and_persist` 把 `task_store.get_task(task_id).get("aws_identity")` 透传给 `run_research(task_id, url, aws_identity=...)`
- [ ] 3.2 [S] `run_research` 函数签名增加 `aws_identity: dict` 参数（默认 None），None 时 Agent 必须 fallback 到 `aws_session.who_am_i()`
- [ ] 3.3 [S] `task_store.save_research_result` 把 `environment` 子字典 promote 到顶层便捷字段：`region` / `region_reason` / `budget_limit_usd` / `tag_strategy`（已做过类似 promote，扩展即可）
- [ ] 3.4 [S] `GET /tasks/{id}` 响应 model `TaskDetail` 加 `environment` 字段（Optional[dict]）

## 4. Execute Agent 消费（P1，依赖 2、3）

_Stage 1 只做"消费契约"，真实 Execute 改造在 Stage 2（AgentCore Runtime）里展开_

- [ ] 4.1 [S] `src/agents/execute/agent.py::run_execute` 入口 assert `research_result["environment"]` 存在，缺失 → raise `TestEnvironmentMissingError`
- [ ] 4.2 [M] Execute SYSTEM_PROMPT 加 "ENVIRONMENT ENFORCEMENT" 段：所有 boto3 session MUST 用 `environment.region`；所有创建调用 MUST 加 `environment.tag_strategy` tags；budget_limit_usd 超 0.9× 停止
- [ ] 4.3 [S] 在 `src/agents/execute/tools.py` 加 helper `apply_mandatory_tags(tags_dict, resource_tags_param)`，Agent 工具统一调它
- [ ] 4.4 [M] Prerequisite 验证最简实现（at least `bedrock_model_access`、`service_quota`、`bucket_exists` 3 类）；失败 → 标 `needs_human`

## 5. 决策拦截（P1）

- [ ] 5.1 [S] `_parse_json_response` 后置加 validator：若 `environment.account_id != aws_identity["Account"]`，强行修正并 log warning（保障"Agent 不能换 account"）
- [ ] 5.2 [S] 若 `environment.region` 不在 `{us-east-1, us-west-2, ap-southeast-1}` 白名单，强行 fallback 到 us-east-1 并在 `region_reason` 补注 "sanitized by post-parser"

## 6. openspec changes 归档（P2，Stage 1 完成后执行）

- [ ] 6.1 [S] 把本 change 的 specs/stage1-test-environment.md 合并到 `openspec/specs/phase2-gap-analysis-v2.md` 的相应章节，或独立保留
- [ ] 6.2 [S] 在 `openspec/changes/archive/` 下归档本 change 目录
- [ ] 6.3 [S] 在 `memory/task-archive.md` 或 `memory/YYYY-MM-DD.md` 记 Stage 1 完成快照（哪些字段落地、回归通过清单）

## 7. Stage 1 退出验收（Definition of Done）

- [ ] specs/stage1-test-environment.md §9 的 V1~V7 **全部通过**（本地 + DDB Local + weichaol-testenv2 profile）
- [ ] `curl http://localhost:8000/tasks/{task_id}` 返回的 JSON 里能看到完整 `environment` 树
- [ ] Execute Agent 未启动时 DDB 可检索 `environment.tag_strategy`（Gap 9 orphan scanner 的数据源就绪）
- [ ] 没有新增 "schema-shaping" commit 偷跑（scope 严格限制在 TestEnvironment + 直接依赖）

## 非目标（再次强调，防 Stage 1 二次 scope creep）

- [ ] ❌ 不做 multi-region 并行（Stage 3）
- [ ] ❌ 不做跨 account（将来）
- [ ] ❌ 不做 CloudWatch Budgets 动态监控（只做静态 budget_limit_usd）
- [ ] ❌ 不实现真正的 EventBridge orphan scanner（Gap 9 / Stage 3）
- [ ] ❌ 不做 Execute → AgentCore Runtime 迁移（Stage 2）
