from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from app.config import get_settings


def json_type():
    return JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    file_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="normalized")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class MaterialVersion(Base):
    __tablename__ = "material_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(index=True)
    source_path: Mapped[str] = mapped_column(Text, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(32), default="normalizing")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    transitioned_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuthorityProfile(Base):
    __tablename__ = "authority_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(index=True, unique=True)
    authority_type: Mapped[str] = mapped_column(String(64), default="other")
    legal_domain: Mapped[str] = mapped_column(String(64), default="general")
    jurisdiction: Mapped[str] = mapped_column(String(64), default="CN")
    effective_status: Mapped[str] = mapped_column(String(32), default="unknown")
    effective_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_category: Mapped[str] = mapped_column(String(256), default="uncategorized")
    version_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuthoritySyncState(Base):
    __tablename__ = "authority_sync_states"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_path: Mapped[str] = mapped_column(Text, unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    material_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="indexed")
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(index=True)
    report_markdown: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class CaseRecord(Base):
    __tablename__ = "case_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(64), default="local-user", index=True)
    title: Mapped[str] = mapped_column(String(256))
    dispute_type: Mapped[str] = mapped_column(String(64), default="general")
    status: Mapped[str] = mapped_column(String(32), default="intake")
    basic_facts: Mapped[dict] = mapped_column(json_type(), default=dict)
    intake_summary: Mapped[str] = mapped_column(Text, default="")
    confirmed_summary: Mapped[str] = mapped_column(Text, default="")
    missing_information: Mapped[list] = mapped_column(json_type(), default=list)
    analysis_goal: Mapped[str] = mapped_column(
        Text,
        default="整理案情、时间线、争议焦点和证据情况，并检索相关权威依据。",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class CaseMaterial(Base):
    __tablename__ = "case_materials"
    __table_args__ = (UniqueConstraint("case_id", "material_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(index=True)
    material_id: Mapped[UUID] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(32), default="evidence")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class IntakeMessage(Base):
    __tablename__ = "intake_messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    turn_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class CaseReport(Base):
    __tablename__ = "case_reports"
    __table_args__ = (UniqueConstraint("review_task_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(index=True)
    review_task_id: Mapped[UUID] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ReportCitation(Base):
    __tablename__ = "report_citations"
    __table_args__ = (UniqueConstraint("review_task_id", "position"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_task_id: Mapped[UUID] = mapped_column(index=True)
    position: Mapped[int] = mapped_column(Integer)
    citation_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AppEvent(Base):
    __tablename__ = "app_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    case_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    material_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    review_task_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    safe_metadata: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class TaskRecord(Base):
    __tablename__ = "task_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    case_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    material_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    review_task_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    payload_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    result_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    error_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


settings = get_settings()
engine = create_engine(
    settings.database_url,
    future=True,
    connect_args={"timeout": 60} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
