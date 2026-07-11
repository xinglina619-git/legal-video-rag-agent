from pathlib import Path

from app.schemas import Citation
from app.services.authority import (
    classify_question,
    infer_authority_metadata,
    rank_authority_results,
)


def test_classify_question_routes_financial_consumer_terms() -> None:
    route = classify_question("信用卡逾期和个人信息处理是否符合金融消费者保护要求？")

    assert route.legal_domain == "financial_consumer"
    assert "信用卡" in route.matched_terms


def test_infer_authority_metadata_uses_category_and_source_url(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    source = root / "01_法律法规【法条库】" / "个人信息保护法_2021.txt"
    source.parent.mkdir(parents=True)
    source.write_text("URL：https://flk.npc.gov.cn/detail.html", encoding="utf-8")

    metadata = infer_authority_metadata(source, root)

    assert metadata["authority_type"] == "statute"
    assert metadata["legal_domain"] == "data_protection"
    assert metadata["source_url"] == "https://flk.npc.gov.cn/detail.html"


def test_sidecar_metadata_overrides_inferred_metadata(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    source = root / "01_legal_regulations" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("plain text", encoding="utf-8")
    source.with_suffix(".txt.metadata.json").write_text(
        '{"legal_domain": "financial_consumer", "effective_status": "effective"}',
        encoding="utf-8",
    )

    metadata = infer_authority_metadata(source, root)

    assert metadata["legal_domain"] == "financial_consumer"
    assert metadata["effective_status"] == "effective"


def test_rank_authority_prioritizes_domain_and_status() -> None:
    common = {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "title": "source",
        "source_type": "text",
        "excerpt": "excerpt",
    }
    candidates = [
        Citation(**common, score=0.9, legal_domain="general", effective_status="unknown"),
        Citation(**common, score=0.7, legal_domain="labor", effective_status="effective"),
    ]

    ranked = rank_authority_results(candidates, classify_question("劳动合同解除"))

    assert ranked[0].legal_domain == "labor"
