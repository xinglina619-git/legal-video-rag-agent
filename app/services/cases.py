import json
import re
from uuid import UUID

from sqlalchemy import delete, select

from app.config import Settings
from app.db import CaseMaterial, CaseRecord, CaseReport, IntakeMessage, Material, SessionLocal
from app.schemas import CaseCreate, CaseUpdate, IntakeResponse
from app.services.ingestion import ingest
from app.services.llm import get_chat_model_with_fallback
from app.services.privacy import redact_sensitive_text

MAX_INTAKE_TURNS = 8

DISPUTE_LEGAL_DOMAINS = {
    "loan_contract": "civil_contract",
    "labor": "labor",
    "marriage_family": "marriage_family",
    "general": "general",
}

FALLBACK_QUESTIONS = {
    "loan_contract": [
        "请说明借款发生的时间、金额以及款项是如何交付的。",
        "双方是否约定利息、还款日期或分期方式？",
        "目前对方已经偿还多少，还有多少未偿还？",
        "你有哪些借条、合同、转账记录或聊天记录？",
        "你是否催要过？对方如何回复？",
        "双方是否存在现金交付、砍头息、担保或其他特殊情况？",
        "你希望通过协商、调解还是诉讼解决？",
        "还有哪些可能影响判断的重要事实需要补充？",
    ],
    "labor": [
        "请说明入职时间、工作岗位、工资标准和实际用工单位。",
        "是否签订劳动合同、缴纳社保？",
        "纠纷发生的时间和具体经过是什么？",
        "你有哪些工资记录、考勤、聊天记录或解除通知？",
        "目前欠付金额或主要争议是什么？",
        "是否已经协商、投诉或申请劳动仲裁？",
        "你希望获得什么处理结果？",
        "还有哪些重要事实需要补充？",
    ],
    "marriage_family": [
        "请说明双方关系、登记时间以及当前是否分居。",
        "主要争议涉及离婚、财产、债务还是子女抚养？",
        "共同财产和可能的共同债务有哪些？",
        "子女年龄、主要照护情况和双方抚养条件如何？",
        "你有哪些能够支持陈述的材料？",
        "双方是否协商过？目前分歧是什么？",
        "你希望获得什么处理结果？",
        "还有哪些重要事实需要补充？",
    ],
}


def _questions(dispute_type: str) -> list[str]:
    return FALLBACK_QUESTIONS.get(
        dispute_type,
        [
            "请说明事情发生的时间、地点和主要经过。",
            "纠纷双方是什么关系？",
            "双方目前的核心分歧是什么？",
            "你有哪些能够支持陈述的材料？",
            "是否已经协商、投诉、调解或诉讼？",
            "你希望获得什么处理结果？",
            "还有哪些事实需要核实？",
            "还有其他重要情况需要补充吗？",
        ],
    )


def _case_payload(case: CaseRecord) -> dict:
    with SessionLocal() as session:
        materials = session.execute(
            select(CaseMaterial, Material)
            .join(Material, Material.id == CaseMaterial.material_id)
            .where(CaseMaterial.case_id == case.id)
            .order_by(CaseMaterial.created_at)
        ).all()
        messages = session.scalars(
            select(IntakeMessage)
            .where(IntakeMessage.case_id == case.id)
            .order_by(IntakeMessage.created_at)
        ).all()
        reports = session.scalars(
            select(CaseReport)
            .where(CaseReport.case_id == case.id)
            .order_by(CaseReport.created_at.desc())
        ).all()
    return {
        "case_id": str(case.id),
        "title": case.title,
        "dispute_type": case.dispute_type,
        "status": case.status,
        "basic_facts": case.basic_facts,
        "intake_summary": case.intake_summary,
        "confirmed_summary": case.confirmed_summary,
        "missing_information": case.missing_information,
        "analysis_goal": case.analysis_goal,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "materials": [
            {
                "material_id": str(material.id),
                "title": material.title,
                "source_type": material.source_type,
                "status": material.status,
                "role": link.role,
            }
            for link, material in materials
        ],
        "messages": [
            {"role": message.role, "content": message.content, "turn_no": message.turn_no}
            for message in messages
        ],
        "report_ids": [str(item.review_task_id) for item in reports],
    }


