"""轻量 RAG 检索评估脚本。

用途：用少量人工标注问题验证 Evidence / Authority 检索是否能命中预期关键词。

示例：
    python scripts/eval_retrieval.py data/demo/loan_case/golden_questions.jsonl --collection authority
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.config import get_settings
from app.services.qdrant_store import get_hybrid_store

SearchFn = Callable[[str, int], list[Any]]


def load_golden_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = json.loads(line)
        if not payload.get("question"):
            raise ValueError(f"{path}:{line_no} 缺少 question")
        if not payload.get("expected_terms"):
            raise ValueError(f"{path}:{line_no} 缺少 expected_terms")
        cases.append(payload)
    return cases


def citation_matches(citation: Any, expected_terms: list[str]) -> bool:
    text = f"{getattr(citation, 'title', '')}\n{getattr(citation, 'excerpt', '')}"
    return all(term in text for term in expected_terms)


def evaluate_cases(cases: list[dict[str, Any]], search: SearchFn, limit: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    hits = 0
    started = perf_counter()
    for case in cases:
        question = str(case["question"])
        expected_terms = [str(item) for item in case["expected_terms"]]
        citations = search(question, limit)
        hit = any(citation_matches(citation, expected_terms) for citation in citations)
        hits += int(hit)
        rows.append(
            {
                "question": question,
                "expected_terms": expected_terms,
                "hit": hit,
                "top_titles": [getattr(item, "title", "") for item in citations[:limit]],
            }
        )
    total = len(cases)
    return {
        "total": total,
        "hits": hits,
        "recall_at_k": round(hits / total, 4) if total else 0.0,
        "limit": limit,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "rows": rows,
    }


def build_qdrant_search(collection_kind: str) -> SearchFn:
    settings = get_settings()
    store = get_hybrid_store(settings)
    collection = (
        settings.authority_collection
        if collection_kind == "authority"
        else settings.evidence_collection
    )

    def search(question: str, limit: int):
        return store.search(collection, question, material_ids=[], limit=limit)

    return search


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval with a JSONL golden set.")
    parser.add_argument("golden_path", type=Path, help="JSONL file with question and expected_terms")
    parser.add_argument(
        "--collection",
        choices=["authority", "evidence"],
        default="authority",
        help="Which Qdrant collection to evaluate.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Recall@K limit")
    args = parser.parse_args()

    cases = load_golden_cases(args.golden_path)
    result = evaluate_cases(cases, build_qdrant_search(args.collection), args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
