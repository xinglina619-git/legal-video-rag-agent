# 任务运行时可观测性实施计划

> **For agentic workers:** 本计划在当前会话的独立 worktree 中按测试先行方式执行；每个任务完成后运行对应测试，再进入下一项。

**目标：** 为材料处理和报告生成任务增加阶段进度、重试、协作式取消和可恢复的前端任务面板。

**架构：** 扩展现有 `TaskRecord` 保存运行时状态；由 `task_queue.py` 统一负责重试、进度和取消，具体 handler 只在业务阶段上报进度。FastAPI 提供状态和取消接口，Streamlit 根据任务 ID读取状态并展示结果。

**技术栈：** FastAPI、SQLAlchemy、SQLite/PostgreSQL、Redis/RQ（可选）、Streamlit、pytest、Ruff。

## 全局约束

- 保持 `TASK_QUEUE_MODE=inline` 为默认开发模式。
- `rq` 和 `redis` 仍是可选依赖，不得成为同步模式启动条件。
- 任务取消采用协作式语义，不强杀 FFmpeg、Whisper 或模型调用。
- 用户可见文档、状态文案和代码解释使用简体中文。
- 不修改认证、多租户、模型供应商和数据库迁移范围。

### Task 1：扩展任务状态响应与数据库模型

**文件：**
- 修改：`app/db.py`
- 修改：`app/schemas.py`
- 修改：`tests/test_task_queue.py`

**接口：**
- `TaskRecord.stage: str`
- `TaskRecord.progress: int`
- `TaskRecord.progress_message: str | None`
- `TaskRecord.cancel_requested: bool`
- `TaskStatusResponse` 暴露上述字段以及 `max_attempts` 和三个时间戳。

- [x] 先新增一个断言：新建任务的阶段为 `queued`、进度为 `0`、取消标记为 `False`，响应包含最大尝试次数。
- [x] 运行红测试，确认因字段不存在而失败。
- [x] 在 `TaskRecord` 和 `TaskStatusResponse` 增加字段，并在 `task_response` 中映射。
- [x] 重新运行该测试，确认通过。

### Task 2：实现进度更新与协作式取消

**文件：**
- 修改：`app/services/task_queue.py`
- 修改：`tests/test_task_queue.py`

**接口：**
- `update_task_progress(task_id: UUID, stage: str, progress: int, message: str | None = None) -> TaskStatusResponse`
- `request_task_cancel(task_id: UUID) -> TaskStatusResponse`
- `is_task_cancel_requested(task_id: UUID) -> bool`

- [x] 写测试验证进度被限制在 0 到 100，状态响应包含阶段和文案，并且取消 queued 任务后状态为 `cancelled`。
- [x] 运行新增测试确认失败。
- [x] 实现三个函数；数据库更新与 `task.progress`、`task.cancelled` 事件必须失败隔离。
- [x] 在 `run_task` 开始前和 handler 完成后检查取消标记；取消任务不得继续写入成功状态。
- [x] 运行任务队列测试，确认通过。

### Task 3：实现重试与阶段上报

**文件：**
- 修改：`app/services/task_queue.py`
- 修改：`app/config.py`
- 修改：`.env.example`
- 修改：`tests/test_task_queue.py`

**接口：**
- `run_task` 在 `max_attempts` 内重试，最终返回 `succeeded` 或 `failed`。
- `run_material_ingest_task` 上报 `normalizing` 和 `indexing`。
- `run_report_generation_task` 上报 `generating` 和 `waiting_review`。

- [x] 写两个测试：前两次抛错第三次成功；超过最大次数后失败并保存最后错误。
- [x] 运行新增测试确认失败。
- [x] 增加 `task_retry_delay_seconds` 配置，测试中使用零延迟；在每次重试之间写入 `retrying` 状态和 `task.retrying` 事件。
- [x] 在两个业务 handler 的阶段边界调用进度上报。
- [x] 运行任务队列全量测试，确认通过。

### Task 4：增加取消 API 与路由测试

**文件：**
- 修改：`app/routers/tasks.py`
- 修改：`tests/test_task_routes.py`

**接口：**
- `POST /tasks/{task_id}/cancel -> TaskStatusResponse`

- [x] 写路由测试验证 queued 任务返回 200 和 `cancelled`。
- [x] 运行测试确认失败。
- [x] 增加路由并将不存在任务映射为 404。
- [x] 运行任务路由测试，确认通过。

### Task 5：增强 Streamlit 任务面板

**文件：**
- 修改：`streamlit_app.py`
- 修改：`docs/task-queue.md`

- [x] 先检查现有任务面板的状态字段读取，不改变同步默认行为。
- [x] 增加进度条、阶段文案、尝试次数/最大尝试次数、错误信息和取消按钮。
- [x] 任务成功后保留现有“载入这份报告”行为；页面刷新后继续读取已记录任务 ID。
- [x] 在文档中说明取消是协作式的，以及 `inline` 和 `rq` 两种模式的差异。

### Task 6：全量验证与交付说明

**文件：**
- 修改：`README.md`
- 修改：`docs/interview-notes.md`

- [x] 运行 Ruff：`..\\legal-video-rag-agent\\.venv\\Scripts\\python.exe -m ruff check app streamlit_app.py scripts tests`。
- [x] 运行测试：`..\\legal-video-rag-agent\\.venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider --basetemp .test-runtime-final`。
- [x] 运行编译检查：`..\\legal-video-rag-agent\\.venv\\Scripts\\python.exe -m compileall -q app streamlit_app.py scripts`。
- [x] 运行 `git diff --check`，确认无空白错误。
- [x] 更新 README 和面试笔记，加入任务状态机、重试、幂等和取消的讲解。
