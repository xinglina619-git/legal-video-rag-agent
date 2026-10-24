from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import AppEvent, Base, ReportCitation, ReviewTask
from app.routers import reports


def test_saved_report_includes_latest_quality_gate_event(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'reports.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    review_task_id = uuid4()
    material_id = uuid4()
    with sessions() as session:
        session.add(
            ReviewTask(
                id=review_task_id,
                material_id=material_id,
                report_markdown="# 报告",
                status="pending",
            )
        )
        session.add(
            ReportCitation(
                review_task_id=review_task_id,
                position=0,
                citation_json={"title": "证据"},
            )
        )
        session.add(
            AppEvent(
                review_task_id=review_task_id,
                event_type="report.quality_gate_completed",
                safe_metadata={
                    "quality_gate": {
                        "available": True,
                        "needs_human_review": True,
                        "risk_level": "high",
                        "confidence": 0.93,
                        "answers": {},
                        "model_version": "jev-1.13-20260917",
                    }
                },
            )
        )
        session.commit()

    monkeypatch.setattr(reports, "SessionLocal", sessions)
    result = reports.saved_report(review_task_id)

    assert result["quality_gate"]["risk_level"] == "high"
    assert result["quality_gate"]["needs_human_review"] is True