def create_case(payload: CaseCreate) -> dict:
    facts = {
        "parties": payload.parties,
        "narrative": payload.narrative,
        "request": payload.request,
    }
    with SessionLocal() as session:
        case = CaseRecord(
            title=payload.title,
            dispute_type=payload.dispute_type,
            basic_facts=facts,
            analysis_goal=payload.analysis_goal
            or "整理案情、时间线、争议焦点和证据情况，并检索相关权威依据。",
            missing_information=["时间与金额", "约定内容", "证据材料", "对方意见"],
        )
        session.add(case)
        session.flush()
        session.add(
            IntakeMessage(
                case_id=case.id,
                role="assistant",
                content=_questions(payload.dispute_type)[0],
                turn_no=0,
            )
        )
        session.commit()
        session.refresh(case)
    return _case_payload(case)


def list_cases() -> list[dict]:
    with SessionLocal() as session:
        cases = session.scalars(
            select(CaseRecord)
            .where(CaseRecord.owner_id == "local-user")
            .order_by(CaseRecord.updated_at.desc())
        ).all()
    return [_case_payload(case) for case in cases]


def get_case(case_id: UUID) -> dict:
    with SessionLocal() as session:
        case = session.get(CaseRecord, case_id)
        if not case:
            raise LookupError("案件不存在")
    return _case_payload(case)


def update_case(case_id: UUID, payload: CaseUpdate) -> dict:
    with SessionLocal() as session:
        case = session.get(CaseRecord, case_id)
        if not case:
            raise LookupError("案件不存在")
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(case, field, value)
        if payload.confirmed_summary is not None:
            case.status = "ready"
            session.execute(
                delete(CaseMaterial).where(
                    CaseMaterial.case_id == case_id, CaseMaterial.role == "intake"
                )
            )
        session.commit()
        session.refresh(case)
    return _case_payload(case)


def attach_material(case_id: UUID, material_id: UUID, role: str = "evidence") -> None:
    with SessionLocal() as session:
        if not session.get(CaseRecord, case_id):
            raise LookupError("案件不存在")
        existing = session.scalar(
            select(CaseMaterial).where(
                CaseMaterial.case_id == case_id, CaseMaterial.material_id == material_id
            )
        )
        if not existing:
            session.add(CaseMaterial(case_id=case_id, material_id=material_id, role=role))
            session.commit()


def material_ids_for_case(case_id: UUID) -> list[UUID]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(CaseMaterial.material_id)
                .where(CaseMaterial.case_id == case_id)
                .order_by(CaseMaterial.created_at)
            ).all()
        )


def analysis_context_for_case(case_id: UUID) -> tuple[str, str]:
    with SessionLocal() as session:
        case = session.get(CaseRecord, case_id)
        if not case:
            raise LookupError("案件不存在")
        summary = case.confirmed_summary or case.intake_summary
        if not summary:
            summary = "\n".join(str(value) for value in case.basic_facts.values() if value)
        return DISPUTE_LEGAL_DOMAINS.get(case.dispute_type, "general"), summary


def _history_text(case_id: UUID) -> str:
    with SessionLocal() as session:
        messages = session.scalars(
            select(IntakeMessage)
            .where(IntakeMessage.case_id == case_id)
            .order_by(IntakeMessage.created_at)
        ).all()
    return "\n".join(f"{item.role}: {item.content}" for item in messages)


