# Phase 2 — Design Gap Spec v2（#114 教训内化）

_基于 openclaw-solutions 执行 #114（Aurora Serverless v2 PV4 scale-to-zero 实测）任务的深度复盘_
_任务周期：2026-04-23 ~ 2026-04-27（计划 1-2 天 → 实际 4 天）_
_参考文件：`~/.openclaw/workspace-solutions/learning/2026-04-23-aurora-serverless-v2-smarter-scaling.md`（930 行笔记）_
_飞书复盘文档：https://feishu.cn/docx/Mz79dQDkSox9kDxWG4EcHGe6ngg_

---

## 概述

phase2-gap-analysis.md (v1) 聚焦于 **Agent prompt 层面** 的规范缺失（Research 设计/Execute 执行/Publish 证据），
本 v2 聚焦于更根本的 **Agent 机制 × 长跑工作流的架构错配**。

v1 假设：三个 Agent 跑在 Step Functions 编排下，整体流程是"可控的 turn-based 自动化"。
v2 发现：**技术文章任务的场景特性与 turn-based Agent 机制存在 3 处根本错配**，仅靠调 prompt 无法消除。

本 spec 定义在 Phase 2 实施过程中必须同时解决的 **架构级 Gap（8-11）**，以消除 #114 事故中暴露的四类问题：
1. 资源泄漏（EC2 空转 17h + 3h10min，$2.89 + $0.54）
2. Sub-agent 失控（fire-and-forget 后无 heartbeat、无 announce、无恢复）
3. 测试黑盒（pgbench 参数/曲线丢失，无 evidence 记录）
4. 发布口吻审查（内部流程语泄漏进外部文章，3 轮打回）

---

## 核心判断：三处场景错配

来自 weichao 2026-04-28 17:48 UTC 的判断（原文）：

> 1. agent 有超时，但做测试的任务是无法预计时间的，比如压测或者模型微调等任务可能需要持续几个小时；
> 2. 一个技术文章任务中可能有多个测试任务，这些测试任务可能必须串行，也可能没有相互依赖可以并行；
> 3. 技术文章任务有很多步骤，openclaw 的 agent 无法专门用作监控整个工作流，因为还需要监听 slack 的消息来做其他任务。

这三点确认了 aws-lab-autopilot 的定位：**不取代 Archie（openclaw-solutions），是 Archie 的长跑武器**。
Archie 继续负责用户交互 / 触发 / 状态查询 / 复盘写作；autopilot 负责长跑工作流执行 + 主动状态回推。

---

## Gap 8：Sub-agent 生命周期可观测性

### 问题（来自 #114）

- **04-23 16:37 ~ 04-24 09:57**：Sub-agent #1 偷偷创建 Aurora 集群（engine 选错、MinACU 错设），没写笔记，没 announce，17 小时后主 session 接手才发现。
- **04-24 12:32 ~ 04-25 05:35**：Sub-agent #2 启动 c6i.xlarge bench 后失联 19 小时，机器空转烧 $2.89，集群被它自己删除但 bench 遗留。
- **04-26 07:34 ~ 10:45**：主 session long-running poll 卡 3h10min，bench 再空转 $0.54。

根本原因：OpenClaw `sessions_spawn` 是 fire-and-forget，announce 是 best-effort。Sub-agent 秒退 / gateway 重启 / 模型 runtime bug 均可导致 announce 丢失，main 无从知晓。

### Requirement: Sub-agent Must Emit Heartbeat Events

每个长跑 Agent（尤其 Execute Agent）MUST 每 N 分钟（默认 5 分钟）向 DynamoDB `tasks` 表写一条 heartbeat：

```json
{
  "task_id": "uuid",
  "agent": "execute",
  "heartbeat_at": "2026-04-28T17:30:00Z",
  "phase": "infra_setup | test_execution | cleanup",
  "current_step": "T3.2a resume sample 1",
  "last_evidence_path": "s3://.../evidence/T3.2a.json"
}
```

