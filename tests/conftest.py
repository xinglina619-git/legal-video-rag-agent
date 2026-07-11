import pytest


@pytest.fixture(autouse=True)
def disable_external_tracing(monkeypatch):
    """测试环境默认禁止外部 trace，避免本地/CI 环境变量导致误上传。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
