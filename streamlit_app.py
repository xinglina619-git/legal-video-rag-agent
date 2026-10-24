import os

import requests
import streamlit as st

from app.services.jev import quality_gate_summary

API_URL = os.getenv("API_URL", "http://localhost:8001")
PROVIDERS = ["deepseek", "openai", "ollama", "dashscope", "siliconflow", "custom"]
DISPUTE_TYPES = {
    "民间借贷 / 合同纠纷": "loan_contract",
    "劳动纠纷": "labor",
    "婚姻家庭纠纷": "marriage_family",
    "其他纠纷": "general",
}

st.set_page_config(page_title="法律纠纷智能受理与分析", page_icon="⚖️", layout="wide")


def api_get(path: str, **kwargs):
    try:
        response = requests.get(f"{API_URL}{path}", timeout=30, **kwargs)
        return response.json() if response.ok else None
    except requests.RequestException:
        return None


def api_send(method: str, path: str, **kwargs):
    try:
        response = requests.request(method, f"{API_URL}{path}", **kwargs)
        if response.ok:
            return response.json(), None
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        return None, str(detail)
    except requests.RequestException as exc:
        return None, f"无法连接后端服务：{exc}"


def refresh_case(case_id: str):
    case = api_get(f"/cases/{case_id}")
    if case:
        st.session_state["selected_case_id"] = case_id
        st.session_state["selected_case"] = case
    return case


def remember_task(case_id: str, task_id: str) -> None:
    key = f"task_ids_{case_id}"
    task_ids = st.session_state.setdefault(key, [])
    if task_id not in task_ids:
        task_ids.append(task_id)


def render_task_panel(case_id: str, *, scope: str) -> None:
    task_ids = st.session_state.get(f"task_ids_{case_id}", [])
    if not task_ids:
        return
    with st.expander("后台任务状态", expanded=True):
        st.caption("适合处理视频转写、OCR、报告生成等耗时任务；点击刷新可重新查询后端状态。")
        if st.button("刷新任务状态", key=f"refresh_tasks_{scope}_{case_id}"):
            st.rerun()
        for task_id in task_ids:
            task = api_get(f"/tasks/{task_id}")
            if not task:
                st.warning(f"任务 {task_id} 暂时无法读取。")
                continue
            status = task.get("status", "unknown")
            stage = task.get("stage", "queued")
            progress = max(0, min(int(task.get("progress", 0)), 100))
            stage_message = task.get("progress_message") or "等待 Worker 处理"
            st.markdown(
                f"- `{task['task_id']}`｜{task['task_type']}｜"
                f"状态：`{status}`｜阶段：`{stage}`｜"
                f"尝试：{task.get('attempts', 0)}/{task.get('max_attempts', 3)}"
            )
            st.progress(progress, text=f"{stage_message}（{progress}%）")
            cancelable = status in {"queued", "running", "retrying"}
            if cancelable and st.button(
                "取消任务", key=f"cancel_task_{scope}_{task_id}", type="secondary"
            ):
                _, error = api_send("POST", f"/tasks/{task_id}/cancel", timeout=30)
                if error:
                    st.error(f"取消失败：{error}")
                else:
                    st.info("已提交取消请求；正在执行的转写或模型调用会在当前阶段结束后停止。")
                    st.rerun()
            if task.get("error_message"):
                st.error(f"{task.get('error_type') or 'Error'}：{task['error_message']}")
            result = task.get("result") or {}
            if result:
                with st.expander(f"查看任务结果：{task['task_id']}", expanded=False):
                    st.json(result)
            review_task_id = result.get("review_task_id")
            if task["task_type"] == "report_generation" and review_task_id:
                if st.button("载入这份报告", key=f"load_report_{scope}_{task_id}"):
                    report = api_get(f"/reports/{review_task_id}")
                    if report:
                        st.session_state[f"report_{case_id}"] = report
                        st.success("报告已载入。")
                        st.rerun()
                    else:
                        st.warning("报告任务已完成，但暂时无法读取报告详情。")


