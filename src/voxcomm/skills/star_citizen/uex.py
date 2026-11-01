from __future__ import annotations

import json
import httpx


UEX_API_BASE = "https://api.uexcorp.uk/2.0"
MAX_QUERY_LENGTH = 100
MAX_RESPONSE_BYTES = 2_000_000
MAX_RESULTS = 8
REQUEST_TIMEOUT = 8
OPERATIONS = {
    "commodities": "commodities",
    "terminals": "terminals",
    "prices": "commodities_prices",
    "routes": "commodities_routes",
}
_TEXT_FIELDS = (
    "name", "fullname", "nickname", "displayname", "code", "slug",
    "commodity_name", "terminal_name", "terminal_origin_name", "terminal_destination_name",
    "origin_terminal_name", "destination_terminal_name",
    "location_name", "star_system_name", "planet_name", "moon_name", "city_name",
    "outpost_name", "space_station_name", "orbit_name",
)
_CORE_RESULT_FIELDS = (
    "id", "id_commodity", "name", "commodity_name", "terminal_name", "terminal_origin_name",
    "terminal_destination_name", "origin_terminal_name", "destination_terminal_name",
    "location_name", "star_system_name", "planet_name", "moon_name", "game_version", "version",
    "price", "price_buy", "price_sell", "price_origin", "price_destination",
    "date_modified", "date_added", "updated_at", "last_updated", "scu_buy", "scu_sell",
    "stock", "status_buy", "status_sell", "availability", "is_available",
    "id_terminal", "id_terminal_origin", "id_terminal_destination", "city_name",
    "outpost_name", "space_station_name", "orbit_name", "star_system_origin_name",
    "star_system_destination_name", "origin_star_system_name", "destination_star_system_name",
)
_CORE_RESULT_FIELD_RANK = {field: rank for rank, field in enumerate(_CORE_RESULT_FIELDS)}
_SEMANTIC_FIELD_PARTS = (
    "commodity", "terminal", "origin", "destination", "location", "system", "planet",
    "moon", "city", "outpost", "station", "orbit", "price", "version", "date",
    "time", "updated", "created", "stock", "availability", "available", "status",
    "scu", "quantity", "supply", "demand",
)


def _request_data(endpoint: str, params: dict[str, str], get=httpx.get) -> list[dict]:
    response = get(
        f"{UEX_API_BASE}/{endpoint}/",
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("UEX response exceeded the size limit")
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        message = payload.get("message", "Unexpected UEX API response") if isinstance(payload, dict) else "Unexpected UEX API response"
        raise RuntimeError(str(message)[:300])
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("UEX API returned an unexpected data format")
    return [row for row in data if isinstance(row, dict)]


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(field, "")) for field in _TEXT_FIELDS).casefold()


def _matching_rows(
    rows: list[dict], query: str, location: str = "", *, prioritize_exact_name: bool = False,
) -> list[dict]:
    query = query.casefold()
    location = location.casefold()
    matches = [
        row for row in rows
        if query in _row_text(row) and (not location or location in _row_text(row))
    ]
    if prioritize_exact_name:
        matches.sort(key=lambda row: str(row.get("name", "")).casefold() != query)
    return matches[:MAX_RESULTS]


def _result_field_order(key: str) -> tuple[int, int, str]:
    if key in _CORE_RESULT_FIELD_RANK:
        return (0, _CORE_RESULT_FIELD_RANK[key], key)
    parts = key.casefold().split("_")
    if any(part in _SEMANTIC_FIELD_PARTS for part in parts):
        return (1, 0, key)
    return (2, 0, key)


def _compact_rows(rows: list[dict]) -> list[dict]:
    compact = []
    for row in rows:
        selected = {}
        for key in sorted(row, key=lambda item: (_result_field_order(str(item)), type(item).__name__, repr(item))):
            if len(selected) >= 32:
                break
            value = row[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                selected.setdefault(str(key)[:80], value[:240] if isinstance(value, str) else value)
        compact.append(selected)
    return compact


def query_uex(operation: str, query: str, location: str = "", *, get=httpx.get) -> dict:
    if operation not in OPERATIONS:
        raise ValueError("Choose a supported UEX data operation")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A commodity or terminal search query is required")
    query = query.strip()
    location = location.strip() if isinstance(location, str) else ""
    if len(query) > MAX_QUERY_LENGTH or len(location) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search values cannot exceed {MAX_QUERY_LENGTH} characters")

    endpoint = OPERATIONS[operation]
    params = {"type": "commodity"} if operation == "terminals" else {}
    if operation in ("prices", "routes"):
        commodities = _matching_rows(
            _request_data("commodities", {}, get), query, prioritize_exact_name=True,
        )
        exact = [row for row in commodities if str(row.get("name", "")).casefold() == query.casefold()]
        if not exact and len(commodities) > 1:
            return {
                "status": "choose_commodity",
                "matches": _compact_rows(commodities),
            }
        if not commodities:
            return {"status": "no_matches", "matches": []}
        commodity = (exact or commodities)[0]
        commodity_id = commodity.get("id")
        if commodity_id is None:
            raise RuntimeError("UEX commodity record has no identifier")
        params["id_commodity"] = str(commodity_id)
        rows = _request_data(endpoint, params, get)
        if location:
            location_query = location.casefold()
            rows = [row for row in rows if location_query in _row_text(row)]
        rows = rows[:MAX_RESULTS]
    else:
        rows = _request_data(endpoint, params, get)
        rows = _matching_rows(rows, query, location)

    return {
        "status": "ok",
        "source": "UEX community database",
        "warning": "Community-reported data may be stale or inaccurate; it is not live-server truth. Trade routes are not exact navigation routes.",
        "operation": operation,
        "matches": _compact_rows(rows),
    }


def uex_result_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)