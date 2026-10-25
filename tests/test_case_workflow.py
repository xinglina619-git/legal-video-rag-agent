from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import (
    Base,
    CaseMaterial,
    CaseRecord,
    Material,
    ReportCitation,
)
from app.schemas import CaseCreate, CaseUpdate, Citation
from app.services import agent, cases


def isolated_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_case_summary_refresh_and_early_report_status(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    monkeypatch.setattr(cases, "SessionLocal", sessions)
    payload = cases.create_case(
        CaseCreate(
            title="借款纠纷",
            dispute_type="loan_contract",
            parties="甲与乙",
            narrative="乙借款后尚未归还",
            request="归还本金",
        )
    )
    case_id = UUID(payload["case_id"])
    material_id = uuid4()
    with sessions() as session:
        session.add(
            Material(
                id=material_id,
                title="旧案情摘要",
                source_type="text",
                kind="evidence",
                file_path="old.md",
                status="indexed",
            )
        )
        session.add(CaseMaterial(case_id=case_id, material_id=material_id, role="intake"))
        session.commit()

    cases.link_report(case_id, uuid4())
    with sessions() as session:
        assert session.get(CaseRecord, case_id).status == "intake"

    cases.update_case(case_id, CaseUpdate(confirmed_summary="确认后的新案情摘要"))
    with sessions() as session:
        links = session.scalars(
            select(CaseMaterial).where(
                CaseMaterial.case_id == case_id, CaseMaterial.role == "intake"
            )
        ).all()
        assert links == []
        assert session.get(CaseRecord, case_id).status == "ready"


class CapturingStore:
    def __init__(self, material_id):
        self.material_id = material_id
        self.queries = []

    def search(self, collection, question, *_args, **_kwargs):
        self.queries.append(question)
        if collection == "legal_evidence":
            return [
                Citation(
                    material_id=self.material_id,
                    title="问答案情",
                    source_type="text",
                    excerpt="乙方向甲方借款后未按期归还。",
                )
            ]
        return [
            Citation(
                material_id=uuid4(),
                title="民法典示例条文",
                source_type="text",
                excerpt="借款人应当按照约定返还借款。",
                authority_type="statute",
                legal_domain="civil_contract",
                effective_status="effective",
            )
        ]


class DraftModel:
    def invoke(self, _prompt):
        return SimpleNamespace(content="# 分析报告\n\n需要核实借款交付及还款情况。")


def test_case_report_uses_case_context_and_persists_citations(tmp_path, monkeypatch):
    sessions = isolated_sessions(tmp_path)
    material_id = uuid4()
    store = CapturingStore(material_id)
    monkeypatch.setattr(agent, "SessionLocal", sessions)
    monkeypatch.setattr(agent, "get_hybrid_store", lambda _settings: store)
    monkeypatch.setattr(agent, "get_chat_model_with_fallback", lambda *_args: DraftModel())
    settings = Settings(storage_dir=tmp_path / "storage", qdrant_url="local")

    result = agent.run_report(
        settings,
        material_id,
        "整理案情并检索相关依据",
        None,
        None,
        material_ids=[material_id],
        retrieval_query="整理案情并检索相关依据\n乙向甲借款十万元后未归还",
        legal_domain="civil_contract",
    )

    assert result.legal_domain == "civil_contract"
    assert len(result.citations) == 2
    assert result.quality_gate is not None
    assert result.quality_gate.available is False
    assert result.status == "pending"
    assert all("借款" in query for query in store.queries)
    with sessions() as session:
        saved = session.scalars(
            select(ReportCitation).where(
                ReportCitation.review_task_id == result.review_task_id
            )
        ).all()
        assert len(saved) == 2
