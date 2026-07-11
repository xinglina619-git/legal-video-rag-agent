from uuid import uuid4

from app.services.agent import build_labeled_citations, format_citations_for_prompt


def test_build_labeled_citations_numbers_evidence_and_authority_separately():
    evidence_id = uuid4()
    authority_id = uuid4()
    evidence = [
        {
            "material_id": str(evidence_id),
            "title": "聊天记录",
            "source_type": "text",
            "excerpt": "李某承认借款八万元。",
        }
    ]
    authorities = [
        {
            "material_id": str(authority_id),
            "title": "民法典借款合同摘录",
            "source_type": "text",
            "excerpt": "借款人应当返还借款。",
        }
    ]

    labeled = build_labeled_citations(evidence, authorities)

    assert [item.reference_label for item in labeled] == ["证据1", "依据1"]


def test_format_citations_for_prompt_includes_reference_labels():
    material_id = uuid4()
    formatted = format_citations_for_prompt(
        [
            {
                "material_id": str(material_id),
                "title": "转账记录",
                "source_type": "text",
                "excerpt": "张某向李某转账 80000 元。",
            }
        ],
        "证据",
    )

    assert "[证据1]" in formatted
    assert "转账记录" in formatted
