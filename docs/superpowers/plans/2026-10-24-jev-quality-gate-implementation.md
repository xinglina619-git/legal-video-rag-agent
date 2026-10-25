# JEV 报告质量门实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 在现有法律多模态 RAG Agent 的报告草稿与人工复核之间增加可选的 JEV 结构化质量门，并在 JEV 不可用时安全降级。

**架构：** 新增独立 `app/services/jev.py`，使用官方 `/api/v1/systemone/` 接口和 Pydantic 模型完成请求、响应校验与错误归一化。`app.services.agent.run_report` 在草稿生成后调用质量门，结果进入 `ReportResponse` 和 `AppEvent.safe_metadata`；保存报告接口从审计事件读取最近一次质量门结果，避免新增数据库列和迁移。前端只展示风险提示与置信度，报告始终保持人工复核状态。

**技术栈：** Python 3.11、Pydantic v2、`requests`、FastAPI、SQLAlchemy、LangGraph、Streamlit、pytest、Ruff。

## 全局约束

- `JEV_ENABLED=false` 必须是默认值，未配置 JEV Key 时不影响既有流程。
- JEV 只用于结构化判断，不生成报告、不自动批准报告、不替代人工复核。
- 发送到 JEV 的状态必须经过 `redact_sensitive_text`，不得包含 API Key、原始文件路径或未脱敏隐私字段。
- 使用固定 `JEV_MODEL`，并记录响应中的 `model_version`、`request_id`、耗时和用量。
- JEV 超时、429、5xx、响应校验失败均降级为 `available=false` 的人工复核提示。
- 每个任务结束都运行针对性测试和全量测试；代码注释使用简体中文。

---

### 任务 1：配置与质量门数据模型

**文件：**
- 修改：`app/config.py`
- 修改：`.env.example`
- 修改：`app/schemas.py`
- 测试：`tests/test_jev.py`

**接口：**
- 新增配置：`jev_enabled`、`jev_api_key`、`jev_base_url`、`jev_model`、`jev_timeout_seconds`、`jev_min_confidence`。
- 新增 `QualityGateAssessment`，字段至少包括 `available`、`needs_human_review`、`risk_level`、`confidence`、`answers`、`model_version`、`request_id`、`latency_ms`、`reason`。
- `ReportResponse.quality_gate` 为可选 `QualityGateAssessment`，保持旧客户端可解析。

- [ ] **步骤 1：先写失败测试**

```python
def test_jev_settings_are_disabled_by_default():
    settings = Settings(_env_file=None)
    assert settings.jev_enabled is False
    assert settings.jev_model == "jev-1.13"
    assert settings.jev_min_confidence == 0.85


def test_report_response_allows_quality_gate():
    payload = ReportResponse(
        report_markdown="# 报告",
        citations=[],
        review_task_id=uuid4(),
        status="pending",
        legal_domain="general",
        quality_gate=QualityGateAssessment(
            available=False,
            needs_human_review=True,
            reason="JEV 未启用",
        ),
    )
    assert payload.quality_gate.available is False
```

