from __future__ import annotations

import httpx

from ..config import TAVILY_API_KEY, TAVILY_TIMEOUT_SECONDS


class SearchNotConfigured(Exception):
    pass


class SearchUnavailable(Exception):
    pass


async def search_web(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    """Return small, untrusted search excerpts for a user-requested evaluation."""
    if not TAVILY_API_KEY:
        raise SearchNotConfigured
    query = " ".join(query.split())[:500]
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                json={
                    "query": query,
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": min(max(1, max_results), 5),
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_usage": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise SearchUnavailable from error

    results = payload.get("results")
    if not isinstance(results, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in results[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()[:200]
        url = str(item.get("url", "")).strip()[:1000]
        content = str(item.get("content", "")).strip()[:1200]
        if not title or not content or not url.startswith(("https://", "http://")):
            continue
        cleaned.append({
            "title": title,
            "url": url,
            "content": content,
            "published_date": str(item.get("published_date", "")).strip()[:80],
        })
    return cleaned
