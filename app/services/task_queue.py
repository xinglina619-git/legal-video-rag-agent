from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal, TaskRecord, utc_now
from app.schemas import TaskStatusResponse
from app.services.observability import emit_event

TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]
TASK_HANDLERS: dict[str, TaskHandler] = {}


def _jsonable(payload: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(payload or {}, ensure_ascii=False, default=str))


def create_task(
    task_type: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    case_id: UUID | None = None,
    material_id: UUID | None = None,
    review_task_id: UUID | None = None,
    max_attempts: int | None = None,
) -> TaskRecord:
    with SessionLocal() as session:
        if idempotency_key:
            existing = session.scalar(
                select(TaskRecord)
                .where(TaskRecord.idempotency_key == idempotency_key)
                .order_by(TaskRecord.created_at.desc())
            )
            if existing and existing.status in {"queued", "running", "retrying", "succeeded"}:
                emit_event(
                    "task.deduplicated",
                    status="success",
                    case_id=existing.case_id,
                    material_id=existing.material_id,
                    review_task_id=existing.review_task_id,
                    metadata={
                        "task_id": str(existing.id),
                        "task_type": existing.task_type,
                        "idempotency_key": idempotency_key,
                    },
                )
                return existing
        task = TaskRecord(
            task_type=task_type,
            status="queued",
            idempotency_key=idempotency_key,
            case_id=case_id,
            material_id=material_id,
            review_task_id=review_task_id,
            payload_json=_jsonable(payload),
            max_attempts=max_attempts or get_settings().task_max_attempts,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    emit_event(
        "task.queued",
        status="queued",
        case_id=case_id,
        material_id=material_id,
        review_task_id=review_task_id,
        metadata={"task_id": str(task_id), "task_type": task_type},
    )
    return task


def get_task_record(task_id: UUID) -> TaskRecord | None:
    with SessionLocal() as session:
        return session.get(TaskRecord, task_id)


def task_response(task: TaskRecord) -> TaskStatusResponse:
    return TaskStatusResponse(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        progress_message=task.progress_message,
        cancel_requested=task.cancel_requested,
        result=task.result_json or {},
        error_type=task.error_type,
        error_message=task.error_message,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def update_task_progress(
    task_id: UUID,
    stage: str,
    progress: int,
    message: str | None = None,
) -> TaskStatusResponse:
    bounded_progress = max(0, min(progress, 100))
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if not task:
            raise LookupError("Task not found")
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task_response(task)
        task.stage = stage
        task.progress = bounded_progress
        task.progress_message = message
        task.updated_at = utc_now()
        session.commit()
        session.refresh(task)
        response = task_response(task)
    emit_event(
        "task.progress",
        status="running",
        metadata={
            "task_id": str(task_id),
            "stage": stage,
            "progress": bounded_progress,
            "message": message,
        },
    )
    return response


def request_task_cancel(task_id: UUID) -> TaskStatusResponse:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if not task:
            raise LookupError("Task not found")
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task_response(task)
        task.cancel_requested = True
        if task.status in {"queued", "retrying"}:
            task.status = "cancelled"
            task.stage = "cancelled"
            task.progress_message = "任务已取消"
            task.finished_at = utc_now()
        task.updated_at = utc_now()
        session.commit()
        session.refresh(task)
        response = task_response(task)
    emit_event(
        "task.cancelled" if response.status == "cancelled" else "task.cancel_requested",
        status=response.status,
        metadata={"task_id": str(task_id), "status": response.status},
    )
    return response


def is_task_cancel_requested(task_id: UUID) -> bool:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        return bool(task and task.cancel_requested)


def run_task(
    task_id: UUID,
    handler: TaskHandler,
    *,
    retry_delay_seconds: float = 0.0,
) -> TaskStatusResponse:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if not task:
            raise LookupError("Task not found")
        payload = dict(task.payload_json or {})
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task_response(task)

    while True:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            if task.cancel_requested:
                task.status = "cancelled"
                task.stage = "cancelled"
                task.progress_message = "任务已取消"
                task.finished_at = utc_now()
                task.updated_at = utc_now()
                session.commit()
                response = task_response(task)
                emit_event("task.cancelled", status="cancelled", metadata={"task_id": str(task_id)})
                return response
            task.status = "running"
            task.attempts += 1
            task.started_at = task.started_at or utc_now()
            task.updated_at = utc_now()
            task.progress_message = f"开始第 {task.attempts} 次尝试"
            session.commit()
            attempt = task.attempts
        emit_event(
            "task.running",
            status="running",
            metadata={"task_id": str(task_id), "attempt": attempt},
        )
        try:
            result = _jsonable(handler({**payload, "_task_id": str(task_id)}))
        except Exception as exc:
            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                assert task is not None
                task.error_type = type(exc).__name__
                task.error_message = str(exc)[:1000]
                task.updated_at = utc_now()
                if task.cancel_requested:
                    task.status = "cancelled"
                    task.stage = "cancelled"
                    task.progress_message = "任务已取消"
                    task.finished_at = utc_now()
                    event_type = "task.cancelled"
                elif task.attempts < task.max_attempts:
                    task.status = "retrying"
                    task.progress_message = f"第 {task.attempts} 次失败，准备重试"
                    event_type = "task.retrying"
                else:
                    task.status = "failed"
                    task.stage = "failed"
                    task.progress_message = "任务失败"
                    task.finished_at = utc_now()
                    event_type = "task.failed"
                session.commit()
                response = task_response(task)
            emit_event(
                event_type,
                status="error" if event_type == "task.failed" else response.status,
                error_type=response.error_type,
                metadata={
                    "task_id": str(task_id),
                    "attempt": response.attempts,
                    "error_message": response.error_message,
                },
            )
            if response.status in {"failed", "cancelled"}:
                return response
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds * response.attempts)
            continue

        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.result_json = result
            task.updated_at = utc_now()
            if task.cancel_requested:
                task.status = "cancelled"
                task.stage = "cancelled"
                task.progress_message = "任务已取消"
                task.finished_at = utc_now()
                event_type = "task.cancelled"
            else:
                task.status = "succeeded"
                task.stage = "completed"
                task.progress = 100
                task.progress_message = "任务处理完成"
                task.finished_at = utc_now()
                if "material_id" in result:
                    task.material_id = UUID(str(result["material_id"]))
                if "review_task_id" in result:
                    task.review_task_id = UUID(str(result["review_task_id"]))
                event_type = "task.succeeded"
            session.commit()
            response = task_response(task)
        emit_event(
            event_type,
            status="success" if event_type == "task.succeeded" else "cancelled",
            metadata={"task_id": str(task_id), "task_type": response.task_type},
        )
        return response


def enqueue_task(
    settings: Settings,
    task_type: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    case_id: UUID | None = None,
) -> TaskStatusResponse:
    task = create_task(
        task_type,
        payload,
        idempotency_key=idempotency_key,
        case_id=case_id,
        max_attempts=settings.task_max_attempts,
    )
    if settings.task_queue_mode == "inline":
        return run_task(
            task.id,
            TASK_HANDLERS[task_type],
            retry_delay_seconds=settings.task_retry_delay_seconds,
        )
    if settings.task_queue_mode == "rq":
        try:
            from redis import Redis
            from rq import Queue
        except ImportError as exc:
            raise RuntimeError("RQ mode requires optional dependencies: redis and rq") from exc
        queue = Queue(settings.task_queue_name, connection=Redis.from_url(settings.redis_url))
        queue.enqueue("app.services.task_queue.run_task_by_id", str(task.id))
        return task_response(task)
    raise ValueError(f"Unsupported TASK_QUEUE_MODE: {settings.task_queue_mode}")


def run_task_by_id(task_id: str) -> TaskStatusResponse:
    record = get_task_record(UUID(task_id))
    if not record:
        raise LookupError("Task not found")
    handler = TASK_HANDLERS[record.task_type]
    return run_task(
        record.id,
        handler,
        retry_delay_seconds=get_settings().task_retry_delay_seconds,
    )


def run_material_ingest_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.cases import attach_material
    from app.services.ingestion import ingest

    settings = get_settings()
    task_id = UUID(str(payload["_task_id"])) if payload.get("_task_id") else None
    if task_id:
        update_task_progress(task_id, "normalizing", 10, "正在解析、转写或 OCR 材料")
    response = ingest(
        Path(str(payload["path"])),
        str(payload["kind"]),
        settings,
        display_name=payload.get("display_name"),
    )
    if task_id:
        update_task_progress(task_id, "indexing", 80, "正在写入检索索引")
    case_id = payload.get("case_id")
    if case_id and response.kind == "evidence":
        attach_material(UUID(str(case_id)), response.material_id)
        response.case_id = UUID(str(case_id))
    return response.model_dump(mode="json")


def run_report_generation_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.agent import run_report
    from app.services.cases import (
        analysis_context_for_case,
        ensure_intake_material,
        link_report,
        material_ids_for_case,
    )

    settings = get_settings()
    task_id = UUID(str(payload["_task_id"])) if payload.get("_task_id") else None
    if task_id:
        update_task_progress(task_id, "generating", 20, "正在检索证据并生成报告")
    case_id = UUID(str(payload["case_id"])) if payload.get("case_id") else None
    material_id = UUID(str(payload["material_id"])) if payload.get("material_id") else None
    material_ids = None
    retrieval_query = None
    legal_domain = None
    if case_id:
        ensure_intake_material(settings, case_id)
        material_ids = material_ids_for_case(case_id)
        legal_domain, case_summary = analysis_context_for_case(case_id)
        retrieval_query = f"{payload['question']}\n{case_summary}"
    result = run_report(
        settings,
        material_id,
        str(payload["question"]),
        payload.get("provider"),
        payload.get("model"),
        material_ids=material_ids,
        retrieval_query=retrieval_query,
        legal_domain=legal_domain,
    )
    if case_id:
        link_report(case_id, result.review_task_id)
    if task_id:
        update_task_progress(task_id, "waiting_review", 90, "报告草稿已生成，等待人工复核")
    return result.model_dump(mode="json")


TASK_HANDLERS.update(
    {
        "material_ingest": run_material_ingest_task,
        "report_generation": run_report_generation_task,
    }
)
