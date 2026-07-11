# Phase 2：生产级可观测性与评估增强设计

## 1. 背景与目标

当前项目已经完成法律多模态 RAG Agent 的核心闭环：案情问答、多模态材料归一化、证据/权威依据双检索、LangGraph 报告生成、人工复核、PDF/Word 导出和基础评估脚本。

Phase 2 的目标不是继续堆 UI 功能，而是把项目往生产级 AI 应用推进：让系统能够回答“这次报告为什么这样生成、哪一步慢、哪一步失败、检索有没有命中、模型输出是否越界、失败样本如何回归评估”。

本阶段聚焦五个能力：

1. 内部 observability hook；
2. 审计事件表；
3. LangSmith 可选接入；
4. trace 脱敏与采样；
5. 报告质量回归评估。

## 2. 设计原则

### 2.1 先有内部 hook，再接外部平台

LangSmith 很适合做 LLM/Agent trace、调试和评估，但项目不能直接把业务代码和某一个外部平台强绑定。系统先定义统一的内部事件接口，再把 LangSmith 作为可选后端接入。

目标结构：

```text
业务流程
  ↓
app.services.observability
  ├─ 数据库审计事件 AppEvent
  ├─ 本地结构化日志
  └─ LangSmith trace，可选开启
```

### 2.2 默认安全，显式开启

法律纠纷材料可能包含身份证号、手机号、银行卡号、住址、聊天记录和转账信息。生产级可观测性不能以牺牲隐私为代价。

默认策略：

- LangSmith tracing 默认关闭；
- trace 进入外部平台前必须经过脱敏；
- 支持采样率；
- 支持只记录 metadata，不记录原文；
- 文档明确提示真实案件材料不要默认发送到第三方平台。

### 2.3 能被测试和演示

每个新增能力都要能被自动化测试覆盖，也要能在面试演示中讲清楚。

本阶段至少提供：

- observability 单元测试；
- Agent 流程事件测试；
- 报告质量评估脚本测试；
- 文档中的“如何排查一次失败报告”示例。

## 3. 方案取舍

### 方案 A：只接 LangSmith

优点：接入快，能快速看到 trace。

缺点：业务代码容易和外部平台耦合；真实法律数据的隐私边界不够清晰；如果没有 LangSmith API Key，本地生产化经验会打折。

结论：不采用。

### 方案 B：内部 hook + 数据库审计事件 + LangSmith 可选后端

优点：更接近生产架构；离线也能记录事件；未来可以接 OpenTelemetry、Prometheus、ELK 或自建后台；隐私和采样策略可控。

缺点：实现稍多，需要设计事件模型。

结论：采用。

### 方案 C：直接上完整监控体系

包括 Celery、Redis、Prometheus、Grafana、OpenTelemetry、集中日志、多租户审计等。

优点：最接近真实生产。

缺点：范围过大，会稀释当前阶段对 AI observability 和评估的学习重点。

结论：暂不采用，可作为 Phase 3/4。

## 4. 数据模型设计

新增 `AppEvent` 表，用于记录可审计事件。

字段建议：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 事件 ID |
| event_type | string | 事件名称，如 `report.generated` |
| case_id | UUID nullable | 关联案件 |
| material_id | UUID nullable | 关联材料 |
| review_task_id | UUID nullable | 关联报告任务 |
| request_id | string nullable | 一次请求或流程的关联 ID |
| status | string | success / error / started |
| duration_ms | integer nullable | 耗时 |
| provider | string nullable | 模型提供商 |
| model | string nullable | 模型名称 |
| error_type | string nullable | 错误类型 |
| safe_metadata | JSON | 已脱敏元数据 |
| created_at | datetime | 创建时间 |

事件类型先覆盖核心链路：

```text
case.created
intake.answered
material.uploaded
material.normalized
material.indexed
retrieval.started
retrieval.completed
report.started
report.generated
review.approved
review.rejected
export.completed
operation.failed
```

## 5. Observability 服务设计

新增文件：

```text
app/services/observability.py
```

核心接口：

```python
def emit_event(
    event_type: str,
    *,
    status: str = "success",
    case_id: UUID | None = None,
    material_id: UUID | None = None,
    review_task_id: UUID | None = None,
    request_id: str | None = None,
    duration_ms: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    error_type: str | None = None,
    metadata: dict | None = None,
) -> None:
    ...
```

要求：

- 所有 metadata 入库前调用现有 `redact_sensitive_text`；
- 单个 metadata 字段长度做限制，避免把全文塞进事件表；
- 事件写入失败不能影响主业务流程；
- 测试环境可 monkeypatch `SessionLocal` 做隔离测试。

## 6. LangSmith 可选接入设计

新增配置：

