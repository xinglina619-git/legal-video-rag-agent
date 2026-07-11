# 3 分钟演示指南

## 演示目标

让面试官看到这个项目不是普通聊天机器人，而是一个完整的法律纠纷材料分析链路：

```text
案情问答 -> 多模态材料处理 -> RAG 检索 -> Agent 报告 -> 人工复核 -> PDF/Word 导出
```

## 准备数据

使用目录：

```text
data/demo/loan_case/
```

建议上传顺序：

1. `case_story.md`：基础案情；
2. `chat_record_excerpt.md`：聊天记录；
3. `transfer_record_excerpt.md`：转账记录；
4. `authority/civil_code_loan_excerpt.md`：作为权威依据导入。

## 演示步骤

### 1. 启动服务

```powershell
docker compose up -d
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

另开终端：

```powershell
$env:API_URL="http://127.0.0.1:8001"
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.port 8502
```

### 2. 导入权威依据

在左侧“管理员：补充权威文件”中上传：

```text
data/demo/loan_case/authority/civil_code_loan_excerpt.md
```

上传后左侧“已收录权威文件”应大于 0。

### 3. 创建案件

案件名称：

```text
张某与李某民间借贷纠纷
```

纠纷类型：

```text
民间借贷 / 合同纠纷
```

纠纷双方：

```text
出借人张某、借款人李某
```

事情经过可粘贴 `case_story.md` 的基础事实。

### 4. 问答补充

按 Agent 提问补充金额、还款时间、已还金额、现有证据。面试时可以强调：

> 系统不是要求用户一次性写完整材料，而是通过最多 8 轮追问逐步补齐关键事实。

### 5. 上传案件材料

在“补充材料”页上传：

```text
data/demo/loan_case/chat_record_excerpt.md
data/demo/loan_case/transfer_record_excerpt.md
```

面试时可以强调：

> 如果上传的是图片、音频或视频，会先 OCR / 转写，再统一成 EvidenceSegment。

### 6. 确认案情并生成报告

在“确认案情”页检查摘要，点击确认。进入“分析报告”页生成报告。重点展示：

- 报告不会输出胜诉率；
- 报告会区分事实、争议点、证据作用和待核实信息；
- 来源清单会显示 `[证据1]`、`[依据1]` 等编号；
- 可以人工修改后确认；
- 可以导出 PDF 和 Word。

## 演示话术

可以这样讲：

> 这个项目重点不是让 AI 直接给法律结论，而是把零散材料结构化、检索化、可引用化。模型只基于检索到的案情材料和权威依据生成报告，最后必须经过人工复核。

## 检索评估演示

导入权威依据后，可以运行：

```powershell
.\.venv\Scripts\python.exe scripts\eval_retrieval.py data\demo\loan_case\golden_questions.jsonl --collection authority --limit 5
```

输出会包含：

- `total`：评估问题数量；
- `hits`：命中数量；
- `recall_at_k`：Recall@K；
- `top_titles`：每个问题召回的标题。

