import sqlite3
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import TypedDict
from uuid import UUID

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.config import Settings
from app.db import ReportCitation, ReviewTask, SessionLocal
from app.schemas import Citation, ReportResponse, ReviewDecision
from app.services.authority import QuestionRoute, classify_question, rank_authority_results
from app.services.llm import get_chat_model_with_fallback
from app.services.official_search import search_official_sources
from app.services.observability import emit_event
from app.services.privacy import redact_sensitive_text
from app.services.qdrant_store import get_hybrid_store

PROMPT = """你是面向普通当事人的法律纠纷材料分析助手。本报告用于信息整理与咨询准备，
不是正式法律意见，也不得预测胜诉率。
只能使用给定的案情材料和权威依据，不得编造人物、日期、金额、证据、法条或结论。
明确区分“当事人陈述”“材料能够支持的内容”“仍待核实的信息”。
状态为 unknown 或 repealed 的依据必须提示人工核验有效性。
正文引用本案材料时使用 [证据N]，引用权威依据时使用 [依据N]，编号必须来自下方材料标签。
使用通俗中文 Markdown，依次输出：
免责声明；案情摘要；事件时间线；核心争议点；现有材料与证明作用；
仍需补充或核实的信息；相关法律依据（逐项引用来源）；初步分析；
可操作的下一步建议（补证、协商、调解、投诉或咨询律师）；来源清单。
不得输出胜诉概率，不得把初步分析写成确定法律结论。
分析目标：{question}\n\n识别领域：{legal_domain}\n\n案情与证据：\n{evidence}\n\n权威依据：\n{authority}"""


class AgentState(TypedDict):
    question: str
    retrieval_query: str
    material_id: UUID
    material_ids: list[UUID]
    provider: str | None
    model: str | None
    route: dict
    evidence: list[dict]
    authorities: list[dict]
    draft: str
    approved: bool
    review_notes: str


def format_citations_for_prompt(items: list[dict], prefix: str) -> str:
    if not items:
        return "No retrieved material."
    formatted = []
    for index, raw_item in enumerate(items, start=1):
        item = Citation.model_validate(raw_item).model_copy(
            update={"reference_label": f"{prefix}{index}"}
        )
        metadata = []
        if item.authority_type:
            metadata.append(item.authority_type)
        if item.effective_status:
            metadata.append(f"status={item.effective_status}")
        if item.source_url:
            metadata.append(item.source_url)
        label = " | ".join([f"[{item.reference_label}] {item.title}", *metadata])
        formatted.append(f"[{label}]\n{item.excerpt}")
    return "\n\n".join(formatted)


def build_labeled_citations(evidence: list[dict], authorities: list[dict]) -> list[Citation]:
    labeled: list[Citation] = []
    for prefix, items in (("证据", evidence), ("依据", authorities)):
        for index, raw_item in enumerate(items, start=1):
            labeled.append(
                Citation.model_validate(raw_item).model_copy(
                    update={"reference_label": f"{prefix}{index}"}
                )
            )
    return labeled


@lru_cache
def _checkpointer(storage_dir: str) -> SqliteSaver:
    path = Path(storage_dir) / "langgraph_checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    saver.setup()
    return saver


def _build_graph(settings: Settings):
    store = get_hybrid_store(settings)

    def evidence_node(state: AgentState):
        started = perf_counter()
        material_ids = state.get("material_ids") or [state["material_id"]]
        query = state.get("retrieval_query") or state["question"]
        found = store.search(settings.evidence_collection, query, material_ids)
        emit_event(
            "retrieval.completed",
            status="success",
            duration_ms=int((perf_counter() - started) * 1000),
            provider=state["provider"],
            model=state["model"],
            metadata={
                "collection": settings.evidence_collection,
                "result_count": len(found),
                "material_count": len(material_ids),
                "top_score": found[0].score if found else None,
            },
        )
        return {"evidence": [item.model_dump(mode="json") for item in found]}

    def authority_node(state: AgentState):
        started = perf_counter()
        query = state.get("retrieval_query") or state["question"]
        candidates = store.search(settings.authority_collection, query, [], limit=16)
        route = QuestionRoute(**state["route"])
        authorities = rank_authority_results(candidates, route, limit=6)
        if settings.official_web_search_enabled and len(authorities) < 3:
            try:
                authorities.extend(
                    search_official_sources(
                        query,
                        settings.official_web_search_limit,
                        settings.official_web_search_timeout_seconds,
                    )
                )
            except Exception:
                pass
        emit_event(
            "retrieval.completed",
            status="success",
            duration_ms=int((perf_counter() - started) * 1000),
            provider=state["provider"],
            model=state["model"],
            metadata={
                "collection": settings.authority_collection,
                "candidate_count": len(candidates),
                "result_count": len(authorities[:6]),
                "legal_domain": route.legal_domain,
                "top_score": authorities[0].score if authorities else None,
            },
        )
        return {
            "authorities": [
                item.model_dump(mode="json") for item in authorities[:6]
            ]
        }

    def draft_node(state: AgentState):
        started = perf_counter()
        result = get_chat_model_with_fallback(
            settings, state["provider"], state["model"]
        ).invoke(
            PROMPT.format(
                question=state["question"],
                legal_domain=str(state["route"]["legal_domain"]),
                evidence=redact_sensitive_text(
                    format_citations_for_prompt(state["evidence"], "证据")
                ),
                authority=format_citations_for_prompt(state["authorities"], "依据"),
            )
        )
        draft = str(result.content)
        emit_event(
            "report.generated",
            status="success",
            duration_ms=int((perf_counter() - started) * 1000),
            provider=state["provider"],
            model=state["model"],
            metadata={
                "draft_chars": len(draft),
                "evidence_count": len(state["evidence"]),
                "authority_count": len(state["authorities"]),
            },
        )
        return {"draft": draft}

    def review_node(state: AgentState):
        decision = interrupt(
            {
                "type": "human_review",
                "draft": state["draft"],
                "legal_domain": state["route"]["legal_domain"],
                "message": "Approve, reject, or edit the evidence-based draft.",
            }
        )
        if not isinstance(decision, dict):
            raise ValueError("Human review decision must be an object.")
        edited = str(decision.get("edited_report") or state["draft"])
        return {
            "draft": edited,
            "approved": bool(decision.get("approved")),
            "review_notes": str(decision.get("notes") or ""),
        }

    graph = StateGraph(AgentState)
    graph.add_node("evidence", evidence_node)
    graph.add_node("authority", authority_node)
    graph.add_node("draft", draft_node)
    graph.add_node("human_review", review_node)
    graph.add_edge(START, "evidence")
    graph.add_edge("evidence", "authority")
    graph.add_edge("authority", "draft")
    graph.add_edge("draft", "human_review")
    graph.add_edge("human_review", END)
    return graph.compile(checkpointer=_checkpointer(str(settings.storage_dir.resolve())))