with st.sidebar:
    st.header("⚖️ 分析设置")
    provider = st.selectbox("大模型提供商", PROVIDERS, index=0)
    model = st.text_input("模型名称（留空使用环境配置）")
    st.caption("在线分析前会自动遮盖手机号、身份证号和银行卡号。")

    st.divider()
    st.subheader("权威依据库")
    stats = api_get("/authorities/stats")
    if stats:
        st.metric("已收录权威文件", stats["documents"])
        if not stats["documents"]:
            st.info("尚未导入权威文件。报告可以生成，但不会获得本地法条依据。")
        elif stats["by_status"]:
            st.caption(
                "有效性：" + "，".join(f"{key} {value}" for key, value in stats["by_status"].items())
            )
    else:
        st.warning("后端服务尚未启动。")

    with st.expander("管理员：补充权威文件"):
        authority_upload = st.file_uploader(
            "上传法规、司法解释或案例",
            type=["md", "txt", "pdf", "docx"],
            key="authority_upload",
        )
        if authority_upload and st.button("加入权威依据库", use_container_width=True):
            with st.spinner("正在提取文字并建立检索索引…"):
                body, error = api_send(
                    "POST",
                    "/materials/ingest",
                    params={"kind": "authority"},
                    files={
                        "file": (
                            authority_upload.name,
                            authority_upload.getvalue(),
                            authority_upload.type,
                        )
                    },
                    timeout=600,
                )
            if error:
                st.error(error)
            else:
                st.success(f"已收录《{body['title']}》，共 {body['segments']} 个可检索片段")
                st.rerun()

st.title("法律纠纷智能受理与分析 Agent")
st.caption("通过案情问答与多模态材料整理事实，检索权威依据，生成供咨询准备和人工核验的分析报告。")
st.warning("本系统用于材料整理和法律咨询准备，不构成正式法律意见，也不预测案件结果。")

cases = api_get("/cases") or []
with st.expander("➕ 创建新案件", expanded=not cases):
    with st.form("create_case", clear_on_submit=True):
        left, right = st.columns(2)
        title = left.text_input("案件名称", placeholder="例如：张某借款未归还纠纷")
        dispute_label = right.selectbox("纠纷类型", list(DISPUTE_TYPES))
        parties = st.text_input("纠纷双方", placeholder="例如：出借人张某、借款人李某")
        narrative = st.text_area(
            "先简单描述事情经过",
            placeholder="不必一次写完整，Agent 后续会针对缺失信息继续提问。",
            height=110,
        )
        request = st.text_input("你希望解决什么问题？", placeholder="例如：希望对方归还本金和约定利息")
        submitted = st.form_submit_button("创建并开始问答", type="primary")
    if submitted:
        if not title.strip():
            st.error("请填写案件名称。")
        else:
            body, error = api_send(
                "POST",
                "/cases",
                json={
                    "title": title,
                    "dispute_type": DISPUTE_TYPES[dispute_label],
                    "parties": parties,
                    "narrative": narrative,
                    "request": request,
                },
                timeout=30,
            )
            if error:
                st.error(error)
            else:
                st.session_state["selected_case_id"] = body["case_id"]
                st.success("案件已创建。")
                st.rerun()

if not cases:
    st.info("请先创建一个案件。你可以只做问答，也可以随后上传视频、聊天截图或合同等材料。")
    st.stop()

case_options = {f"{item['title']} · {item['status']}": item["case_id"] for item in cases}
current_id = st.session_state.get("selected_case_id")
labels = list(case_options)
default_index = next(
    (index for index, label in enumerate(labels) if case_options[label] == current_id), 0
)
selected_label = st.selectbox("当前案件", labels, index=default_index)
case_id = case_options[selected_label]
case = refresh_case(case_id)
if not case:
    st.error("案件读取失败，请确认后端服务状态。")
    st.stop()

status_map = {"intake": "信息采集中", "ready": "可生成报告", "analyzing": "分析中", "completed": "已有报告"}
st.caption(f"当前状态：{status_map.get(case['status'], case['status'])}　｜　案件材料：{len(case['materials'])} 份")

intake_tab, material_tab, confirm_tab, report_tab = st.tabs(
    ["1️⃣ 案情问答", "2️⃣ 补充材料", "3️⃣ 确认案情", "4️⃣ 分析报告"]
)

