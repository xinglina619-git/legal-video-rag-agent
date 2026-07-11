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

## 4. Demo 阶段为什么不直接上 Celery

Celery、Redis、对象存储和任务队列更接近生产系统，但会明显扩大 Demo 范围。当前项目更适合先用数据库状态字段表达任务生命周期，保留后续扩展接口。

面试时可以这样回答：

> 当前 Demo 使用数据库状态字段记录材料处理、报告生成和人工复核进度。生产化时可以把转写、OCR、索引和报告生成拆成异步任务，用 Celery 或 RQ 执行，前端通过轮询或 WebSocket 查询任务状态。

