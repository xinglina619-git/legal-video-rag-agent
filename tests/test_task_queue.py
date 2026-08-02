from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base, TaskRecord
from app.services import task_queue


def isolated_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_create_task_persists_queued_record(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)

    task = task_queue.create_task(
        "material_ingest",
        payload={"path": "demo.md", "kind": "evidence"},
        idempotency_key="upload:demo",
    )

    with sessions() as session:
        saved = session.get(TaskRecord, task.id)

    assert saved is not None
    assert saved.status == "queued"
    assert saved.task_type == "material_ingest"
    assert saved.payload_json["path"] == "demo.md"
    assert saved.idempotency_key == "upload:demo"


def test_create_task_reuses_active_idempotency_key(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(
        task_queue,
        "emit_event",
        lambda event_type, **kwargs: events.append((event_type, kwargs)),
    )

    first = task_queue.create_task("demo", payload={"value": 1}, idempotency_key="same-key")
    second = task_queue.create_task("demo", payload={"value": 2}, idempotency_key="same-key")

    with sessions() as session:
        saved = session.query(TaskRecord).all()

    assert second.id == first.id
    assert len(saved) == 1
    assert "task.deduplicated" in [item[0] for item in events]


def test_run_task_marks_success_and_result(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda event_type, **kwargs: events.append((event_type, kwargs)))
    task = task_queue.create_task("demo", payload={"value": 1})

    task_queue.run_task(task.id, lambda payload: {"value": payload["value"] + 1})

    with sessions() as session:
        saved = session.get(TaskRecord, task.id)

    assert saved.status == "succeeded"
    assert saved.attempts == 1
    assert saved.result_json == {"value": 2}
    assert "task.succeeded" in [item[0] for item in events]


def test_run_task_marks_failure_without_raising_to_worker_loop(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda *_args, **_kwargs: None)
    task = task_queue.create_task("demo", payload={"value": 1})

    def broken(_payload):
        raise ValueError("bad input")

    task_queue.run_task(task.id, broken)

    with sessions() as session:
        saved = session.get(TaskRecord, task.id)

    assert saved.status == "failed"
    assert saved.attempts == 1
    assert saved.error_type == "ValueError"
    assert "bad input" in saved.error_message


def test_enqueue_task_inline_executes_registered_handler(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(task_queue.TASK_HANDLERS, "demo", lambda payload: {"ok": payload["ok"]})

    response = task_queue.enqueue_task(
        Settings(storage_dir=tmp_path, task_queue_mode="inline"),
        "demo",
        {"ok": True},
    )

    assert response.status == "succeeded"
    assert isinstance(response.task_id, UUID)
    assert response.result == {"ok": True}
