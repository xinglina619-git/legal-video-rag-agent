# 长任务状态设计说明

## 1. 为什么要设计任务状态

法律材料分析中有几类耗时操作：

- 视频抽音频；
- Whisper 转写；
- 视频抽帧 OCR；
- 大文件切分与向量索引；
- RAG 检索；
- 报告生成；
- 人工复核。

如果这些步骤完全同步执行，用户会遇到“页面一直转圈、不知道失败在哪里、刷新后结果丢失”的问题。因此即使是 Demo，也需要能解释任务状态。

## 2. 当前已有状态

当前项目已经有几类状态：

| 对象 | 字段 | 示例状态 |
|---|---|---|
| Material | `status` | normalizing / indexed / error |
| MaterialVersion | `state` | normalizing / indexed / error |
| CaseRecord | `status` | intake / ready / analyzing / completed |
| ReviewTask | `status` | generating / pending / approved / rejected |

这些状态已经能覆盖 Demo 的主要链路。

## 3. 推荐的轻量状态机

### 材料处理

```text
uploaded -> normalizing -> indexing -> indexed
                         -> error
```

说明：

- `uploaded`：文件已保存；
- `normalizing`：正在 OCR、转写或解析；
- `indexing`：正在写入检索库；
- `indexed`：可被检索；
- `error`：解析或索引失败。

### 报告生成

```text
generating -> pending_review -> approved
                         └──> rejected
                         └──> failed
```

说明：

- `generating`：正在检索和生成草稿；
- `pending_review`：等待人工确认；
- `approved`：人工确认通过；
- `rejected`：人工驳回或要求修改；
- `failed`：生成失败。

## 4. Demo 与生产模式的切换

当前项目默认使用 `TASK_QUEUE_MODE=inline`，便于本机演示；切换到 `rq` 后，材料处理和报告生成会由 Redis/RQ Worker 执行。两种模式共用 `TaskRecord` 状态、进度、重试和取消语义，前端通过 `/tasks/{task_id}` 查询。

面试时可以这样回答：

> Demo 默认同步是为了降低启动成本，但任务边界已经抽象出来。生产环境切换为 Redis/RQ Worker 后，HTTP 请求只负责创建任务，Worker 负责转写、OCR、索引和报告生成；数据库记录任务生命周期，前端查询阶段与进度。后续还可以接入死信队列、人工补偿和 WebSocket 推送。

