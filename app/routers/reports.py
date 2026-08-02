from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import get_settings
from sqlalchemy import select

from app.db import ReportCitation, ReviewTask, SessionLocal
from app.schemas import ReportRequest, ReportResponse, TaskStatusResponse
from app.services.agent import run_report
from app.services.cases import (
    analysis_context_for_case,
    ensure_intake_material,
    link_report,
    material_ids_for_case,
)
from app.services.report_export import render_report_docx, render_report_pdf
from app.services.task_queue import enqueue_task

router = APIRouter(tags=["report"])


@router.post("/reports", response_model=ReportResponse | TaskStatusResponse)
def report(request: ReportRequest):
    try:
        if request.background:
            return enqueue_task(
                get_settings(),
                "report_generation",
                request.model_dump(mode="json"),
                idempotency_key=f"report:{request.case_id or request.material_id}:{request.question}",
                case_id=request.case_id,
            )
        material_ids = None
        retrieval_query = None
        legal_domain = None
        if request.case_id:
            ensure_intake_material(get_settings(), request.case_id)
            material_ids = material_ids_for_case(request.case_id)
            legal_domain, case_summary = analysis_context_for_case(request.case_id)
            retrieval_query = f"{request.question}\n{case_summary}"
        result = run_report(
            get_settings(),
            request.material_id,
            request.question,
            request.provider,
            request.model,
            material_ids=material_ids,
            retrieval_query=retrieval_query,
            legal_domain=legal_domain,
        )
        if request.case_id:
            link_report(request.case_id, result.review_task_id)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports/{task_id}")
def saved_report(task_id: UUID):
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Review task not found")
        citations = session.scalars(
            select(ReportCitation)
            .where(ReportCitation.review_task_id == task_id)
            .order_by(ReportCitation.position)
        ).all()
        return {
            "review_task_id": str(task.id),
            "report_markdown": task.report_markdown,
            "status": task.status,
            "citations": [item.citation_json for item in citations],
            "legal_domain": "saved",
        }


@router.get("/reports/{task_id}/pdf")
def export_pdf(task_id: UUID):
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Review task not found")
        content = render_report_pdf(task.report_markdown)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="legal-review-{task_id}.pdf"'},
    )


@router.get("/reports/{task_id}/docx")
def export_docx(task_id: UUID):
    with SessionLocal() as session:
        task = session.get(ReviewTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Review task not found")
        content = render_report_docx(task.report_markdown)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="legal-review-{task_id}.docx"'},
    )
