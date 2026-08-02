from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app
from app.routers import tasks
from app.services import task_queue


def isolated_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'routes.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_task_status_route_returns_saved_task(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda *_args, **_kwargs: None)
    task = task_queue.create_task("demo", {"value": 1})

    response = TestClient(app).get(f"/tasks/{task.id}")

    assert response.status_code == 200
    assert response.json()["task_id"] == str(task.id)
    assert response.json()["status"] == "queued"


def test_material_ingest_background_returns_task(monkeypatch, tmp_path):
    captured = {}

    def fake_enqueue(_settings, task_type, payload, **kwargs):
        captured["task_type"] = task_type
        captured["payload"] = payload
        return {
            "task_id": str(uuid4()),
            "task_type": task_type,
            "status": "queued",
            "result": {},
            "error_type": None,
            "error_message": None,
            "attempts": 0,
        }

    from app.routers import materials

    monkeypatch.setattr(materials, "enqueue_task", fake_enqueue)
    response = TestClient(app).post(
        "/materials/ingest?kind=evidence&background=true",
        files={"file": ("demo.md", b"hello", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json()["task_type"] == "material_ingest"
    assert captured["task_type"] == "material_ingest"
    assert captured["payload"]["kind"] == "evidence"


def test_report_background_returns_task(monkeypatch):
    captured = {}

    def fake_enqueue(_settings, task_type, payload, **kwargs):
        captured["task_type"] = task_type
        captured["payload"] = payload
        return {
            "task_id": str(uuid4()),
            "task_type": task_type,
            "status": "queued",
            "result": {},
            "error_type": None,
            "error_message": None,
            "attempts": 0,
        }

    from app.routers import reports

    monkeypatch.setattr(reports, "enqueue_task", fake_enqueue)
    response = TestClient(app).post(
        "/reports",
        json={"material_id": str(uuid4()), "question": "整理案情", "background": True},
    )

    assert response.status_code == 200
    assert response.json()["task_type"] == "report_generation"
    assert captured["payload"]["question"] == "整理案情"
