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
| `retrying` | 本次尝试失败，仍有剩余尝试次数 |
| `succeeded` | 处理完成，`result` 中保存材料或报告结果 |
| `failed` | 处理失败，保存 `error_type` 与 `error_message` |
| `cancelled` | 已取消；运行中的任务会在当前阶段结束后协作式停止 |

状态变化也会写入 `app_events`，因此可以和现有可观测性、LangSmith trace 组合排查问题。

任务响应还会返回当前阶段、0 到 100 的 `progress`、阶段说明 `progress_message`、`attempts` / `max_attempts` 和 `cancel_requested`。

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

### 取消任务

```http
POST /tasks/{task_id}/cancel
```

取消是协作式的：排队中的任务会立即标记为 `cancelled`，正在执行 FFmpeg、Whisper 或模型调用的任务不会被强制杀死，而是在当前处理阶段结束后停止下一阶段或下一次重试。

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
docker compose --env-file .env.local up -d
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
- Worker 失败会在 `max_attempts` 内自动重试，并保存最后一次错误，便于后续接入死信队列和人工补偿。
- 材料任务会展示“解析/转写”和“索引写入”阶段，报告任务会展示“检索与生成”和“等待人工复核”阶段。
- 前端刷新页面后仍可根据会话中的任务 ID 查询状态，不依赖浏览器一直保持原请求。
