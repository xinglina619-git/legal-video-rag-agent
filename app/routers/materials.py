from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.config import get_settings
from app.schemas import MaterialResponse, ReviewDecision
from app.services.agent import resume_review
from app.services.cases import attach_material
from app.services.ingestion import ingest, list_materials

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


@router.post("/ingest", response_model=MaterialResponse)
async def upload(
    kind: str = Query(pattern="^(evidence|authority)$"),
    case_id: UUID | None = Query(default=None),
    file: UploadFile = File(...),
):
    settings = get_settings()
    source_name = Path(file.filename or "upload.txt").name
    target = settings.storage_dir / "uploads" / f"{uuid4().hex}_{source_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
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
