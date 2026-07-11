from urllib.parse import parse_qs, unquote, urlparse
from uuid import NAMESPACE_URL, uuid5

import requests

from app.schemas import Citation

OFFICIAL_DOMAINS = (
    "gov.cn",
    "npc.gov.cn",
    "court.gov.cn",
    "spp.gov.cn",
)


def _is_official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_DOMAINS)


def _result_url(href: str) -> str:
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc:
        value = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(value)
    return href


def search_official_sources(
    question: str, limit: int = 3, timeout_seconds: int = 12
) -> list[Citation]:
    """Optional best-effort supplement; only official Chinese public-sector domains survive."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("联网权威检索需要安装 beautifulsoup4") from exc
    query = f"{question} 法律 司法解释 site:gov.cn OR site:npc.gov.cn OR site:court.gov.cn"
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 legal-study-demo/0.3"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    citations: list[Citation] = []
    for result in soup.select(".result"):
        anchor = result.select_one(".result__a")
        if not anchor:
            continue
        url = _result_url(str(anchor.get("href") or ""))
        if not _is_official(url):
            continue
        snippet_node = result.select_one(".result__snippet")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        if not snippet:
            continue
        citations.append(
            Citation(
                material_id=uuid5(NAMESPACE_URL, url),
                title=anchor.get_text(" ", strip=True),
                source_type="text",
                excerpt=snippet,
                authority_type="official_web",
                legal_domain="general",
                effective_status="unknown",
                source_category="official_web",
                source_url=url,
            )
        )
        if len(citations) >= limit:
            break
    return citations
