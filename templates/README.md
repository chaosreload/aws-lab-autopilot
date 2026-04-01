# Templates — 文章模板使用说明

本目录包含 Publish Agent 生成 AWS Hands-on Lab 文章时使用的模板。

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `article.md` | 标准 Lab 实测文章模板（最常用） |
| `comparison.md` | 服务/功能对比文章模板（TODO） |
| `benchmark.md` | 性能基准测试文章模板（TODO） |

---

## 如何使用 `article.md`

Publish Agent 在写文章时，**必须以 `article.md` 为蓝图**，将 `{{ }}` 占位符替换为 Execute Agent evidence 中的实测数据。

### 数据来源映射

| 占位符 | 数据来源 | 说明 |
|--------|---------|------|
| `{{ article_title }}` | Publish Agent 自行生成 | 格式：`[功能名] 实测：[副标题]` |
| `{{ lab_difficulty }}` | Publish Agent 根据步骤复杂度判断 | ⭐ / ⭐⭐ / ⭐⭐⭐ |
| `{{ lab_duration_min }}` | Publish Agent 估算 | 整数分钟 |
| `{{ lab_cost_usd }}` | 根据 `cost_table` 计算 | 如 `< $0.10` |
| `{{ background }}` | Research Agent notes.md | 背景分析 |
| `{{ prerequisites }}` | Research Agent notes.md + Evidence | 前置依赖列表 |
| `{{ core_concepts }}` | Research Agent notes.md | 参数表格、关键概念 |
| `{{ steps }}` | Execute Agent evidence (explore + verify) | 验证通过的 CLI/Python 代码 |
| `{{ test_results }}` | Execute Agent evidence | 实测数据表格 |
| `{{ pitfalls }}` | Execute Agent evidence (stderr/exceptions) | 遇到的真实错误 |
| `{{ cost_table }}` | Execute Agent evidence (API call count) | 费用明细表 |
| `{{ actual_cost_usd }}` | 计算得出 | 费用合计 |
| `{{ cleanup_commands }}` | Execute Agent evidence (resources created) | 清理命令 |
| `{{ conclusion }}` | Publish Agent 综合分析 | 基于测试数据的建议 |
| `{{ references }}` | Research Agent (aws_knowledge_read URLs) | 参考链接 |
| `{{ iam_policy_json }}` | Research Agent (iam_policy) | 最小权限 Policy JSON |

---

## Publish Agent 硬性约束（不得违反）

以下规则写在 `article.md` 的 `{# #}` 注释中，Publish Agent 必须严格执行：

### 1. Lab 信息框 — MANDATORY
每篇文章开头必须有难度、时间、费用三行，缺一不可。

### 2. CLI 优先
动手实践的代码示例：先给 `aws` CLI 命令，再给 Python boto3 代码。读者应该能只用 CLI 就完成实验。

### 3. "发现:" 摘要 — MANDATORY
每个测试结果小节必须以 `**发现:**` 行结束，提炼 1-2 条数据洞察。不允许只有表格没有结论。

### 4. 数值来自实测 — 绝对禁止伪造
- 所有延迟、相似度分数、向量维度等数值必须来自 Execute Agent evidence
- 禁止写 `~XXX ms`、`约 X`、`0.xxx` 等占位符或估算值
- 代码示例中的向量值（如 `[-0.032, 0.066, ...]`）必须来自 evidence 里的真实浮点数

### 5. 踩坑来自 Evidence — 禁止捏造
踩坑记录必须对应 Execute Agent evidence 里实际出现的异常（stderr/exception 日志）。不允许添加"可能会遇到"的假设性踩坑。

### 6. 费用基于实际调用次数
费用必须根据 evidence 里实际执行的 API 调用次数计算，不能写"约 $X"。

### 7. 文章长度控制
目标 2500-5000 字（中文）。不要把每个参数都详细解释，聚焦在实测发现上。

---

## 最终检查清单

Publish Agent 在调用 `quality_check` 之前，对照此清单：

- [ ] Lab 信息框存在（难度/时间/费用）
- [ ] 至少一步有 CLI 命令（不全是 Python）
- [ ] 每个测试结果小节都有 `**发现:**`
- [ ] 没有任何 `{{ }}` 未替换的占位符
- [ ] 没有 `~XXX ms`、`约 X` 等估算数值
- [ ] 踩坑条数 ≤ Execute Agent evidence 中出现的真实错误数
- [ ] 费用合计与 API 调用次数吻合
- [ ] 代码可以直接复制运行（无伪代码）
- [ ] 参考链接包含 What's New 公告 URL、官方文档、定价页
