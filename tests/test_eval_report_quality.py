from scripts.eval_report_quality import evaluate_cases, evaluate_report


GOOD_REPORT = """
# 法律纠纷材料分析报告

## 免责声明

本报告仅用于材料整理。

## 案情摘要

张某主张李某借款未还。[证据1]

## 事件时间线

- 2025-03-05 转账。

## 核心争议点

借款关系和剩余本金。

## 仍需补充或核实的信息

需要核实完整流水。

## 相关法律依据

借款人应返还借款。[依据1]
"""


def test_evaluate_report_passes_good_report():
    result = evaluate_report(GOOD_REPORT)

    assert result["passed"] is True
    assert result["missing_sections"] == []
    assert result["forbidden_terms"] == []


def test_evaluate_report_fails_without_citations():
    result = evaluate_report(GOOD_REPORT.replace("[证据1]", "").replace("[依据1]", ""))

    assert result["passed"] is False
    assert "evidence_citation" in result["missing_requirements"]
    assert "authority_citation" in result["missing_requirements"]


def test_evaluate_report_fails_for_forbidden_legal_conclusion():
    result = evaluate_report(GOOD_REPORT + "\n本案胜诉率为 90%，法院一定支持。")

    assert result["passed"] is False
    assert "胜诉率" in result["forbidden_terms"]
    assert "一定支持" in result["forbidden_terms"]


def test_evaluate_cases_summarizes_pass_rate():
    cases = [
        {"case_id": "good", "report_markdown": GOOD_REPORT},
        {"case_id": "bad", "report_markdown": "只有一句话"},
    ]

    result = evaluate_cases(cases)

    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["pass_rate"] == 0.5
