from types import SimpleNamespace

from scripts.eval_retrieval import citation_matches, evaluate_cases, load_golden_cases


def test_citation_matches_terms_from_title_or_excerpt():
    citation = SimpleNamespace(title="民法典借款合同摘录", excerpt="借款人应当按照约定返还借款。")

    assert citation_matches(citation, ["借款", "返还"])
    assert not citation_matches(citation, ["劳动仲裁"])


def test_load_golden_cases_reads_jsonl(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"question":"借款到期不还怎么办","expected_terms":["借款","返还"]}\n',
        encoding="utf-8",
    )

    cases = load_golden_cases(path)

    assert cases == [
        {
            "question": "借款到期不还怎么办",
            "expected_terms": ["借款", "返还"],
        }
    ]


def test_evaluate_cases_reports_recall_at_k():
    cases = [
        {"question": "借款到期不还怎么办", "expected_terms": ["借款", "返还"]},
        {"question": "劳动工资拖欠怎么办", "expected_terms": ["工资"]},
    ]

    def fake_search(question: str, limit: int):
        if "借款" in question:
            return [SimpleNamespace(title="民法典借款合同", excerpt="返还借款")]
        return [SimpleNamespace(title="民法典合同编", excerpt="一般合同规则")]

    result = evaluate_cases(cases, fake_search, limit=3)

    assert result["total"] == 2
    assert result["hits"] == 1
    assert result["recall_at_k"] == 0.5
    assert result["rows"][0]["hit"] is True
    assert result["rows"][1]["hit"] is False
