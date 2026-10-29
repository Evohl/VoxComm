from __future__ import annotations

from urllib.parse import urlparse

from ddgs import DDGS


MAX_QUERY_LENGTH = 400
MAX_RESULTS = 5
MAX_TITLE_LENGTH = 300
MAX_SNIPPET_LENGTH = 800


def search_web(query: str) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search query cannot exceed {MAX_QUERY_LENGTH} characters")

    results = []
    errors = []
    for backend in ("bing", "google"):
        try:
            results.extend(DDGS(timeout=8).text(
                query,
                safesearch="moderate",
                max_results=MAX_RESULTS,
                backend=backend,
            ))
        except Exception as error:
            errors.append(error)
    if not results and errors:
        raise RuntimeError("All web search backends failed") from errors[-1]

    safe_results = []
    seen_urls = set()
    for result in results[:MAX_RESULTS]:
        url = str(result.get("href", ""))
        if urlparse(url).scheme not in ("http", "https") or url in seen_urls:
            continue
        seen_urls.add(url)
        safe_results.append({
            "title": str(result.get("title", ""))[:MAX_TITLE_LENGTH],
            "url": url[:2000],
            "snippet": str(result.get("body", ""))[:MAX_SNIPPET_LENGTH],
        })
    return safe_results