with intake_tab:
    st.subheader("Agent 补充问答")
    st.caption("最多追问 8 轮；信息足够时会提前结束。每次只需要回答一个问题。")
    for message in case["messages"]:
        with st.chat_message("assistant" if message["role"] == "assistant" else "user"):
            st.write(message["content"])
    if case["status"] == "intake":
        with st.form("intake_answer", clear_on_submit=True):
            answer = st.text_area("你的回答", height=90, label_visibility="collapsed")
            send = st.form_submit_button("提交回答", type="primary")
        if send and answer.strip():
            with st.spinner("正在整理回答并判断还缺少哪些信息…"):
                body, error = api_send(
                    "POST",
                    f"/cases/{case_id}/intake",
                    json={"answer": answer, "provider": provider, "model": model or None},
                    timeout=180,
                )
            if error:
                st.error(error)
            else:
                if body["complete"]:
                    st.success("关键信息已基本收集完成，请进入“确认案情”。")
                st.rerun()
    else:
        st.success("问答已完成。仍可在“确认案情”中修正或补充摘要。")

with material_tab:
    st.subheader("上传与本案有关的材料")
    st.caption(
        "视频会提取声音转写，并每隔约 15 秒抽帧识别画面文字；所有片段保留页码或时间点，便于报告引用。"
    )
    uploads = st.file_uploader(
        "支持视频、音频、图片、PDF、Word 和文本，可一次选择多份",
        type=[
            "md",
            "txt",
            "pdf",
            "docx",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "bmp",
            "mp3",
            "wav",
            "m4a",
            "aac",
            "mp4",
            "mov",
            "avi",
            "mkv",
        ],
        accept_multiple_files=True,
    )
    background_ingest = st.checkbox(
        "后台处理材料（推荐用于视频、音频或大文件）",
        value=False,
        key=f"background_ingest_{case_id}",
    )
    if uploads and st.button("处理并加入本案", type="primary"):
        progress = st.progress(0, text="准备处理材料")
        for index, upload in enumerate(uploads, start=1):
            progress.progress(
                (index - 1) / len(uploads), text=f"正在处理：{upload.name}"
            )
            body, error = api_send(
                "POST",
                "/materials/ingest",
                params={
                    "kind": "evidence",
                    "case_id": case_id,
                    "background": str(background_ingest).lower(),
                },
                files={"file": (upload.name, upload.getvalue(), upload.type)},
                timeout=900,
            )
            if error:
                st.error(f"{upload.name}：{error}")
            elif "task_id" in body:
                remember_task(case_id, body["task_id"])
                st.success(f"{upload.name}：已创建后台任务 {body['task_id']}")
            else:
                st.success(f"{upload.name}：已形成 {body['segments']} 个可引用片段")
        progress.progress(1.0, text="材料处理完成")
        st.rerun()

    render_task_panel(case_id, scope="materials")

    if case["materials"]:
        st.dataframe(
            [
                {
                    "材料名称": item["title"],
                    "来源": item["source_type"],
                    "用途": "问答案情" if item["role"] == "intake" else "案件材料",
                    "状态": item["status"],
                }
                for item in case["materials"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("尚未上传附件。仅使用问答内容也可以生成报告。")

with confirm_tab:
    st.subheader("确认 Agent 整理的案情")
    summary_value = case["confirmed_summary"] or case["intake_summary"]
    if not summary_value:
        facts = case["basic_facts"]
        summary_value = "\n".join(
            [
                f"纠纷双方：{facts.get('parties', '')}",
                f"事情经过：{facts.get('narrative', '')}",
                f"希望解决：{facts.get('request', '')}",
            ]
        )
    summary = st.text_area(
        "请检查事实、时间和金额；可以直接修改",
        value=summary_value,
        height=300,
        key=f"summary_{case_id}",
    )
    if case["missing_information"]:
        st.info("建议继续补充：" + "；".join(case["missing_information"]))
    if st.button("确认案情摘要", type="primary"):
        body, error = api_send(
            "PATCH",
            f"/cases/{case_id}",
            json={"confirmed_summary": summary, "status": "ready"},
            timeout=30,
        )
        if error:
            st.error(error)
        else:
            st.success("案情摘要已确认，可以生成报告。")
            st.rerun()

with report_tab:
    st.subheader("生成通俗版分析报告")
    goal = st.text_area(
        "分析目标（通常无需修改）",
        value=case["analysis_goal"],
        height=80,
        key=f"goal_{case_id}",
    )
    if case["status"] == "intake":
        st.warning("问答尚未结束。你仍可提前生成，报告会标明缺失和待核实信息。")
    background_report = st.checkbox(
        "后台生成报告（推荐用于材料较多或模型响应较慢时）",
        value=False,
        key=f"background_report_{case_id}",
    )
    if st.button("开始综合分析", type="primary"):
        api_send(
            "PATCH",
            f"/cases/{case_id}",
            json={"analysis_goal": goal},
            timeout=30,
        )
        spinner_text = "正在提交后台报告任务…" if background_report else "正在检索本案材料和权威依据，并生成分析报告…"
        with st.spinner(spinner_text):
            body, error = api_send(
                "POST",
                "/reports",
                json={
                    "case_id": case_id,
                    "question": goal,
                    "provider": provider,
                    "model": model or None,
                    "background": background_report,
                },
                timeout=360,
            )
        if error:
            st.error(error)
        elif "task_id" in body:
            remember_task(case_id, body["task_id"])
            st.success(f"已创建后台报告任务：{body['task_id']}")
            st.rerun()
        else:
            st.session_state[f"report_{case_id}"] = body
            st.rerun()

    render_task_panel(case_id, scope="reports")

    report = st.session_state.get(f"report_{case_id}")
    if not report and case["report_ids"]:
        report = api_get(f"/reports/{case['report_ids'][0]}")
        if report:
            st.session_state[f"report_{case_id}"] = report
    if report:
        st.divider()
        with st.expander("JEV 质量门", expanded=True):
            for line in quality_gate_summary(report.get("quality_gate")):
                st.write(line)
        draft = st.text_area(
            "报告草稿（可人工修改后确认）",
            value=report["report_markdown"],
            height=560,
            key=f"report_editor_{case_id}",
        )
        review_notes = st.text_input("审核备注（可选）", key=f"notes_{case_id}")
        approve_col, reject_col = st.columns(2)
        if approve_col.button("确认报告", use_container_width=True, type="primary"):
            body, error = api_send(
                "POST",
                f"/materials/reviews/{report['review_task_id']}",
                json={
                    "approved": True,
                    "edited_report": draft,
                    "notes": review_notes or None,
                },
                timeout=60,
            )
            if error:
                st.error(error)
            else:
                report["report_markdown"] = body["report_markdown"]
                report["status"] = body["status"]
                st.success("报告已确认。")
        if reject_col.button("退回修改", use_container_width=True):
            body, error = api_send(
                "POST",
                f"/materials/reviews/{report['review_task_id']}",
                json={
                    "approved": False,
                    "edited_report": draft,
                    "notes": review_notes or None,
                },
                timeout=60,
            )
            if error:
                st.error(error)
            else:
                report["status"] = body["status"]
                st.info("报告已标记为需要修改。")

        pdf = requests.get(
            f"{API_URL}/reports/{report['review_task_id']}/pdf", timeout=60
        )
        docx = requests.get(
            f"{API_URL}/reports/{report['review_task_id']}/docx", timeout=60
        )
        download_pdf, download_docx = st.columns(2)
        if pdf.ok:
            download_pdf.download_button(
                "下载 PDF",
                pdf.content,
                file_name=f"{case['title']}-分析报告.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        if docx.ok:
            download_docx.download_button(
                "下载 Word",
                docx.content,
                file_name=f"{case['title']}-分析报告.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

        with st.expander(f"查看引用来源（{len(report['citations'])} 条）"):
            for citation in report["citations"]:
                label = citation.get("reference_label") or "来源"
                location = ""
                if citation.get("page_no"):
                    location = f"第 {citation['page_no']} 页"
                elif citation.get("start_ms") is not None:
                    location = f"{citation['start_ms'] // 1000} 秒"
                st.markdown(f"**[{label}] {citation['title']}**　{location}")
                st.write(citation["excerpt"])
                if citation.get("source_url"):
                    st.link_button("打开官方来源", citation["source_url"])
                st.divider()
