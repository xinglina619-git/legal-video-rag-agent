# 生产级可观测性与评估说明

## 1. 为什么要做可观测性

法律多模态 RAG Agent 的生产问题通常不是“代码能不能跑”，而是：

- 哪一步慢：OCR、转写、索引、检索还是模型生成？
- 哪一步失败：材料解析、向量库、模型接口还是导出？
- 检索为什么没召回关键证据？
- 报告为什么出现越界表达？
- 人工驳回后，如何把失败样本加入回归评估？

因此项目新增了内部 observability hook、数据库审计事件、可选 LangSmith tracing 和报告质量评估。

## 2. 内部审计事件

系统会把关键流程记录到 `app_events` 表。典型事件包括：

| 事件 | 触发时机 |
|---|---|
| `case.created` | 创建案件 |
| `intake.answered` | 用户完成一轮案情问答 |
| `material.normalized` | 材料完成文本解析、OCR 或转写 |
| `material.indexed` | 材料写入检索库 |
| `retrieval.completed` | 证据库或权威依据库检索完成 |
| `report.started` | 报告任务开始 |
| `report.generated` | 报告草稿生成 |
| `review.approved` | 人工确认报告 |
| `review.rejected` | 人工驳回报告 |
| `operation.failed` | 可归类的失败事件 |

事件元数据会先调用脱敏逻辑，避免手机号、身份证号和银行卡号进入审计表。

## 3. LangSmith 可选接入

LangSmith 用来观察 Agent/LLM 调用链路、耗时、失败 trace 和后续评估实验。它默认关闭，避免真实案件材料被意外发送到第三方平台。

在 `.env.local` 中显式开启：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的 LangSmith API Key
LANGSMITH_PROJECT=legal-video-rag-agent
LANGSMITH_SAMPLE_RATE=0.1
LANGSMITH_RECORD_INPUTS=false
```

说明：

- `LANGSMITH_TRACING=false` 是默认值；
- `LANGSMITH_RECORD_INPUTS=false` 表示默认不鼓励记录完整输入原文；
- `LANGSMITH_SAMPLE_RATE` 用于表达生产采样策略，当前本地 hook 先记录配置，后续可扩展为真实采样；
- 真实案件材料接入 LangSmith 前应确认授权和脱敏策略。

## 4. 报告质量回归评估

检索评估关注“召回是否命中”，报告质量评估关注“生成内容是否合规”。

运行：

```powershell
.\.venv\Scripts\python.exe scripts\eval_report_quality.py data\demo\loan_case\report_quality_cases.jsonl
```

当前检查规则：

1. 必须包含免责声明；
2. 必须包含案情摘要；
3. 必须包含事件时间线；
4. 必须包含核心争议点；
5. 必须包含仍需补充或核实的信息；
6. 必须出现 `[证据N]`；
7. 必须出现 `[依据N]`；
8. 不得出现“胜诉率”“一定胜诉”“必然败诉”“法院一定支持”等越界表达。

## 5. 如何排查一次失败报告

假设用户反馈“报告没有引用关键转账记录”，排查顺序如下：

1. 查看 `app_events` 中该报告的 `report.started`，确认任务 ID 和案件 ID；
2. 查看同一流程的 `retrieval.completed` 事件，确认 Evidence Collection 的 `result_count`；
3. 如果 `result_count=0`，检查材料是否出现 `material.indexed`；
4. 如果材料已索引但未召回，运行 `scripts/eval_retrieval.py` 增加相关 golden question；
5. 如果检索命中但报告没引用，运行 `scripts/eval_report_quality.py` 检查引用规则；
6. 如果开启了 LangSmith，可进入对应 project 查看 LLM 调用 trace；
7. 将失败样本追加到 JSONL 评估集，作为后续回归测试。

## 6. 面试讲法

可以这样说：

> 我没有只做“能生成报告”的 Demo，而是补了生产级可观测性。系统内部有统一 observability hook，会记录材料处理、检索、报告生成和人工复核事件；LangSmith 作为可选 trace 后端，默认关闭并支持脱敏/采样策略；同时有检索评估和报告质量回归评估，方便把失败样本沉淀为测试集。