Heartbeat 丢失超过 3 × 间隔 → EventBridge Rule 触发 `task-stuck-alarm`，自动：
1. 写 `status=stuck` 到 DynamoDB
2. 发送 Slack webhook 通知（告警频道）
3. 保留现场，不自动清理（等人介入）

### Requirement: Step Functions State Must Declare HeartbeatSeconds

所有调用 Agent 的 Step Functions state MUST 设置 `HeartbeatSeconds`：

```json
{
  "Type": "Task",
  "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
  "HeartbeatSeconds": 600,
  "TimeoutSeconds": 7200,
  "Retry": [{"ErrorEquals": ["States.Timeout"], "MaxAttempts": 0}],
  "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "CleanupOnFailure"}]
}
```

`TimeoutSeconds` 按任务类型分档：
- api_call: 900s（15 min）
- infrastructure: 7200s（2h）
- long_running: 14400s（4h，压测/微调类）

### Requirement: Agent Must Support Resume-from-Checkpoint

Agent 进程被强杀后，下一次启动 MUST 能从 DynamoDB `tasks.current_step` + S3 evidence 恢复，而不是从头开始。
具体：
- Agent 启动时先 `GetItem` 读 `current_step`
- 按 `test_matrix` 索引定位到该 step，跳过已完成项
- `resources.md` 中已创建资源直接复用（不重建）

---

## Gap 9：AWS 资源 TTL 与兜底清理

### 问题（来自 #114 + 历史事故）

- #112 Batch B：4 台 c6i.24xlarge 空转 6 天，$1300+
- #114 Sub-agent #2：c6i.xlarge 空转 17h，$2.89
- #114 主 session 卡：c6i.xlarge 空转 3h10min，$0.54

共性：launch 脚本无 TTL、无 trap、无外部看门狗。一旦 Agent 失联，资源无人回收。

SOUL.md 的 "Lessons Learned 2026-04-24" 已经明确要求：launch 脚本内置 TTL + trap + EventBridge 兜底。但这是"人类 agent"的规范，aws-lab-autopilot 必须在基础设施层面强制执行。

### Requirement: All Compute Resources Must Have AutoTerminate Tag

Execute Agent 创建任何 EC2/ECS/SageMaker 任务时 MUST 打以下 Tag：

| Tag Key | Value | 用途 |
|---|---|---|
| `autopilot:task_id` | {task_id} | 任务归属 |
| `autopilot:autoterminate` | `true` | 启用兜底清理 |
| `autopilot:max_age_hours` | `8`（默认） / 任务自定义 | 最大存活时间 |
| `autopilot:created_at` | ISO8601 UTC | 创建时间戳 |

### Requirement: EventBridge Orphan Resource Scanner

系统 MUST 部署以下 EventBridge + Lambda 基础设施：

1. **Scheduled Rule**：每 15 分钟触发 `orphan-scanner` Lambda
2. **Scanner 逻辑**：
   - 枚举所有带 `autopilot:autoterminate=true` 的 EC2 / ECS Task / SageMaker Job
   - 计算 `now - created_at > max_age_hours` 的资源
   - 终止这些资源（EC2 terminate / ECS StopTask / SageMaker StopTrainingJob）
   - 写入 `s3://{bucket}/orphan-cleanup-log/{date}.jsonl`
   - 发送 Slack webhook 通知

3. **Task 完成事件钩子**：Step Functions 状态机进入终态（Succeeded/Failed/Aborted）时 MUST 调用 `cleanup-by-task-id` Lambda，强制清理所有 `autopilot:task_id=<id>` 资源并 assertion。

### Requirement: Cleanup Must Be Idempotent and Verified