def _parse_json(content: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise ValueError("模型未返回有效的问答状态")
        return json.loads(match.group(0))


def answer_intake(
    settings: Settings,
    case_id: UUID,
    answer: str,
    provider: str | None,
    model: str | None,
) -> IntakeResponse:
    with SessionLocal() as session:
        case = session.get(CaseRecord, case_id)
        if not case:
            raise LookupError("案件不存在")
        turn_no = (
            session.query(IntakeMessage)
            .filter(IntakeMessage.case_id == case_id, IntakeMessage.role == "user")
            .count()
            + 1
        )
        session.add(
            IntakeMessage(case_id=case_id, role="user", content=answer, turn_no=turn_no)
        )
        session.commit()

    history = _history_text(case_id)
    complete = turn_no >= MAX_INTAKE_TURNS
    question = None if complete else _questions(case.dispute_type)[min(turn_no, 7)]
    summary = (
        f"案件名称：{case.title}\n纠纷类型：{case.dispute_type}\n"
        f"基础信息：{json.dumps(case.basic_facts, ensure_ascii=False)}\n问答记录：\n{history}"
    )
    missing = [] if complete else _questions(case.dispute_type)[turn_no + 1 : 5]

    prompt = f"""你是面向普通当事人的法律纠纷信息采集助手，不提供法律结论。
根据基础信息和问答记录，判断生成分析报告所需的事实是否足够。最多追问 {MAX_INTAKE_TURNS} 轮，
每次只问一个最重要、用户容易回答的问题，不重复提问。
仅返回 JSON：
{{"complete": false, "question": "下一问题或null", "summary": "客观案情摘要",
"missing_information": ["仍缺失的信息"]}}
基础信息：{json.dumps(case.basic_facts, ensure_ascii=False)}
纠纷类型：{case.dispute_type}
当前第 {turn_no} 轮
问答记录：
{redact_sensitive_text(history)}
"""
    try:
        result = get_chat_model_with_fallback(settings, provider, model).invoke(prompt)
        parsed = _parse_json(str(result.content))
        complete = bool(parsed.get("complete")) or complete
        question = None if complete else str(parsed.get("question") or question)
        summary = str(parsed.get("summary") or summary)
        missing = [str(item) for item in parsed.get("missing_information", missing)]
    except Exception:
        # Intake remains useful offline; the UI can transparently continue with curated questions.
        pass

    with SessionLocal() as session:
        case = session.get(CaseRecord, case_id)
        assert case is not None
        case.intake_summary = summary
        case.missing_information = missing
        session.execute(
            delete(CaseMaterial).where(
                CaseMaterial.case_id == case_id, CaseMaterial.role == "intake"
            )
        )
        if complete:
            case.status = "ready"
        if question:
            session.add(
                IntakeMessage(
                    case_id=case_id,
                    role="assistant",
                    content=question,
                    turn_no=turn_no,
                )
            )
        session.commit()

    return IntakeResponse(
        case_id=case_id,
        complete=complete,
        question=question,
        summary=summary,
        missing_information=missing,
        turn_no=turn_no,
    )


def ensure_intake_material(settings: Settings, case_id: UUID) -> UUID:
    with SessionLocal() as session:
        existing = session.scalar(
            select(CaseMaterial).where(
                CaseMaterial.case_id == case_id, CaseMaterial.role == "intake"
            )
        )
        if existing:
            return existing.material_id
        case = session.get(CaseRecord, case_id)
        if not case:
            raise LookupError("案件不存在")
        summary = case.confirmed_summary or case.intake_summary
        if not summary:
            facts = case.basic_facts
            summary = "\n\n".join(str(value) for value in facts.values() if value)
        if not summary:
            raise ValueError("请先填写案情或完成至少一轮问答")
        title = case.title

    path = settings.storage_dir / "cases" / str(case_id) / "intake.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{summary}", encoding="utf-8")
    response = ingest(path, "evidence", settings, display_name=f"{title}-问答案情.md")
    attach_material(case_id, response.material_id, role="intake")
    return response.material_id


def link_report(case_id: UUID, review_task_id: UUID) -> None:
    with SessionLocal() as session:
        session.add(CaseReport(case_id=case_id, review_task_id=review_task_id))
        case = session.get(CaseRecord, case_id)
        if case and case.status != "intake":
            case.status = "completed"
        session.commit()
