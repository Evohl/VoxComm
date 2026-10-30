from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from voxcomm.providers.web import MAX_QUERY_LENGTH, MAX_RESULTS, MAX_SNIPPET_LENGTH, MAX_TITLE_LENGTH
from voxcomm.i18n import tr


CLOUD_BASE_URL = "https://ollama.com"
MODEL_CACHE_TTL_SECONDS = 24 * 60 * 60
TAGS_TIMEOUT_SECONDS = 10
SEARCH_TIMEOUT_SECONDS = 20
AUTH_ERROR = "Ollama Cloud API key is missing or invalid"


class CloudAuthError(RuntimeError):
    """Missing or rejected Ollama Cloud API key."""


def model_cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "voxcomm" / "ollama-cloud-models.json"


def cached_models() -> tuple[list[str], float]:
    try:
        raw = json.loads(model_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], 0.0
    if not isinstance(raw, dict):
        return [], 0.0
    models = raw.get("models")
    fetched_at = raw.get("fetched_at")
    if not isinstance(models, list) or isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return [], 0.0
    if not math.isfinite(fetched_at):
        return [], 0.0
    names = [model for model in models if isinstance(model, str) and model]
    return names, float(fetched_at)


def store_models(models: list[str]) -> None:
    target = model_cache_path()
    payload = json.dumps({"fetched_at": time.time(), "models": list(models)}, ensure_ascii=True)
    temporary = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError:
        return


def _headers(api_key: str) -> dict[str, str]:
    key = api_key.strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def list_cloud_models(base_url: str) -> list[str]:
    response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=TAGS_TIMEOUT_SECONDS)
    response.raise_for_status()
    return [item["name"] for item in response.json().get("models", []) if item.get("name")]


def verify_api_key(base_url: str, api_key: str) -> None:
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/web_search",
        json={"query": "ollama", "max_results": 1},
        headers=_headers(api_key),
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403):
        raise CloudAuthError(tr(AUTH_ERROR))
    if response.status_code >= 400:
        raise RuntimeError(tr("Ollama Cloud returned HTTP {status}: {error}",
                      status=response.status_code, error=response.text[:200]))


def web_search(query: str, base_url: str, api_key: str) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search query cannot exceed {MAX_QUERY_LENGTH} characters")
    if not api_key.strip():
        raise CloudAuthError(tr("Ollama Cloud API key is missing"))
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/web_search",
        json={"query": query, "max_results": MAX_RESULTS},
        headers=_headers(api_key),
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403):
        raise CloudAuthError(tr(AUTH_ERROR))
    if response.status_code >= 400:
        raise RuntimeError(f"Ollama web search failed with HTTP {response.status_code}")
    safe_results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for result in response.json().get("results", [])[:MAX_RESULTS]:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url", ""))
        if urlparse(url).scheme not in ("http", "https") or url in seen_urls:
            continue
        seen_urls.add(url)
        safe_results.append({
            "title": str(result.get("title", ""))[:MAX_TITLE_LENGTH],
            "url": url[:2000],
            "snippet": str(result.get("content", ""))[:MAX_SNIPPET_LENGTH],
        })
    return safe_results
