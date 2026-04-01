{#
  AWS Hands-on Lab 文章模板 v1.0
  =====================================================
  本模板由 Publish Agent 使用，根据 Execute Agent 的实测证据填充内容。

  使用规则（MANDATORY — 不得违反）：
  1. Lab 信息框必须存在，难度/时间/费用都要填
  2. 动手实践 Steps 必须先给 AWS CLI 命令，再给 Python（如果适用）
  3. 每个测试结果小节必须以 "**发现:**" 结尾，提炼数据洞察
  4. 踩坑记录只能来自 Execute Agent 的 evidence（stdout/stderr 日志），禁止凭空捏造
  5. 费用必须基于 Execute Agent 实际执行的 API 调用次数计算
  6. 所有数值（延迟、相似度、向量维度等）必须是 Execute Agent 跑出来的实际测量值
     - 禁止写 "~XXX ms"、"约 X 秒"、"0.xxx" 等占位符
     - 向量示例值也必须来自 evidence 里的真实浮点数
  7. 文章长度控制在 2500-5000 字（不要写成论文）
  8. 代码块中的示例输出必须来自 evidence，不能是示意性伪代码
#}

# {{ article_title }}

{# article_title 格式："[功能/服务名] 实测：[副标题]"
   例如："Amazon Nova Multimodal Embeddings 实测：首个统一多模态 Embedding 模型"
   例如："Amazon S3 Express One Zone 对象标签实测：目录桶 ABAC 权限控制"
#}

## Lab 信息

{# 此框为 MANDATORY，任何情况都不得省略 #}

- 难度: {{ lab_difficulty }}
- 预估时间: {{ lab_duration_min }} 分钟
- 预估费用: {{ lab_cost_usd }}

{# lab_difficulty: ⭐ 入门 / ⭐⭐ 中级 / ⭐⭐⭐ 高级
   lab_duration_min: 整数，例如 30
   lab_cost_usd: 字符串，例如 "< $0.10" 或 "约 $0.50"，基于 cost_table 计算
#}

{{ background }}

{# background: 1-2 段背景介绍
   - 说明该功能解决什么问题（旧方案的痛点）
   - 解释新功能的核心价值（一句话）
   - 不超过 150 字
   - 结尾不需要"让我们开始吧"之类的废话
#}

## 前置条件

{{ prerequisites }}

{# prerequisites 格式（Markdown 无序列表）：
   - AWS 账号，开通 [具体服务] 的访问权限
   - AWS CLI v2 已配置（~/.aws/credentials 或 IAM Role）
   - Python 3.8+ 及 boto3（`pip install boto3`）
   - [其他依赖，如 Pillow、numpy 等，如不需要则省略]
   示例：
   - AWS 账号，开通 Amazon Bedrock 中 Nova Multimodal Embeddings 模型访问权限
   - AWS CLI v2 已配置
   - Python 3.8+ + boto3
#}

## 核心概念

{{ core_concepts }}

{# core_concepts：
   - 必须包含至少一个参数对照表（Markdown 表格）
   - 列出本功能的关键参数、支持的枚举值、限制条件
   - 如有多个相关概念，分小节（### 三级标题）
   - 表格后加一两句说明最重要的使用规则
   示例结构：
   ### 参数一览
   | 参数 | 说明 | 可选值 |
   ...
   ### [关键概念名称]（如 embeddingPurpose 解析）
   ...
#}

## 动手实践

{# 步骤数量：通常 3-5 步（根据测试矩阵 T1-TN 决定）
   每步结构：### Step N: [步骤名称]
   - 先给 CLI 命令（aws bedrock-runtime invoke-model 等）
   - 再给等价的 Python 代码
   - 代码必须能直接复制运行（完整、可执行）
   - 代码中的示例输出必须来自 Execute Agent 的实测结果（evidence 里的 stdout）
   - 代码块使用 ```bash 或 ```python 标注
#}

{{ steps }}

{# steps 内容来源：
   - 根据 Research Agent 的 test_matrix 展开
   - 每个测试用例对应一个 Step
   - Step 代码使用 Execute Agent evidence 中经过验证的调用方式
   - 如果 Execute Agent 在某步遇到了参数调整，在 Step 里用注释说明正确用法
#}

## 测试结果

{{ test_results }}

{# test_results 格式要求：
   - 必须包含至少一张数据对比表（Markdown 表格）
   - 表格后紧跟 "**发现:**" 一行，提炼 1-2 条数据洞察（MANDATORY）
   - 数值来自 Execute Agent evidence，精确到小数点后 2-4 位
   - 示例：

   ### 维度性能对比

   | 维度 | HTTP 状态码 | 实际向量长度 | 测试延迟 |
   |------|------------|------------|---------|
   | 256  | 200 | 256  | 222.01 ms |
   | 1024 | 200 | 1024 | 209.26 ms |

   **发现:** 256 维延迟与 1024 维接近（差异 < 10%），但存储成本降低 75%，适合原型阶段。

   - 如有多组测试结果，分多个 ### 小节，每节都要有表格和"发现"
#}

## 踩坑记录

{{ pitfalls }}

{# pitfalls 格式：有序列表，每条结构如下
   
   1. **[坑的标题]**

      [现象描述 + 错误信息（如有，引用 evidence 中的 stderr/exception）]

      [根本原因]

      [修复方式 / 正确做法]

   约束：
   - 踩坑内容必须来自 Execute Agent evidence（stdout/stderr）
   - 只记录真实遇到的坑，不补充"可能会遇到"的假设性内容
   - 每条踩坑要有具体的错误信息或异常类型（如果有）
   - 如果 Execute Agent 没有遇到任何坑，此节可写"本次测试未遇到意外错误。"
#}

## 费用明细

{{ cost_table }}

{# cost_table 格式：

   | 资源 | 说明 | 费用 |
   |------|------|------|
   | [服务名] invoke-model | [N] 次 API 调用 | < $X.XX |
   | ... | ... | ... |

   合计：{{ actual_cost_usd }}

   约束：
   - 费用基于 Execute Agent 实际执行的 API 调用次数（在 evidence 中可数）
   - 如果费用极小（< $0.01），写"< $0.01（可忽略）"
   - 引用官方定价页面（https://aws.amazon.com/bedrock/pricing/ 等）
#}

## 清理资源

{{ cleanup_commands }}

{# cleanup_commands：
   - 如果 Execute Agent 创建了 AWS 资源（Lambda、DynamoDB、S3 bucket 等），
     提供 AWS CLI 删除命令
   - 如果只调用了托管 API（如 Bedrock InvokeModel），无需清理，写：
     "本 Lab 仅调用托管 API，**无需删除任何 AWS 资源**。"
   - 如果有本地临时文件，提供 rm 命令
#}

## 结论与建议

{{ conclusion }}

{# conclusion 结构：

   ### 适合场景
   [3-5 条无序列表，具体说明哪类业务场景适合使用该功能]

   ### [配置/参数选择建议标题]（例如 "维度选型建议" / "模型选型建议"）
   | 场景 | 推荐配置 | 理由 |
   |------|----------|------|
   ...

   ### 生产环境注意
   [3-5 条需要注意的生产级事项，基于测试中发现的限制]

   约束：
   - 选型建议表格的"理由"列必须引用测试数据（如"延迟 X ms，存储 Y 维"）
   - 不要写空泛的"适合企业级应用"之类没有信息量的句子
#}

## 参考链接

{{ references }}

{# references 格式：

   - [标题](URL)
   
   必须包含：
   - 官方 What's New 公告（来自 task URL）
   - 官方文档（来自 Research Agent 查询的文档）
   - AWS Blog 文章（如果有）
   - 定价页面
   
   可选：
   - GitHub 示例代码
   - re:Invent / re:Inforce 演讲录像
#}

---

{# === 附录：IAM Policy（机器可读，不展示在正文中） ===

最小权限 IAM Policy（Research Agent 推导，Execute Agent 验证可运行）：

```json
{{ iam_policy_json }}
```

注意：此 Policy 已在 Execute Agent 双轮测试中验证（T1–TN 全部测试使用此权限成功执行）。
#}
