from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base, TaskRecord
from app.schemas import MaterialResponse, ReportResponse
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


def test_create_task_exposes_runtime_state(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)

    task = task_queue.create_task("demo", payload={"value": 1}, max_attempts=4)
    response = task_queue.task_response(task)

    assert response.status == "queued"
    assert response.stage == "queued"
    assert response.progress == 0
    assert response.progress_message is None
    assert response.cancel_requested is False
    assert response.attempts == 0
    assert response.max_attempts == 4
    assert response.created_at is not None


def test_update_task_progress_clamps_and_persists(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(
        task_queue,
        "emit_event",
        lambda event_type, **kwargs: events.append((event_type, kwargs)),
    )
    task = task_queue.create_task("demo", payload={"value": 1})

    response = task_queue.update_task_progress(task.id, "indexing", 140, "写入检索库")

    assert response.stage == "indexing"
    assert response.progress == 100
    assert response.progress_message == "写入检索库"
    assert "task.progress" in [item[0] for item in events]


def test_cancel_queued_task_marks_cancelled(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(
        task_queue,
        "emit_event",
        lambda event_type, **kwargs: events.append((event_type, kwargs)),
    )
    task = task_queue.create_task("demo", payload={"value": 1})

    response = task_queue.request_task_cancel(task.id)

    assert response.status == "cancelled"
    assert response.cancel_requested is True
    assert "task.cancelled" in [item[0] for item in events]


def test_run_task_honors_cancel_requested_after_handler(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda *_args, **_kwargs: None)
    task = task_queue.create_task("demo", payload={})

    def cooperative_handler(_payload):
        task_queue.request_task_cancel(task.id)
        return {"ok": True}

    response = task_queue.run_task(task.id, cooperative_handler)

    assert response.status == "cancelled"
    assert response.cancel_requested is True


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
    assert saved.attempts == 3
    assert saved.error_type == "ValueError"
    assert "bad input" in saved.error_message


def test_run_task_retries_until_success(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda event_type, **kwargs: events.append(event_type))
    task = task_queue.create_task("demo", payload={}, max_attempts=3)
    attempts = 0

    def flaky(_payload):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f"temporary-{attempts}")
        return {"ok": True}

    response = task_queue.run_task(task.id, flaky)

    assert response.status == "succeeded"
    assert response.attempts == 3
    assert response.progress == 100
    assert events.count("task.retrying") == 2


def test_run_task_fails_after_max_attempts(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    events = []
    monkeypatch.setattr(task_queue, "SessionLocal", sessions)
    monkeypatch.setattr(task_queue, "emit_event", lambda event_type, **kwargs: events.append(event_type))
    task = task_queue.create_task("demo", payload={}, max_attempts=2)

    response = task_queue.run_task(task.id, lambda _payload: (_ for _ in ()).throw(ValueError("permanent")))

    assert response.status == "failed"
    assert response.attempts == 2
    assert response.error_type == "ValueError"
    assert response.error_message == "permanent"
    assert events.count("task.retrying") == 1


def test_business_handlers_report_runtime_stages(tmp_path, monkeypatch):
    stages = []
    monkeypatch.setattr(task_queue, "get_settings", lambda: Settings(storage_dir=tmp_path))
    monkeypatch.setattr(
        task_queue,
        "update_task_progress",
        lambda _task_id, stage, progress, message=None: stages.append(
            (stage, progress, message)
        ),
    )

    from app.services import agent, ingestion

    monkeypatch.setattr(
        ingestion,
        "ingest",
        lambda *_args, **_kwargs: MaterialResponse(
            material_id=UUID("00000000-0000-0000-0000-000000000001"),
            title="demo",
            source_type="text",
            kind="evidence",
            segments=1,
        ),
    )
    monkeypatch.setattr(
        agent,
        "run_report",
        lambda *_args, **_kwargs: ReportResponse(
            report_markdown="# 报告",
            citations=[],
            review_task_id=UUID("00000000-0000-0000-0000-000000000002"),
            status="pending",
            legal_domain="general",
        ),
    )

    task_queue.run_material_ingest_task(
        {
            "path": str(tmp_path / "demo.md"),
            "kind": "evidence",
            "display_name": "demo.md",
            "_task_id": "00000000-0000-0000-0000-000000000010",
        }
    )
    task_queue.run_report_generation_task(
        {
            "material_id": "00000000-0000-0000-0000-000000000001",
            "question": "整理案情",
            "_task_id": "00000000-0000-0000-0000-000000000010",
        }
    )

    assert [(stage, progress) for stage, progress, _ in stages] == [
        ("normalizing", 10),
        ("indexing", 80),
        ("generating", 20),
        ("waiting_review", 90),
    ]


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
