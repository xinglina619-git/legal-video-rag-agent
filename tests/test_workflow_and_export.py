from types import SimpleNamespace
from uuid import uuid4

from langgraph.types import Command

from app.config import Settings
from app.services import agent
from app.services.report_export import render_report_docx, render_report_pdf


class FakeStore:
    def __init__(self, _settings):
        pass

    def search(self, *_args, **_kwargs):
        return []


class FakeModel:
    def invoke(self, _prompt):
        return SimpleNamespace(content="# Review draft\nEvidence needs human verification.")


def test_checkpoint_interrupt_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "get_hybrid_store", lambda _settings: FakeStore(_settings))
    monkeypatch.setattr(agent, "get_chat_model_with_fallback", lambda *_args: FakeModel())
    settings = Settings(storage_dir=tmp_path, qdrant_url="local")
    graph = agent._build_graph(settings)
    config = {"configurable": {"thread_id": "review-test"}}
    graph.invoke(
        {
            "question": "Please organize the contract evidence.",
            "retrieval_query": "Please organize the contract evidence.",
            "material_id": uuid4(),
            "material_ids": [],
            "provider": None,
            "model": None,
            "route": {
                "legal_domain": "civil_contract",
                "matched_terms": ["contract"],
            },
            "evidence": [],
            "authorities": [],
            "draft": "",
            "approved": False,
            "review_notes": "",
        },
        config,
    )
    assert graph.get_state(config).values["draft"].startswith("# Review draft")
    graph.invoke(
        Command(resume={"approved": True, "edited_report": "# Approved", "notes": "checked"}),
        config,
    )
    state = graph.get_state(config).values
    assert state["approved"] is True
    assert state["draft"] == "# Approved"


def test_report_pdf_export_is_valid_pdf():
    result = render_report_pdf("# Review\n\n- Evidence item\n- \u9700\u8981\u4eba\u5de5\u6838\u9a8c")
    assert result.startswith(b"%PDF")
    assert len(result) > 500


def test_report_docx_export_is_valid_zip():
    result = render_report_docx("# 分析报告\n\n- 证据需要核验")
    assert result.startswith(b"PK")
    assert len(result) > 1000