- [ ] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_jev.py::test_jev_settings_are_disabled_by_default tests/test_jev.py::test_report_response_allows_quality_gate -q`

预期：失败，原因是配置字段和响应模型尚不存在。

- [ ] **步骤 3：实现最小模型和配置**

在 `Settings` 增加默认关闭的 JEV 字段，在 `.env.example` 增加对应变量，在 `schemas.py` 增加质量门模型并把 `ReportResponse.quality_gate` 设为可选。

- [ ] **步骤 4：运行测试确认通过**

运行同一条 pytest 命令，预期 2 项通过。

- [ ] **步骤 5：提交**

```powershell
git add app/config.py app/schemas.py .env.example tests/test_jev.py
git commit -m "feat: add jev quality gate schemas"
```

### 任务 2：JEV 客户端与错误降级

**文件：**
- 创建：`app/services/jev.py`
- 修改：`tests/test_jev.py`

**接口：**
- `evaluate_quality_gate(settings: Settings, state: str, *, request_id: str) -> QualityGateAssessment`
- `JevDecisionError`：统一表示 HTTP、超时和响应校验失败。
- JEV 请求使用 `POST {base_url.rstrip('/')}/systemone/`，请求体包含固定模型、state 和五个问题；头部包含 Bearer Key、Content-Type、`Idempotency-Key`。

- [ ] **步骤 1：先写失败测试**

```python
def test_quality_gate_posts_typed_questions_and_parses_response(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "dec_123",
                "model": "jev-1.13",
                "model_version": "jev-1.13-20260917",
                "answers": {
                    "grounding_ok": {"type": "noul", "noul": 0.96},
                    "authority_sufficient": {"type": "noul", "noul": 0.90},
                    "overclaiming_risk": {"type": "noul", "noul": 0.04},
                    "needs_human_review": {"type": "noul", "noul": 0.91},
                    "risk_level": {"type": "choice", "choice": "medium", "confidence": 0.88},
                    "evidence_sufficiency": {"type": "score", "score": 2.2, "confidence": 0.86},
                },
                "usage": {"input_tokens": 120, "charged_tokens": 120},
                "latency_ms": 220,
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(jev.requests, "post", fake_post)
    result = evaluate_quality_gate(Settings(jev_enabled=True, jev_api_key="test"), "脱敏报告", request_id="r1")
    assert result.available is True
    assert result.needs_human_review is True
    assert captured["url"].endswith("/systemone/")
    assert len(captured["kwargs"]["json"]["questions"]) == 6


def test_quality_gate_degrades_when_jev_is_unavailable(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(jev.requests, "post", fail_post)
    result = evaluate_quality_gate(Settings(jev_enabled=True, jev_api_key="test"), "报告", request_id="r1")
    assert result.available is False
    assert result.needs_human_review is True
    assert "timeout" in result.reason.lower()
```

- [ ] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_jev.py -q`

预期：失败，原因是客户端尚未实现。

- [ ] **步骤 3：实现最小客户端**

使用 `requests.post` 和配置超时调用 JEV；构造固定问题；对响应做 Pydantic/字典校验；将最低置信度检查和失败原因归一化为 `QualityGateAssessment`。日志和异常消息不得包含 state 原文或 API Key。

- [ ] **步骤 4：运行测试确认通过**

运行：`pytest tests/test_jev.py -q`，预期客户端成功、超时、未启用和无 Key 场景全部通过。

- [ ] **步骤 5：提交**

```powershell
git add app/services/jev.py tests/test_jev.py
git commit -m "feat: add resilient jev decision client"
```

### 任务 3：接入报告生成和审计事件

**文件：**
- 修改：`app/services/agent.py`
- 修改：`app/routers/reports.py`
- 修改：`tests/test_agent_observability.py`
- 修改：`tests/test_case_workflow.py`

**接口：**
- 报告生成完成后调用 `evaluate_quality_gate`，不改变 `ReviewTask.status="pending"`。
- 质量门成功事件：`report.quality_gate_completed`；降级事件：`report.quality_gate_unavailable`。
- 保存报告接口从 `AppEvent` 查询最近质量门事件并返回 `quality_gate`，没有事件时返回不可用且需要人工复核的默认值。

- [ ] **步骤 1：先写失败测试**

在已有报告测试中 monkeypatch `agent.evaluate_quality_gate` 返回可用结果，断言：

```python
assert result.quality_gate.risk_level == "medium"
assert result.status == "pending"
assert "report.quality_gate_completed" in event_types
```

增加 JEV 返回不可用时的测试，断言报告仍成功生成、状态仍为 `pending`，并包含 `report.quality_gate_unavailable`。

- [ ] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_agent_observability.py::test_report_and_review_emit_events tests/test_case_workflow.py::test_case_report_uses_case_context_and_persists_citations -q`

预期：失败，原因是 `ReportResponse` 没有质量门结果，且报告流程尚未调用客户端。

- [ ] **步骤 3：实现最小接入**

构造脱敏状态并调用客户端；将返回结果写入响应和审计事件；在 `saved_report` 查询 `AppEvent` 时按 `review_task_id`、事件类型和时间倒序取最近一条质量门结果。JEV 异常不得冒泡到报告主流程。

- [ ] **步骤 4：运行测试确认通过**

运行上述两条测试，预期通过；再运行 `pytest tests/test_jev.py tests/test_agent_observability.py tests/test_case_workflow.py -q`。

- [ ] **步骤 5：提交**

```powershell
git add app/services/agent.py app/routers/reports.py tests/test_agent_observability.py tests/test_case_workflow.py
git commit -m "feat: add jev report quality gate"
```

### 任务 4：前端展示质量门

**文件：**
- 修改：`streamlit_app.py`
- 修改：`tests/test_jev.py`

**接口：**
- 报告复核区域增加“JEV 质量门”折叠面板；显示是否可用、风险等级、置信度、人工复核建议和降级原因。
- 不显示原始 JEV state，不允许前端根据 JEV 结果直接确认报告。

- [ ] **步骤 1：先写失败测试**

增加纯函数测试（将展示文案提取为 `quality_gate_summary`），验证可用和不可用结果分别产生“风险等级/置信度”和“服务不可用/仍需人工复核”文案。

- [ ] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_jev.py -q`，预期展示摘要函数尚不存在。

- [ ] **步骤 3：实现最小展示**

在报告加载后、编辑器之前展示质量门摘要；缺失字段使用安全默认值；不改变已有确认/驳回按钮。

- [ ] **步骤 4：运行测试确认通过**

运行：`pytest tests/test_jev.py -q`，预期全部通过。

- [ ] **步骤 5：提交**

```powershell
git add streamlit_app.py tests/test_jev.py
git commit -m "feat: show jev quality gate in review ui"
```

### 任务 5：文档、配置示例和全量验证

**文件：**
- 修改：`README.md`
- 修改：`docs/architecture.md`
- 修改：`docs/production-observability.md`
- 修改：`docs/interview-notes.md`
- 修改：`.env.example`

- [ ] **步骤 1：更新文档**

说明 JEV 是可选决策层，不是报告生成模型；补充启用方法、固定版本、脱敏、人工复核、失败降级和质量评估指标。

- [ ] **步骤 2：运行针对性测试**

运行：`pytest tests/test_jev.py tests/test_agent_observability.py tests/test_case_workflow.py -q`。

- [ ] **步骤 3：运行全量验证**

运行：

```powershell
$py='D:\AI20260326课件\ai课题\ai-projects\legal-video-rag-agent\.venv\Scripts\python.exe'
& $py -m pytest -q
& $py -m ruff check app tests scripts streamlit_app.py
& $py -m compileall -q app scripts streamlit_app.py
git diff --check
```

预期：pytest 全部通过，Ruff 无错误，compileall 返回 0，diff check 无错误。

- [ ] **步骤 4：提交最终文档和验证结果**

```powershell
git add README.md docs/architecture.md docs/production-observability.md docs/interview-notes.md .env.example
git commit -m "docs: explain jev quality gate and fallback"
```
