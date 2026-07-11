from uuid import UUID

from pydantic import BaseModel, Field


class MaterialResponse(BaseModel):
    material_id: UUID
    title: str
    source_type: str
    kind: str
    segments: int
    case_id: UUID | None = None


class Citation(BaseModel):
    material_id: UUID
    title: str
    source_type: str
    excerpt: str
    reference_label: str | None = None
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None
    score: float | None = None
    authority_type: str | None = None
    legal_domain: str | None = None
    effective_status: str | None = None
    source_category: str | None = None
    source_url: str | None = None


class ReportRequest(BaseModel):
    material_id: UUID | None = None
    case_id: UUID | None = None
    question: str = Field(
        default="整理案情、时间线、争议焦点和证据情况，并检索相关权威依据。",
        min_length=2,
        max_length=2000,
    )
    provider: str | None = None
    model: str | None = None


class ReportResponse(BaseModel):
    report_markdown: str
    citations: list[Citation]
    review_task_id: UUID
    status: str
    legal_domain: str


class ReviewDecision(BaseModel):
    approved: bool
    edited_report: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AuthorityStatsResponse(BaseModel):
    documents: int
    by_type: dict[str, int]
    by_domain: dict[str, int]
    by_status: dict[str, int]


class AuthoritySearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    legal_domain: str | None = None
    authority_type: str | None = None
    limit: int = Field(default=6, ge=1, le=20)


class CaseCreate(BaseModel):
    title: str = Field(min_length=2, max_length=256)
    dispute_type: str = Field(default="loan_contract", max_length=64)
    parties: str = Field(default="", max_length=2000)
    narrative: str = Field(default="", max_length=10000)
    request: str = Field(default="", max_length=3000)
    analysis_goal: str | None = Field(default=None, max_length=2000)


class CaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=256)
    confirmed_summary: str | None = Field(default=None, max_length=20000)
    analysis_goal: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(intake|ready|analyzing|completed)$")


class IntakeAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)
    provider: str | None = None
    model: str | None = None


class IntakeResponse(BaseModel):
    case_id: UUID
    complete: bool
    question: str | None = None
    summary: str
    missing_information: list[str]
    turn_no: int
