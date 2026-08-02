import pytest


@pytest.fixture(autouse=True)
def disable_external_tracing(monkeypatch):
    """测试环境默认禁止外部 trace，避免本地/CI 环境变量导致误上传。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture(autouse=True)
def isolate_business_observability(monkeypatch):
    """业务流程测试默认不写真实审计库，避免读取本地 .env.local 后误连外部数据库。"""
    from app.services import agent, cases, ingestion

    def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent, "emit_event", noop)
    monkeypatch.setattr(cases, "emit_event", noop)
    monkeypatch.setattr(ingestion, "emit_event", noop)
