from collections import Counter

from fastapi import APIRouter
from sqlalchemy import select

from app.config import get_settings
from app.db import AuthorityProfile, SessionLocal
from app.schemas import AuthoritySearchRequest, AuthorityStatsResponse, Citation
from app.services.authority import classify_question, rank_authority_results
from app.services.qdrant_store import get_hybrid_store

router = APIRouter(prefix="/authorities", tags=["authorities"])


@router.get("/stats", response_model=AuthorityStatsResponse)
def stats() -> AuthorityStatsResponse:
    with SessionLocal() as session:
        profiles = session.scalars(select(AuthorityProfile)).all()
    return AuthorityStatsResponse(
        documents=len(profiles),
        by_type=dict(Counter(profile.authority_type for profile in profiles)),
        by_domain=dict(Counter(profile.legal_domain for profile in profiles)),
        by_status=dict(Counter(profile.effective_status for profile in profiles)),
    )


@router.post("/search", response_model=list[Citation])
def search(request: AuthoritySearchRequest) -> list[Citation]:
    route = classify_question(request.question)
    candidates = get_hybrid_store(get_settings()).search(
        get_settings().authority_collection,
        request.question,
        [],
        limit=max(request.limit * 3, 12),
        filters={"authority_type": request.authority_type} if request.authority_type else None,
    )
    if request.legal_domain:
        route = route.__class__(
            legal_domain=request.legal_domain, matched_terms=route.matched_terms
        )
    return rank_authority_results(candidates, route, limit=request.limit)