```text
LANGSMITH_TRACING=false
LANGSMITH_PROJECT=legal-video-rag-agent
LANGSMITH_SAMPLE_RATE=0.0
LANGSMITH_RECORD_INPUTS=false
```

说明：

- `LANGSMITH_TRACING=false`：默认不上传 trace；
- `LANGSMITH_SAMPLE_RATE`：控制采样比例，默认 0；
- `LANGSMITH_RECORD_INPUTS=false`：默认只记录节点 metadata，不记录完整案情原文；
- `LANGSMITH_API_KEY` 使用 LangSmith 官方环境变量，不写入示例真实值。

接入点：

- `answer_intake`：记录问答轮次、是否 complete、耗时；
- `evidence_node`：记录 evidence 检索数量、top score；
- `authority_node`：记录 authority 检索数量、是否使用官方补充；
- `draft_node`：记录模型提供商、模型名、输出长度、耗时；
- `resume_review`：记录通过/驳回和人工备注长度。

LangSmith 用途：

- 调试 Agent 节点；
- 观察模型调用耗时；
- 对比 prompt 版本；
- 采集失败 trace 进入评估集；
- 后续接在线评估。

## 7. 报告质量回归评估

当前已有 `scripts/eval_retrieval.py` 用于 Recall@K 检索评估。Phase 2 新增报告质量检查：

```text
scripts/eval_report_quality.py
data/demo/loan_case/report_quality_cases.jsonl
tests/test_eval_report_quality.py
```

检查规则：

1. 必须包含免责声明；
2. 必须包含案情摘要；
3. 必须包含事件时间线；
4. 必须包含核心争议点；
5. 必须包含仍需补充或核实的信息；
6. 必须出现至少一个 `[证据N]`；
7. 必须出现至少一个 `[依据N]`；
8. 不得出现“胜诉率”“一定胜诉”“必然败诉”“法院一定支持”等禁止表达。

输出：

```json
{
  "total": 3,
  "passed": 2,
  "failed": 1,
  "pass_rate": 0.6667,
  "rows": [...]
}
```

这个脚本用于说明项目不只评估检索，还能对生成报告做结构化质量回归。

## 8. 前端与文档增强

前端不做复杂监控后台，只做轻量增强：

- 报告页展示最近一次任务状态；
- 如后端返回失败，提示“失败发生在材料处理 / 检索 / 模型生成 / 导出”；
- 文档新增“生产问题排查手册”。

新增文档：

```text
docs/production-observability.md
```

内容包括：

- 什么是 trace；
- 什么是审计事件；
- LangSmith 如何开启；
- 为什么默认关闭；
- 如何排查一次失败报告；
- 如何把失败样本加入回归评估。

## 9. 测试策略

新增测试：

1. `tests/test_observability.py`
   - metadata 会脱敏；
   - emit_event 写入 AppEvent；
   - emit_event 写入失败不影响主流程。

2. `tests/test_agent_observability.py`
   - `run_report` 会记录 `report.started` 和 `report.generated`；
   - 检索节点会记录召回数量；
   - review 节点会记录通过/驳回。

3. `tests/test_eval_report_quality.py`
   - 合格报告通过；
   - 缺少引用失败；
   - 出现禁止表达失败。

回归验证命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check app streamlit_app.py scripts tests
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp '.test-phase2'
.\.venv\Scripts\python.exe -m compileall -q app streamlit_app.py scripts
git diff --check
```

## 10. 本阶段不做的事

为了保持阶段目标清晰，本阶段暂不实现：

- 用户注册登录；
- 多用户权限隔离；
- Celery/RQ 异步队列；
- Prometheus/Grafana；
- OpenTelemetry 全链路追踪；
- 对象存储；
- 生产部署脚本。

这些是后续 Phase 3/4 的好方向，但 Phase 2 先把 AI 应用的 observability 和 eval 基础打牢。

## 11. 验收标准

Phase 2 完成时，项目应该能够做到：

1. 关键业务流程写入可审计事件；
2. 事件元数据默认脱敏；
3. LangSmith 可以通过环境变量开启；
4. 没有 LangSmith API Key 时，本地流程不受影响；
5. 检索评估和报告质量评估都可以独立运行；
6. 文档能解释如何排查一次失败报告；
7. 所有测试、静态检查和编译通过。

## 12. 面试讲法

可以这样介绍 Phase 2：

> 我在 Demo 基础上继续补了生产级可观测性。系统内部有统一 observability hook，每次材料上传、归一化、检索、报告生成和人工复核都会写入审计事件；LangSmith 是可选 trace 后端，默认关闭并支持脱敏和采样；同时增加了报告质量回归评估，检查报告结构、引用和禁止表达。这让我能从“能跑通”推进到“能排查、能复盘、能评估、能迭代”。

