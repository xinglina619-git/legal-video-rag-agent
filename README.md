# 法律多模态材料分析与法规检索 Agent

面向文本、音频和视频材料的公开演示项目。它将不同来源统一为可追溯的 `EvidenceSegment`，再通过 LangGraph 完成材料检索、法规资料检索、报告草稿和人工复核任务创建。

> 仅用于学习、材料整理与法规检索演示；不构成法律意见，不预测裁判结果，不替代律师或司法人员。

## 简历项目亮点

- **多模态材料归一化**：支持文档、图片、音频、视频，统一转换为带页码或时间戳的 `EvidenceSegment`。
- **混合检索 RAG**：基于 Qdrant 同时使用 dense embedding、sparse embedding 和 RRF 融合检索。
- **案件式 Agent 工作流**：通过案情问答、材料上传、案情确认、报告生成、人工复核形成完整闭环。
- **证据与依据双库**：本案材料进入 Evidence Collection，法规、司法解释和官方材料进入 Authority Collection。
- **可追溯报告**：报告来源清单区分 `[证据N]` 和 `[依据N]`，便于人工核验。
- **安全边界**：在线模型调用前脱敏手机号、身份证号、银行卡号；报告不输出胜诉率或确定裁判结论。

## 支持的输入

```text
文本：TXT / Markdown / PDF / DOCX / PPT
音频：MP3 / WAV / M4A
视频：MP4 / MOV / AVI / MKV
```

```text
文本 -> 解析 -> EvidenceSegment
音频 -> faster-whisper 转写 -> EvidenceSegment
视频 -> ffmpeg 提取音频 -> faster-whisper 转写 -> EvidenceSegment
```

每条证据片段保留来源文件、页码或起止时间戳、说话人字段和原文，便于报告引用回链。`pyannote.audio` 为可选依赖，用于后续补充说话人分离。

## Agent 工作流

```text
材料上传 -> 标准化 -> Qdrant Evidence 检索
                            +
                       Qdrant Authority 检索
                            |
               LangGraph 报告草稿生成节点
                            |
                    ReviewTask 人工审核
                            |
                 通过 / 驳回 / 编辑后导出
```

系统输出“材料摘要、时间线、待核查事实、法规资料摘要、人工复核问题和来源引用”，不输出责任认定或裁判预测。

## Demo 演示材料

民间借贷纠纷演示数据位于：

```text
data/demo/loan_case/
```

推荐阅读：

- `docs/architecture.md`：架构说明；
- `docs/demo-guide.md`：3 分钟演示步骤；
- `docs/interview-notes.md`：简历写法与高频面试题；
- `docs/task-status.md`：长任务状态设计；
- `docs/sample-output/loan_case_report_example.md`：示例报告。

## 快速开始

```powershell
Copy-Item .env.example .env.local
docker compose up -d
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
# 要启用音视频：python -m pip install -e ".[media]"
uvicorn app.main:app --reload --port 8001
```

另开终端：

```powershell
streamlit run streamlit_app.py --server.port 8502
```

## 检索评估

导入 demo 权威依据后，可以用 golden questions 做轻量 Recall@K 检查：

```powershell
.\.venv\Scripts\python.exe scripts\eval_retrieval.py data\demo\loan_case\golden_questions.jsonl --collection authority --limit 5
```

该脚本会输出 `total`、`hits`、`recall_at_k` 和每个问题召回的 `top_titles`，用于面试中说明“不是只凭感觉判断 RAG 效果”。

## 公开数据边界

`data/demo/` 中的转写材料和法规摘要均为演示用途。上传真实材料前必须确认拥有合法授权；不得上传他人隐私材料、未公开案件材料或受保密义务约束的信息。

## 多模型模式

界面可选择 Ollama、OpenAI、DeepSeek、百炼、硅基流动或 custom 兼容接口。API Key 仅保存在 `.env.local`，不发送到浏览器，不提交 Git。
