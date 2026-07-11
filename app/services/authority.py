import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas import Citation


@dataclass(frozen=True)
class QuestionRoute:
    legal_domain: str
    matched_terms: list[str]


DOMAIN_TERMS = {
    "labor": ["\u52b3\u52a8", "\u52b3\u52a1", "\u5de5\u4f24", "\u793e\u4fdd", "\u5de5\u8d44", "\u52a0\u73ed", "\u89e3\u9664\u52b3\u52a8"],
    "marriage_family": ["\u5a5a\u59fb", "\u79bb\u5a5a", "\u629a\u517b", "\u7ee7\u627f", "\u5f69\u793c", "\u592b\u59bb"],
    "company": ["\u516c\u53f8", "\u80a1\u6743", "\u80a1\u4e1c", "\u8463\u4e8b", "\u7834\u4ea7", "\u5408\u4f19"],
    "criminal": ["\u5211\u4e8b", "\u72af\u7f6a", "\u5211\u6cd5", "\u53d6\u4fdd", "\u8bc8\u9a97", "\u76d7\u7a83"],
    "administrative": ["\u884c\u653f", "\u884c\u653f\u5904\u7f5a", "\u884c\u653f\u590d\u8bae", "\u884c\u653f\u8bc9\u8bbc"],
    "data_protection": ["\u4e2a\u4eba\u4fe1\u606f", "\u9690\u79c1", "\u6570\u636e\u5b89\u5168", "\u7f51\u7edc\u5b89\u5168"],
    "financial_consumer": ["\u94f6\u884c", "\u4fe1\u7528\u5361", "\u8d37\u6b3e", "\u91d1\u878d\u6d88\u8d39\u8005", "\u5f81\u4fe1", "\u652f\u4ed8"],
    "civil_contract": ["\u5408\u540c", "\u501f\u8d37", "\u503a\u52a1", "\u8fdd\u7ea6", "\u62c5\u4fdd", "\u4fb5\u6743", "\u6c11\u4e8b"],
}


def classify_question(question: str) -> QuestionRoute:
    matched: dict[str, list[str]] = {
        domain: [term for term in terms if term in question]
        for domain, terms in DOMAIN_TERMS.items()
    }
    matched = {domain: terms for domain, terms in matched.items() if terms}
    if not matched:
        return QuestionRoute(legal_domain="general", matched_terms=[])
    domain, terms = max(matched.items(), key=lambda item: len(item[1]))
    return QuestionRoute(legal_domain=domain, matched_terms=terms)


def infer_authority_metadata(path: Path, source_root: Path) -> dict[str, str | None]:
    relative = path.resolve().relative_to(source_root.resolve())
    category = relative.parts[0] if relative.parts else "uncategorized"
    name = path.stem
    context = f"{category} {name}"
    metadata = {
        "authority_type": _authority_type(context),
        "legal_domain": _domain_from_text(context),
        "jurisdiction": "CN",
        "effective_status": _effective_status(context),
        "effective_date": _find_date(name),
        "source_url": _find_source_url(path),
        "source_category": category,
        "version_note": "Imported from local public-study corpus; verify authority and current validity before professional use.",
    }
    return {**metadata, **_read_sidecar_metadata(path)}


def rank_authority_results(
    candidates: list[Citation], route: QuestionRoute, limit: int = 6
) -> list[Citation]:
    def priority(item: Citation) -> tuple[int, int, float]:
        domain_match = int(item.legal_domain == route.legal_domain)
        status_priority = {"effective": 2, "unknown": 1, "repealed": 0}.get(
            item.effective_status or "unknown", 1
        )
        return domain_match, status_priority, item.score or 0.0

    return sorted(candidates, key=priority, reverse=True)[:limit]


def _authority_type(text: str) -> str:
    if "\u53f8\u6cd5\u89e3\u91ca" in text:
        return "judicial_interpretation"
    if any(term in text for term in ("\u6cd5\u5f8b\u6cd5\u89c4", "\u6cd5\u6761", "\u6761\u4f8b")):
        return "statute"
    if any(term in text for term in ("\u6307\u5bfc\u6848\u4f8b", "\u516c\u62a5")):
        return "guiding_case"
    if any(term in text for term in ("\u88c1\u5224", "\u5224\u51b3", "\u88c1\u5b9a")):
        return "judgment"
    if any(term in text for term in ("\u6a21\u677f", "\u8d77\u8bc9\u72b6", "\u7533\u8bf7\u4e66")):
        return "template"
    if any(term in text for term in ("\u5b9e\u52a1", "\u95ee\u7b54")):
        return "practice"
    return "other"


def _domain_from_text(text: str) -> str:
    for domain, terms in DOMAIN_TERMS.items():
        if any(term in text for term in terms):
            return domain
    return "general"


def _effective_status(text: str) -> str:
    if any(term in text for term in ("\u5e9f\u6b62", "\u5931\u6548", "\u5df2\u5e9f")):
        return "repealed"
    if any(term in text for term in ("\u73b0\u884c\u6709\u6548", "\u6709\u6548")):
        return "effective"
    return "unknown"


def _find_date(text: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2})[\u5e74_-]?(\d{1,2})?[\u6708_-]?(\d{1,2})?", text)
    if not match:
        return None
    year, month, day = match.groups()
    return "-".join(
        part.zfill(2) if index else part
        for index, part in enumerate((year, month or "1", day or "1"))
    )


def _find_source_url(path: Path) -> str | None:
    if path.suffix.lower() not in {".txt", ".md"}:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:6000]
    except OSError:
        return None
    match = re.search(r"https?://[^\s\]\)\"']+", text)
    return match.group(0) if match else None


def _read_sidecar_metadata(path: Path) -> dict[str, str | None]:
    sidecar = path.with_suffix(path.suffix + ".metadata.json")
    if not sidecar.exists():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    allowed = {
        "authority_type",
        "legal_domain",
        "jurisdiction",
        "effective_status",
        "effective_date",
        "source_url",
        "source_category",
        "version_note",
    }
    return {
        key: str(value) for key, value in payload.items() if key in allowed and value is not None
    }
