from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.db import SessionLocal, TaskRecord
from app.schemas import TaskStatusResponse
from app.services.task_queue import request_task_cancel, task_response

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
def task_status(task_id: UUID):
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task_response(task)


@router.post("/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(task_id: UUID):
    try:
        return request_task_cancel(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
