# JEV 报告质量门设计

## 目标

在现有法律多模态 RAG Agent 的报告草稿与人工复核之间增加一个可选的 JEV 决策层，用于对证据支撑度、权威依据充分性、结论越界风险和人工复核必要性进行结构化判断。JEV 只负责分流与质量提示，不替代报告生成模型，也不替代人工审核。

## 范围与非目标

本阶段包含：

- 独立的 JEV HTTP 客户端与 Pydantic 请求/响应模型；
- `JEV_ENABLED=false` 默认关闭，以及 API Key、模型、超时和置信度阈值配置；
- 报告草稿生成后调用一次 JEV，批量询问质量门问题；
- 将 JEV 结果写入可观测事件，并附加到报告响应/人工复核上下文；
- JEV 超时、限流、格式错误或未配置时的安全降级；
- 单元测试、模拟 HTTP 测试和质量门回归样例；
- README、架构、任务/可观测性和面试文档更新。

本阶段不包含：

- 用 JEV 生成自然语言报告；
- 用 JEV 处理原始视频、音频或 OCR；
- 自动批准或自动发布法律报告；
- 将 JEV 伪装成 `ChatOpenAI` 兼容的普通聊天模型；
- 引入新的数据库表或强制数据库迁移；
- 修改现有 LLM、RAG、人工复核和导出流程的既有语义。

## 架构

```text
Evidence + Authority + Draft Report
                |
                v
       JevDecisionClient
                |
                v
   QualityGateAssessment（结构化结果）
                |
     +----------+----------+
     |                     |
  置信度足够            低置信度/高风险
     |                     |
  进入人工复核        强制人工复核并提示原因
```

JEV 使用官方决策接口，而不是聊天接口。客户端发送一个脱敏后的 `state` 和一组 `noul`、`choice`、`score` 问题，接收答案、概率、置信度、模型版本、请求 ID 和用量信息。模型版本必须被记录，以便 pinned/rolling 模型变更时可以解释结果差异。

质量门至少包含四个判断：

1. `grounding_ok`：报告主要事实是否能由当前证据支持；
2. `authority_sufficient`：权威依据是否足够支撑当前分析；
3. `overclaiming_risk`：报告是否包含确定性责任、胜诉或其他越界表达；
4. `needs_human_review`：是否必须由人工复核后才能继续。

另增加 `risk_level` 选择题和 `evidence_sufficiency` 评分题，作为前端提示与后续路由依据。无论 JEV 给出什么结果，法律报告仍保持 `pending` 人工复核状态。

## 数据流

1. 现有 LangGraph 完成证据检索、权威依据检索和报告草稿生成。
2. 质量门构造只包含必要上下文的脱敏状态：报告正文、引用标题/摘要、证据与依据计数、法律领域和免责声明；不发送原始文件路径、API Key 或未脱敏隐私字段。
3. JEV 客户端通过 trailing-slash endpoint 调用，使用固定的 `JEV_MODEL`，请求携带幂等键。
4. 客户端验证响应结构，保留 `model_version`、`request_id`、`answers`、`latency_ms` 和 `usage`。
5. 质量门将结果转为 `QualityGateAssessment`，并在失败时返回 `available=false`、`needs_human_review=true` 和可读的降级原因。
6. `run_report` 将质量门结果写入 `report.quality_gate_completed` 或 `report.quality_gate_unavailable` 事件，并把结果带入 `ReportResponse`。
7. Streamlit 在报告复核区域显示“JEV 质量门”摘要；只展示风险提示和置信度，不展示为法律结论。

## 配置与降级

```env
JEV_ENABLED=false
JEV_API_KEY=
JEV_BASE_URL=https://jev-ai.org/api/v1
JEV_MODEL=jev-1.13
JEV_TIMEOUT_SECONDS=5
JEV_MIN_CONFIDENCE=0.85
```

- 未启用或未配置 Key：跳过调用，继续人工复核；
- HTTP 超时、429、5xx、响应校验失败：记录脱敏错误事件，继续人工复核；
- 质量门返回低于阈值的置信度：标记为需要人工复核，不阻断报告创建；
- JEV 调用不得把 API Key 放入事件 metadata、前端响应或 LangSmith 输入；
- 默认关闭 JEV，确保本地 Demo 无需第三方账号即可运行。

## 测试策略

- 客户端测试：请求路径、认证头、固定模型、幂等键、问题类型和响应解析；
- 错误测试：超时、429、5xx、无效 JSON、缺失答案和未配置 Key；
- 质量门测试：高置信度、低置信度、越界风险和不可用降级；
- 报告流程测试：JEV 成功不改变人工复核状态，JEV 失败不影响原报告生成；
- 前端测试：质量门结果存在和不可用时均能显示可读提示；
- 全量回归：原有任务队列、RAG、导出和报告质量评估测试必须继续通过。

## 验收标准

- JEV 默认关闭时所有现有测试和本地 Demo 行为不回归；
- 开启 JEV 后，报告响应包含可审计的质量门结果；
- JEV 失败时报告仍进入人工复核，不会被自动批准；
- 不发送未脱敏的身份证号、手机号、银行卡号或原始文件路径；
- 文档能说明 JEV 的适用边界、版本固定、隐私和人工复核策略。