清理操作 MUST：
- 幂等（重复调用安全）
- 终态验证（不是发了 terminate 就完事，要轮询到 `terminated` 状态）
- VPC 资源必须等 ENI 清理（参考 v1 Gap 7 的 ENI cleanup check）
- 清理失败时写入 `s3://.../cleanup-failures/{task_id}.json`，告警人工介入

---

## Gap 10：Step Functions DAG 编排与并行执行

### 问题（来自 #114）

#114 测试矩阵实际有约 10 项（T1-T10），大致依赖关系：
- T1（PV=4 验证）→ 独立
- T3.1（pgbench init）→ 后续所有 T3.x 的前置
- T3.2a / T3.2b / T3.2c（不同 paused 时长的 resume 采样）→ **相互独立，可并行**
- T3.2d-cold / T3.2d-mid → 可并行
- T3.3（稳态压测）→ 独立
- T3.4（scale-down）→ 依赖 T3.3 结束

但 openclaw-solutions 手撸方式是**纯串行执行**，每个采样点都要等前一个结束，总时长拉长 3-5 倍。
且依赖全靠人和 prompt 描述，容易漏。

### Requirement: Research Agent MUST Output DAG Dependencies

Research Agent 输出的 test_matrix 每项 MUST 显式声明：

```json
{
  "id": "T3.2a",
  "depends_on": ["T3.1_pgbench_init"],
  "parallel_group": "resume-samples",
  "shared_resources": ["aurora-cluster-1"],
  "exclusive_use": true
}
```

- `depends_on`：前置 test id 列表，空表示独立可先跑
- `parallel_group`：同 group 内多项可并行，需检查 `shared_resources` 冲突
- `shared_resources`：读/写的资源 ID
- `exclusive_use=true`：该测试独占 shared_resources，同 group 其他项必须等

### Requirement: Step Functions Must Use Map + Parallel States

Execute 阶段 MUST 通过 Step Functions 的 `Map` / `Parallel` state 显式表达 DAG：

- 独立测试项 → `Parallel` branches
- 同 `parallel_group` 且无资源冲突 → `Map` with `MaxConcurrency`
- 依赖链 → 顺序 `Next` 连接
- 失败策略 → 每 branch 独立 `Catch` + `Retry`

Main orchestrator 的默认 `MaxConcurrency` 上限 = `min(5, test_matrix独立项数)`，避免 AWS API 限流。

### Requirement: Shared Resource Lock

对于同 `shared_resources` 的并行测试，系统 MUST 使用 DynamoDB conditional put 作为锁：

```python
# Pseudo
lock_acquired = dynamodb.put_item(
    Key={"resource": "aurora-cluster-1"},
    ConditionExpression="attribute_not_exists(lock_holder)",
    Item={"resource": "aurora-cluster-1", "lock_holder": test_id, "acquired_at": now}
)
```

锁超时自动释放（TTL），避免 stuck test 阻塞全局。

---

## Gap 11：Archie ↔ autopilot 异步通信协议

### 问题

- Archie 是 Slack 交互入口，不能被长跑阻塞
- 如果 Archie 主动 poll autopilot 状态 → 消耗 Archie session 资源，且 poll 粒度粗
- 如果 autopilot 只写 S3 / DynamoDB，Archie 不知道何时回读 → 用户体验差

### Requirement: Autopilot Must Push Key Events to Slack

autopilot 在以下关键节点 MUST 主动 push 事件到 Slack webhook：