def run_report(
    settings: Settings,
    material_id: UUID | None,
    question: str,
    provider: str | None,
    model: str | None,
    material_ids: list[UUID] | None = None,
    retrieval_query: str | None = None,
    legal_domain: str | None = None,
) -> ReportResponse:
    started = perf_counter()
    selected_ids = material_ids or ([material_id] if material_id else [])
    if not selected_ids:
        raise ValueError("至少需要一份问答案情或案件材料")
    search_query = retrieval_query or question
    route = classify_question(search_query)
    if legal_domain and legal_domain != "general":
        route = QuestionRoute(legal_domain=legal_domain, matched_terms=route.matched_terms)
    with SessionLocal() as session:
        task = ReviewTask(material_id=selected_ids[0], report_markdown="", status="generating")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    emit_event(
        "report.started",
        status="started",
        review_task_id=task_id,
        provider=provider,
        model=model,
        metadata={"material_count": len(selected_ids), "question_chars": len(question)},
    )

    graph = _build_graph(settings)
    config = {"configurable": {"thread_id": str(task_id)}}
    graph.invoke(
        {
            "question": question,
            "retrieval_query": search_query,
            "material_id": material_id,
            "material_ids": selected_ids,
            "provider": provider,
            "model": model,
            "route": {
                "legal_domain": route.legal_domain,
                "matched_terms": route.matched_terms,
            },
            "evidence": [],
            "authorities": [],
            "draft": "",
            "approved": False,
            "review_notes": "",
        },
        config,
    )
    snapshot = graph.get_state(config)
    state = snapshot.values
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        assert task is not None
        task.report_markdown = state["draft"]
        task.status = "pending"
        response_citations = build_labeled_citations(state["evidence"], state["authorities"])
        session.add_all(
            [
                ReportCitation(
                    review_task_id=task_id,
                    position=index,
                    citation_json=citation.model_dump(mode="json"),
                )
                for index, citation in enumerate(response_citations)
            ]
        )
        session.commit()
    emit_event(
        "report.generated",
        status="success",
        review_task_id=task_id,
        duration_ms=int((perf_counter() - started) * 1000),
        provider=provider,
        model=model,
        metadata={
            "citation_count": len(response_citations),
            "legal_domain": route.legal_domain,
            "report_chars": len(state["draft"]),
        },
    )
    return ReportResponse(
        report_markdown=state["draft"],
        citations=response_citations,
        review_task_id=task_id,
        status="pending",
        legal_domain=route.legal_domain,
    )


def resume_review(settings: Settings, task_id: UUID, decision: ReviewDecision) -> dict:
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if not task:
            raise LookupError("Review task not found")
        if task.status != "pending":
            raise ValueError(f"Review task is already {task.status}")
    graph = _build_graph(settings)
    config = {"configurable": {"thread_id": str(task_id)}}
    graph.invoke(
        Command(
            resume={
                "approved": decision.approved,
                "edited_report": decision.edited_report,
                "notes": decision.notes,
            }
        ),
        config,
    )
    state = graph.get_state(config).values
    status = "approved" if state["approved"] else "rejected"
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        assert task is not None
        task.status = status
        task.report_markdown = state["draft"]
        session.commit()
        emit_event(
            f"review.{status}",
            status=status,
            review_task_id=task_id,
            metadata={
                "notes_chars": len(decision.notes or ""),
                "edited_report_chars": len(decision.edited_report or ""),
            },
        )
        return {
            "task_id": str(task.id),
            "status": task.status,
            "report_markdown": task.report_markdown,
        }
