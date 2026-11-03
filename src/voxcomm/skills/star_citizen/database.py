from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

import httpx


REFRESH_INTERVAL = 24 * 60 * 60
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
API_BASE = "https://api.uexcorp.uk/2.0"
RESOURCES = (
    "categories", "companies", "commodities", "commodities_prices_all", "commodities_raw_prices_all",
    "terminals", "vehicles", "vehicles_prices", "vehicles_purchases_prices_all",
    "vehicles_rentals_prices_all", "items_prices_all",
)
_refresh_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def database_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "voxcomm" / "star_citizen_uex.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS records (resource TEXT NOT NULL, data TEXT NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS records_resource ON records(resource)")
    return connection


def last_updated(path: Path | None = None) -> float | None:
    target = path or database_path()
    if not target.exists():
        return None
    with closing(_connect(target)) as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key = 'updated_at'").fetchone()
    return float(row[0]) if row else None


def refresh_due(path: Path | None = None, *, now: float | None = None) -> bool:
    updated = last_updated(path)
    current = time.time() if now is None else now
    return updated is None or current - updated >= REFRESH_INTERVAL


def replace_snapshot(resources: dict[str, list[dict]], path: Path | None = None, *, now: float | None = None) -> None:
    current = time.time() if now is None else now
    with closing(_connect(path or database_path())) as connection, connection:
        connection.execute("DELETE FROM records")
        for resource, rows in resources.items():
            connection.executemany(
                "INSERT INTO records(resource, data) VALUES (?, ?)",
                ((resource, json.dumps(row, ensure_ascii=False)) for row in rows),
            )
        connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('updated_at', ?)", (str(current),))


def read_resource(resource: str, path: Path | None = None) -> list[dict]:
    target = path or database_path()
    if not target.exists():
        return []
    with closing(_connect(target)) as connection:
        rows = connection.execute("SELECT data FROM records WHERE resource = ?", (resource,)).fetchall()
    return [json.loads(row[0]) for row in rows]


class Snapshot:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def last_updated(self) -> float | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = 'updated_at'").fetchone()
        return float(row[0]) if row else None

    def read_resource(self, resource: str) -> list[dict]:
        rows = self.connection.execute("SELECT data FROM records WHERE resource = ?", (resource,)).fetchall()
        return [json.loads(row[0]) for row in rows]


@contextmanager
def read_snapshot(path: Path | None = None):
    target = path or database_path()
    if not target.exists():
        yield None
        return
    with closing(_connect(target)) as connection:
        connection.execute("BEGIN")
        yield Snapshot(connection)


def _request_resource(client: httpx.Client, resource: str, params: dict | None = None) -> list[dict]:
    with client.stream("GET", f"{API_BASE}/{resource}/", params=params, headers={"Accept": "application/json"}) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_RESPONSE_BYTES:
                raise ValueError(f"UEX {resource} response exceeded the size limit")
    payload = json.loads(content)
    if (isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("data", ...) is None
            and resource in ("items", "items_attributes") and params and "id_category" in params):
        return []
    if not isinstance(payload, dict) or payload.get("status") != "ok" or not isinstance(payload.get("data"), list):
        status = payload.get("status") if isinstance(payload, dict) else None
        raise ValueError(f"UEX {resource} {params or {}} returned an invalid response (status: {status})")
    rows = payload["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"UEX {resource} returned invalid records")
    return rows


def collect_snapshot(request) -> dict[str, list[dict]]:
    resources = {resource: request(resource) for resource in RESOURCES}
    categories = [row for row in resources["categories"] if row.get("type") == "item"]
    if not categories or not resources["commodities"] or not resources["vehicles"] or not resources["terminals"]:
        raise ValueError("UEX returned an incomplete catalog")
    resources["items"] = []
    resources["items_attributes"] = []
    for category in categories:
        category_id = category.get("id")
        if not isinstance(category_id, int) or isinstance(category_id, bool):
            raise ValueError("UEX returned an invalid category")
        params = {"id_category": category_id}
        resources["items"].extend(request("items", params))
        resources["items_attributes"].extend(request("items_attributes", params))
    if not resources["items"] or not resources["items_prices_all"]:
        raise ValueError("UEX returned an incomplete item catalog")
    return resources


def refresh(path: Path | None = None, *, request=None, now: float | None = None) -> bool:
    target = path or database_path()
    if not refresh_due(target, now=now):
        return False
    if request is not None:
        snapshot = collect_snapshot(request)
    else:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            last_request = 0.0

            def limited_request(resource, params=None):
                nonlocal last_request
                delay = 0.65 - (time.monotonic() - last_request)
                if delay > 0:
                    time.sleep(delay)
                last_request = time.monotonic()
                return _request_resource(client, resource, params)

            snapshot = collect_snapshot(limited_request)
    replace_snapshot(snapshot, target, now=now)
    return True


def start_refresh_if_due() -> None:
    def run():
        if not _refresh_lock.acquire(blocking=False):
            return
        try:
            refresh()
        except Exception:
            _logger.exception("UEX background refresh failed; retaining the previous snapshot")
        finally:
            _refresh_lock.release()

    threading.Thread(target=run, name="uex-database-refresh", daemon=True).start()