| 事件 | 触发时机 | Slack 消息格式 |
|---|---|---|
| `task.accepted` | Step Functions 启动 | `🟢 #{task_id} 任务接收 | {url}` |
| `research.verdict` | Research Agent 完成 | `📋 #{task_id} Research: {verdict} | complexity={S/M/L} | 预计 {min}min` |
| `research.no_go` | verdict=skip | `⏭️ #{task_id} 跳过 | 理由: {reason}` |
| `execute.started` | Execute Agent 进入 infra_setup | `🔨 #{task_id} 开始执行 | {test_matrix_summary}` |
| `execute.progress` | 每完成一个 test_item | `✅ T{id} passed` 或 `❌ T{id} failed: {error}` |
| `execute.stuck` | Heartbeat 超时 | `⚠️ #{task_id} STUCK at {step} | 请人工介入` |
| `publish.quality_pending` | 质量检查未通过 | `🔍 #{task_id} 质量检查: {failed_rules}` |
| `publish.success` | 文章发布成功 | `🎉 #{task_id} 完成 | {article_url}` |
| `task.failed` | 终态失败 | `🔴 #{task_id} 失败 | {error} | cleanup={status}` |

所有消息 MUST 带 `thread_ts`（任务触发时的 Slack 消息 ts），归到同一 thread。

### Requirement: Archie Must Provide Status Query Command

Archie 的 `aws-article` SKILL.md 补充命令约定：

```
查询 autopilot 任务 → GET https://{api-gw}/tasks/{task_id}
返回: {status, current_step, progress_pct, cost_so_far, last_heartbeat, evidence_count}
```

Archie 收到用户"任务 X 进展如何"时，直接查 API 而非读 laerning/ 笔记。

### Requirement: Slack Webhook Must Be Idempotent

所有 push 事件 MUST 带 `event_id`（基于 task_id + event_type + state transition 计算），
Slack 端通过 `event_id` 去重，防止 EventBridge 重试造成多条消息。

---

## 实施优先级（v2 新增）

在 v1 Phase 2a-2d 基础上插入：

### Phase 2a+（与 AgentCore Runtime 接入同步）
- Gap 8：Sub-agent heartbeat + resume-from-checkpoint
- Gap 9：AutoTerminate tag + orphan-scanner Lambda

**理由**：这两项是**安全基础设施**，必须在第一个真实长跑任务跑起来之前就位。否则又会出 #114 事故。

### Phase 2b+（与基础设施生命周期同步）
- Gap 10：DAG 依赖 + Parallel/Map 编排

**理由**：DAG 编排是 Execute 生命周期的上层抽象，同时实施更省事。

### Phase 2e（新增阶段）
- Gap 11：Archie ↔ autopilot Slack webhook 协议

**理由**：独立于核心执行，Phase 2 末尾补上即可。

---

## 与 v1 Gap 1-7 的关系

| v1 Gap | v2 关联 |
|---|---|
| Gap 1（Execute Lambda 900s 不够）| Gap 8 heartbeat + resume 的前置 |
| Gap 2（Infra 生命周期）| Gap 9 tag 清理 + Gap 10 DAG 的基础 |
| Gap 3（错误处理路径切换）| Gap 10 `Retry` 策略承担部分；prompt 层面仍需 v1 Gap 3 |
| Gap 4（Test matrix 含验证）| Gap 10 DAG 依赖字段是 Gap 4 的超集 |
| Gap 5（Publish evidence 严格）| 不变 |
| Gap 6（Task type 分类）| Gap 8 HeartbeatSeconds 分档按 task_type |
| Gap 7（Agent SOP 规范）| 不变，继续 prompt 层面落实 |

v1 的 Gap 都仍然有效。v2 是在 v1 基础上 **补架构层**，不是取代。

---

## 验收标准

v2 落地后，下次遇到类似 #114 的任务 MUST 满足：

- [ ] Sub-agent 失联 ≤ 15 分钟被检测（heartbeat 3 次丢失）
- [ ] 资源超期自动终止 ≤ max_age_hours + 15 分钟
- [ ] 相互独立的 resume 采样 Parallel 执行，总时长缩短 ≥ 50%
- [ ] Slack thread 中能看到任务每个关键节点（不依赖用户追问"进展如何"）
- [ ] Agent 进程被强杀后，下次重启能从 `current_step` 恢复
- [ ] 任何任务结束（无论成败）后 15 分钟内，AWS 账户无该任务的 running 资源
