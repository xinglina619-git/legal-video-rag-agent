# Phase 3：长任务队列化与任务状态机设计

## 1. 背景与目标

当前项目已经具备多模态材料处理、RAG 检索、LangGraph 报告生成、人工复核、导出、可观测性和质量评估能力。下一步要补齐生产级 AI 应用最常见的问题：长任务不能阻塞 HTTP 请求，用户刷新页面后任务不能丢，失败要能追踪、重试和复盘。

本阶段目标：

1. 新增 `TaskRecord` 任务表；
2. 新增统一任务服务；
3. 新增任务状态查询 API；
4. 材料处理和报告生成支持可选后台任务；
5. 支持 inline fallback，保证没有 Redis/RQ 时 Demo 仍可运行；
6. 与现有 AppEvent 审计事件联动；
7. 文档说明生产中如何切换到 Redis + RQ worker。

## 2. 设计原则

### 2.1 不破坏现有同步流程

现有 `/materials/ingest` 和 `/reports` 默认继续同步返回结果，保证演示路径稳定。

新增异步模式通过显式参数开启：

```text
POST /materials/ingest?background=true
POST /reports with {"background": true}
```

### 2.2 队列可选，默认 inline

配置：

```env
TASK_QUEUE_MODE=inline
REDIS_URL=redis://localhost:6379/0
TASK_QUEUE_NAME=legal-agent
TASK_MAX_ATTEMPTS=3
```

说明：

- `inline`：任务创建后立即在当前进程执行，适合本地 Demo 和测试；
- `rq`：任务入 Redis/RQ 队列，由 worker 执行，适合生产化演示；
- 没有安装 RQ 或 Redis 不可用时，系统给出清晰错误，不静默丢任务。

### 2.3 任务状态可恢复

任务状态：

```text
queued -> running -> succeeded
                 └-> failed
```

补充状态：

- `retrying`：后续扩展；
- `cancelled`：后续扩展。

## 3. 数据模型

新增 `TaskRecord`：

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 任务 ID |
| task_type | string | `material_ingest` / `report_generation` |
| status | string | queued / running / succeeded / failed |
| idempotency_key | string nullable | 幂等键 |
| case_id | UUID nullable | 关联案件 |
| material_id | UUID nullable | 关联材料 |
| review_task_id | UUID nullable | 关联报告复核任务 |
| payload_json | JSON | 任务输入参数，存储安全路径和必要 metadata |
| result_json | JSON | 任务输出结果 |
| error_type | string nullable | 错误类型 |
| error_message | text nullable | 错误摘要 |
| attempts | integer | 已尝试次数 |
| max_attempts | integer | 最大尝试次数 |
| created_at | datetime | 创建时间 |
| started_at | datetime nullable | 开始时间 |
| finished_at | datetime nullable | 结束时间 |
| updated_at | datetime | 更新时间 |

## 4. 服务设计

新增：

```text
app/services/task_queue.py
app/routers/tasks.py
scripts/run_worker.py
```

核心函数：

```python
create_task(...)
get_task(...)
mark_task_running(...)
mark_task_succeeded(...)
mark_task_failed(...)
enqueue_task(...)
run_material_ingest_task(...)
run_report_generation_task(...)
```

## 5. API 设计

### 查询任务

```text
GET /tasks/{task_id}
```

返回：

```json
{
  "task_id": "...",
  "task_type": "material_ingest",
  "status": "succeeded",
  "result": {...},
  "error_type": null,
  "error_message": null,
  "attempts": 1
}
```

### 后台材料处理

```text
POST /materials/ingest?kind=evidence&case_id=...&background=true
```

返回：

```json
{
  "task_id": "...",
  "task_type": "material_ingest",
  "status": "queued"
}
```

### 后台报告生成

`ReportRequest` 新增：

```json
{
  "case_id": "...",
  "question": "...",
  "background": true
}
```

返回：

```json
{
  "task_id": "...",
  "task_type": "report_generation",
  "status": "queued"
}
```

## 6. Worker 设计

```powershell
.\.venv\Scripts\python.exe scripts\run_worker.py
```

worker 负责：

- 监听 RQ 队列；
- 执行材料处理任务；
- 执行报告生成任务；
- 更新 `TaskRecord`；
- 写入 AppEvent。

## 7. 前端设计

Streamlit 暂做轻量增强：

- 材料上传按钮旁增加“后台处理”选项；
- 报告生成按钮旁增加“后台生成”选项；
- 返回 task_id 后显示任务状态；
- 通过“刷新任务状态”按钮或简单轮询查看结果；
- 成功后展示材料结果或报告结果。

## 8. 验收标准

1. 默认同步流程不变；
2. 后台模式可以返回 task_id；
3. `GET /tasks/{task_id}` 可查询状态；
4. inline 模式下测试不依赖 Redis；
5. 任务成功写入 result_json；
6. 任务失败写入 error_type / error_message；
7. 任务事件写入 AppEvent；
8. ruff、pytest、compileall、diff check 通过。

