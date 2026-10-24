from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.schemas import CaseCreate, Citation, QualityGateAssessment, ReviewDecision
from app.services import agent, cases, ingestion


def isolated_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_case_create_and_intake_emit_events(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(cases, "SessionLocal", sessions)
    monkeypatch.setattr(cases, "emit_event", lambda event_type, **kwargs: events.append((event_type, kwargs)))

    payload = cases.create_case(
        CaseCreate(
            title="借款纠纷",
            dispute_type="loan_contract",
            parties="甲与乙",
            narrative="乙借款未还",
            request="归还本金",
        )
    )

    class IntakeModel:
        def invoke(self, _prompt):
            return SimpleNamespace(
                content='{"complete": true, "question": null, "summary": "借款纠纷摘要", "missing_information": []}'
            )

    monkeypatch.setattr(cases, "get_chat_model_with_fallback", lambda *_args: IntakeModel())
    cases.answer_intake(Settings(storage_dir=tmp_path), UUID(payload["case_id"]), "借款 8 万", None, None)

    assert "case.created" in [item[0] for item in events]
    assert "intake.answered" in [item[0] for item in events]


def test_ingest_emits_material_events(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    path = tmp_path / "material.md"
    path.write_text("借款事实\n\n转账记录", encoding="utf-8")

    class Store:
        def upsert(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(ingestion, "SessionLocal", sessions)
    monkeypatch.setattr(ingestion, "emit_event", lambda event_type, **kwargs: events.append((event_type, kwargs)))
    monkeypatch.setattr(ingestion, "next_version", lambda _path: 1)
    monkeypatch.setattr(ingestion, "create_version", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingestion, "transition_material", lambda *_args, **_kwargs: None)

    ingestion.ingest(path, "evidence", Settings(storage_dir=tmp_path), store=Store())

    assert "material.normalized" in [item[0] for item in events]
    assert "material.indexed" in [item[0] for item in events]


def test_report_and_review_emit_events(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    material_id = uuid4()
    events = []

    class Store:
        def search(self, collection, *_args, **_kwargs):
            if collection == "legal_evidence":
                return [
                    Citation(
                        material_id=material_id,
                        title="证据",
                        source_type="text",
                        excerpt="李某承认借款。",
                    )
                ]
            return [
                Citation(
                    material_id=uuid4(),
                    title="依据",
                    source_type="text",
                    excerpt="借款人应返还借款。",
                    legal_domain="civil_contract",
                    effective_status="effective",
                )
            ]

    class Model:
        def invoke(self, _prompt):
            return SimpleNamespace(content="# 报告\n\n引用 [证据1] 和 [依据1]。")

    monkeypatch.setattr(agent, "SessionLocal", sessions)
    monkeypatch.setattr(agent, "get_hybrid_store", lambda _settings: Store())
    monkeypatch.setattr(agent, "get_chat_model_with_fallback", lambda *_args: Model())
    monkeypatch.setattr(agent, "emit_event", lambda event_type, **kwargs: events.append((event_type, kwargs)))
    monkeypatch.setattr(
        agent,
        "evaluate_quality_gate",
        lambda *_args, **_kwargs: QualityGateAssessment(
            available=True,
            needs_human_review=True,
            risk_level="medium",
            confidence=0.91,
            model_version="jev-1.13-20260917",
            request_id="dec_123",
        ),
    )
    settings = Settings(storage_dir=tmp_path, qdrant_url="local")

    result = agent.run_report(
        settings,
        material_id,
        "整理借款纠纷",
        "deepseek",
        "deepseek-v4-flash",
        material_ids=[material_id],
        retrieval_query="借款纠纷",
        legal_domain="civil_contract",
    )
    agent.resume_review(settings, result.review_task_id, ReviewDecision(approved=True))

    event_types = [item[0] for item in events]
    assert "report.started" in event_types
    assert event_types.count("retrieval.completed") >= 2
    assert "report.generated" in event_types
    assert result.quality_gate.risk_level == "medium"
    assert "report.quality_gate_completed" in event_types
    assert result.status == "pending"
    assert "review.approved" in event_types
