from __future__ import annotations

import json
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
            if existing and existing.status in {"queued", "running", "succeeded"}:
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
        result=task.result_json or {},
        error_type=task.error_type,
        error_message=task.error_message,
        attempts=task.attempts,
    )


def run_task(task_id: UUID, handler: TaskHandler) -> TaskStatusResponse:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if not task:
            raise LookupError("Task not found")
        task.status = "running"
        task.attempts += 1
        task.started_at = utc_now()
        task.updated_at = utc_now()
        payload = dict(task.payload_json or {})
        session.commit()
    emit_event("task.running", status="running", metadata={"task_id": str(task_id)})
    try:
        result = _jsonable(handler(payload))
    except Exception as exc:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.status = "failed"
            task.error_type = type(exc).__name__
            task.error_message = str(exc)[:1000]
            task.finished_at = utc_now()
            task.updated_at = utc_now()
            session.commit()
            response = task_response(task)
        emit_event(
            "task.failed",
            status="error",
            error_type=response.error_type,
            metadata={"task_id": str(task_id), "error_message": response.error_message},
        )
        return response
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.status = "succeeded"
        task.result_json = result
        task.finished_at = utc_now()
        task.updated_at = utc_now()
        if "material_id" in result:
            task.material_id = UUID(str(result["material_id"]))
        if "review_task_id" in result:
            task.review_task_id = UUID(str(result["review_task_id"]))
        session.commit()
        response = task_response(task)
    emit_event(
        "task.succeeded",
        status="success",
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
        return run_task(task.id, TASK_HANDLERS[task_type])
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
    return run_task(record.id, handler)


def run_material_ingest_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.cases import attach_material
    from app.services.ingestion import ingest

    settings = get_settings()
    response = ingest(
        Path(str(payload["path"])),
        str(payload["kind"]),
        settings,
        display_name=payload.get("display_name"),
    )
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
    return result.model_dump(mode="json")


TASK_HANDLERS.update(
    {
        "material_ingest": run_material_ingest_task,
        "report_generation": run_report_generation_task,
    }
)
