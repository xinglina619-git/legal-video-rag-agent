from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.schemas import CaseCreate, CaseUpdate, IntakeAnswer
from app.services.cases import answer_intake, create_case, get_case, list_cases, update_case

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("")
def all_cases():
    return list_cases()


@router.post("")
def new_case(payload: CaseCreate):
    return create_case(payload)


@router.get("/{case_id}")
def one_case(case_id: UUID):
    try:
        return get_case(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{case_id}")
def edit_case(case_id: UUID, payload: CaseUpdate):
    try:
        return update_case(case_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{case_id}/intake")
def intake(case_id: UUID, payload: IntakeAnswer):
    try:
        return answer_intake(
            get_settings(), case_id, payload.answer, payload.provider, payload.model
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
