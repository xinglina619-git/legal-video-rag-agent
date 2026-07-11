"""报告质量回归评估脚本。

示例：
    python scripts/eval_report_quality.py data/demo/loan_case/report_quality_cases.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REQUIRED_SECTIONS = [
    "免责声明",
    "案情摘要",
    "事件时间线",
    "核心争议点",
    "仍需补充或核实的信息",
    "相关法律依据",
]

FORBIDDEN_TERMS = [
    "胜诉率",
    "一定胜诉",
    "必然胜诉",
    "必然败诉",
    "法院一定支持",
    "一定支持",
]


def evaluate_report(markdown: str) -> dict[str, Any]:
    missing_sections = [section for section in REQUIRED_SECTIONS if section not in markdown]
    missing_requirements = []
    if not re.search(r"\[证据\d+\]", markdown):
        missing_requirements.append("evidence_citation")
    if not re.search(r"\[依据\d+\]", markdown):
        missing_requirements.append("authority_citation")
    forbidden_terms = [term for term in FORBIDDEN_TERMS if term in markdown]
    passed = not missing_sections and not missing_requirements and not forbidden_terms
    return {
        "passed": passed,
        "missing_sections": missing_sections,
        "missing_requirements": missing_requirements,
        "forbidden_terms": forbidden_terms,
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = json.loads(line)
        if "report_markdown" not in payload:
            raise ValueError(f"{path}:{line_no} 缺少 report_markdown")
        cases.append(payload)
    return cases


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    passed_count = 0
    for index, case in enumerate(cases, start=1):
        result = evaluate_report(str(case["report_markdown"]))
        passed_count += int(result["passed"])
        rows.append(
            {
                "case_id": case.get("case_id", f"case-{index}"),
                **result,
            }
        )
    total = len(cases)
    return {
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": round(passed_count / total, 4) if total else 0.0,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generated legal report quality.")
    parser.add_argument("cases_path", type=Path, help="JSONL with case_id and report_markdown")
    args = parser.parse_args()
    print(json.dumps(evaluate_cases(load_cases(args.cases_path)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
