# 任务运行时可观测性设计

## 目标

在现有 Redis/RQ 可选任务队列基础上，补齐长视频、音频和报告任务的可观测生命周期，使用户能够看到当前阶段、进度、尝试次数和失败原因，并能取消尚未执行或下一次重试的任务。

## 范围

本阶段只增强任务运行时，不引入用户注册、多租户、Alembic 或新的模型供应商。同步 `inline` 模式和异步 `rq` 模式都必须保持可用。

## 状态与数据模型

`TaskRecord` 增加以下字段：

- `stage`：`queued`、`normalizing`、`indexing`、`generating`、`waiting_review`、`completed`、`failed`、`cancelled`；
- `progress`：0 到 100 的整数；
- `progress_message`：面向用户的当前阶段说明；
- `cancel_requested`：协作式取消标记。

任务状态使用 `queued`、`running`、`retrying`、`succeeded`、`failed`、`cancelled`。达到最大尝试次数后才进入 `failed`；任务在开始下一次尝试前发现取消标记时进入 `cancelled`。

## 运行逻辑

`task_queue.py` 提供：

- `update_task_progress(task_id, stage, progress, message)`：更新数据库并发出 `task.progress` 事件；
- `request_task_cancel(task_id)`：队列中的任务立即取消，运行中的任务设置协作式取消标记；
- `is_task_cancel_requested(task_id)`：供 Worker 在阶段边界检查；
- `run_task`：单次调用内部按照 `max_attempts` 重试，记录每次尝试和最后错误；
- `run_task_by_id`：RQ Worker 入口，保持幂等，已结束任务不重复执行。

材料任务至少报告“材料解析/转写”“索引写入”两个阶段；报告任务至少报告“检索与生成”“等待人工复核”两个阶段。取消不会强杀正在执行的 FFmpeg 或 Whisper 进程，而是在当前阶段结束后生效，避免留下半写入数据。

## API 与界面

- `GET /tasks/{task_id}` 返回状态、阶段、进度、尝试次数、最大尝试次数、取消标记、时间戳和错误信息；
- `POST /tasks/{task_id}/cancel` 请求取消任务；
- Streamlit 任务面板显示进度条、阶段说明、重试次数、失败信息和取消按钮；
- 报告任务成功后仍可通过任务结果加载报告；页面刷新后根据案件会话中的任务 ID 恢复状态。

## 幂等与失败处理

活动状态包括 `queued`、`running`、`retrying` 和 `succeeded`。相同幂等键命中活动任务时复用原任务，不创建重复任务。`failed` 任务允许用户重新提交新任务。所有进度、重试、取消和失败事件进入脱敏审计表。

## 验收标准

1. inline 模式下成功任务返回 100% 和 `succeeded`；
2. handler 前两次失败、第三次成功时任务最终成功且 `attempts == 3`；
3. 超过最大尝试次数后任务为 `failed`，保存错误类型和错误信息；
4. queued 任务可取消，运行中的任务在阶段边界后变为 `cancelled`；
5. API 返回完整任务状态，前端能展示进度并发起取消；
6. 原有任务队列、材料上传、报告生成和全量测试不回归。
