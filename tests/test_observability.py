from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import AppEvent, Base
from app.services import observability


def isolated_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_safe_metadata_redacts_sensitive_values():
    result = observability.safe_metadata(
        {
            "phone": "13800138000",
            "id_card": "110101199001011234",
            "bank": "6222021234567890123",
            "nested": {"content": "联系电话 13800138000"},
        }
    )

    text = str(result)
    assert "13800138000" not in text
    assert "199001011234" not in text
    assert "6222021234567890123" not in text


def test_emit_event_persists_sanitized_event(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(observability, "SessionLocal", sessions)
    case_id = uuid4()

    observability.emit_event(
        "report.generated",
        case_id=case_id,
        status="success",
        duration_ms=123,
        provider="deepseek",
        model="deepseek-v4-flash",
        metadata={"summary": "电话 13800138000，报告长度 2000"},
    )

    with sessions() as session:
        event = session.scalar(select(AppEvent))

    assert event is not None
    assert event.event_type == "report.generated"
    assert event.case_id == case_id
    assert event.duration_ms == 123
    assert event.provider == "deepseek"
    assert "13800138000" not in str(event.safe_metadata)


def test_emit_event_never_breaks_business_flow(monkeypatch):
    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("database down")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(observability, "SessionLocal", lambda: BrokenSession())

    observability.emit_event("operation.failed", metadata={"anything": "ok"})


def test_langsmith_settings_default_to_safe_values():
    settings = Settings()

    assert settings.langsmith_tracing is False
    assert settings.langsmith_sample_rate == 0.0
    assert settings.langsmith_record_inputs is False


def test_configure_langsmith_environment_is_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

    observability.configure_langsmith_environment(
        Settings(langsmith_tracing=False, langsmith_project="demo")
    )

    assert "LANGSMITH_TRACING" not in observability.os.environ

    observability.configure_langsmith_environment(
        Settings(langsmith_tracing=True, langsmith_project="demo")
    )

    assert observability.os.environ["LANGSMITH_TRACING"] == "true"
    assert observability.os.environ["LANGSMITH_PROJECT"] == "demo"
