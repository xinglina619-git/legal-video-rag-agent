import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.config import get_settings
from app.schemas import MaterialResponse, ReviewDecision, TaskStatusResponse
from app.services.agent import resume_review
from app.services.cases import attach_material
from app.services.ingestion import ingest, list_materials
from app.services.task_queue import enqueue_task

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("")
def materials(kind: str | None = Query(default=None)):
    return [
        {
            "material_id": str(item.id),
            "title": item.title,
            "source_type": item.source_type,
            "kind": item.kind,
            "status": item.status,
        }
        for item in list_materials(kind)
    ]


@router.post("/ingest", response_model=MaterialResponse | TaskStatusResponse)
async def upload(
    kind: str = Query(pattern="^(evidence|authority)$"),
    case_id: UUID | None = Query(default=None),
    background: bool = Query(default=False),
    file: UploadFile = File(...),
):
    settings = get_settings()
    source_name = Path(file.filename or "upload.txt").name
    target = settings.storage_dir / "uploads" / f"{uuid4().hex}_{source_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    file_content = await file.read()
    target.write_bytes(file_content)
    if background:
        content_hash = hashlib.sha256(file_content).hexdigest()
        return enqueue_task(
            settings,
            "material_ingest",
            {
                "path": str(target),
                "kind": kind,
                "display_name": source_name,
                "case_id": str(case_id) if case_id else None,
            },
            idempotency_key=f"upload:{kind}:{case_id or 'global'}:{source_name}:{content_hash}",
            case_id=case_id,
        )
    try:
        response = ingest(target, kind, settings, display_name=source_name)
        if case_id and kind == "evidence":
            attach_material(case_id, response.material_id)
            response.case_id = case_id
        return response
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reviews/{task_id}")
def review(task_id: UUID, decision: ReviewDecision):
    try:
        return resume_review(get_settings(), task_id, decision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
