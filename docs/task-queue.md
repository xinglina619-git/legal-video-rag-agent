# 长任务队列与任务状态机

本项目把“视频转写、OCR、材料入库、综合报告生成”这类耗时操作抽象成后台任务。默认仍使用 `inline` 模式，便于本机 Demo 一键运行；需要展示接近生产系统的异步能力时，可以切换到 `rq` 模式并使用 Redis 与 Worker。

## 为什么需要队列

视频、音频和大文件处理通常会超过普通 HTTP 请求可接受的等待时间。如果让浏览器一直等待，容易出现超时、重复提交、用户不知道进度、服务重启后任务丢失等问题。

队列化后的流程是：

```text
前端提交材料/报告请求
        |
        v
创建 task_records 任务记录
        |
        +-- inline 模式：当前进程立即执行，适合 Demo
        |
        +-- rq 模式：写入 Redis 队列，由 Worker 执行
        |
        v
前端通过 /tasks/{task_id} 查询状态和结果
```

## 任务状态

| 状态 | 含义 |
| --- | --- |
| `queued` | 任务已创建，等待执行 |
| `running` | Worker 或 inline 执行器正在处理 |
| `succeeded` | 处理完成，`result` 中保存材料或报告结果 |
| `failed` | 处理失败，保存 `error_type` 与 `error_message` |

状态变化也会写入 `app_events`，因此可以和现有可观测性、LangSmith trace 组合排查问题。

## API

### 后台上传材料

```http
POST /materials/ingest?kind=evidence&case_id={case_id}&background=true
```

### 后台生成报告

```http
POST /reports
```

请求体示例：

```json
{
  "case_id": "uuid",
  "question": "整理案情、时间线、争议焦点和权威依据",
  "provider": "deepseek",
  "model": null,
  "background": true
}
```

### 查询任务状态

```http
GET /tasks/{task_id}
```

报告任务完成后，`result.review_task_id` 可用于继续读取报告、人工确认和导出 PDF/Word。

## 本机运行方式

Demo 默认配置：

```env
TASK_QUEUE_MODE=inline
```

这种模式不需要 Redis，接口会立即执行任务并返回最终状态，适合课堂演示和面试讲解。

如果要启用真正后台队列：

```powershell
python -m pip install -e ".[worker]"
docker compose up -d
$env:TASK_QUEUE_MODE="rq"
uvicorn app.main:app --reload --port 8001
```

另开一个终端启动 Worker：

```powershell
$env:TASK_QUEUE_MODE="rq"
python scripts/run_worker.py
```

Streamlit 页面会显示任务 ID、任务状态、失败原因和完成结果。

## 面试可讲点

- HTTP 请求只负责创建任务，耗时计算交给 Worker，避免浏览器超时。
- `task_records` 是任务状态事实表，便于恢复、审计和排障。
- `TASK_QUEUE_MODE=inline` 让 Demo 简单可跑，`rq` 模式展示生产演进路径。
- 任务事件进入 `app_events`，能和 LangSmith、日志、指标体系串起来。
- 当前版本保留 `max_attempts` 字段，为后续重试、死信队列和人工补偿预留扩展点。
