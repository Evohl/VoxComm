from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .facts import NAVIGATION_INSTANCE_RE, SHIP_EXIT_RE, SHIP_JOIN_RE, SHIP_LEAVE_RE, VEHICLE_ID_SUFFIX_RE


_logger = logging.getLogger(__name__)
SCAN_BYTES = 512 * 1024


def collection_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "voxcomm" / "star_citizen_ships.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=1)
    connection.execute("CREATE TABLE IF NOT EXISTS ship_names (kind TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (kind, name))")
    connection.execute("CREATE TABLE IF NOT EXISTS ship_matches (name TEXT PRIMARY KEY, vehicle_id INTEGER, vehicle_name TEXT, manufacturer TEXT, source TEXT NOT NULL)")
    return connection


def collect_lines(lines: list[str], path: Path | None = None) -> bool:
    names = set()
    for line in lines:
        for pattern in (NAVIGATION_INSTANCE_RE, SHIP_EXIT_RE):
            if match := pattern.search(line):
                name = VEHICLE_ID_SUFFIX_RE.sub("", match["vehicle"])
                if name:
                    names.add(("class", name))
        for pattern in (SHIP_JOIN_RE, SHIP_LEAVE_RE):
            if match := pattern.search(line):
                name = match["channel"].strip()
                if name:
                    names.add(("channel", name))
    if not names:
        return True
    try:
        with closing(_connect(path or collection_path())) as connection, connection:
            connection.executemany("INSERT OR IGNORE INTO ship_names(kind, name) VALUES (?, ?)", sorted(names))
        return True
    except (OSError, sqlite3.Error):
        _logger.exception("Could not store Star Citizen ship names")
        return False


def list_names(path: Path | None = None) -> list[tuple[str, str]]:
    target = path or collection_path()
    if not target.exists():
        return []
    with closing(_connect(target)) as connection:
        return connection.execute("SELECT kind, name FROM ship_names ORDER BY kind, name COLLATE NOCASE, name").fetchall()


def list_matches(path: Path | None = None) -> dict[str, dict]:
    target = path or collection_path()
    if not target.exists():
        return {}
    with closing(_connect(target)) as connection:
        rows = connection.execute("SELECT name, vehicle_id, vehicle_name, manufacturer, source FROM ship_matches").fetchall()
    return {name: {"id": vehicle_id, "name": vehicle_name, "manufacturer": manufacturer, "source": source}
            for name, vehicle_id, vehicle_name, manufacturer, source in rows}


def save_match(name: str, vehicle: dict | None, source: str, path: Path | None = None) -> None:
    if source not in ("llm", "manual"):
        raise ValueError("Invalid ship match source")
    row = (name, vehicle["id"] if vehicle else None, vehicle["name"] if vehicle else None,
           vehicle["manufacturer"] if vehicle else None, source)
    with closing(_connect(path or collection_path())) as connection, connection:
        statement = ("INSERT OR REPLACE" if source == "manual" else "INSERT OR IGNORE")
        connection.execute(f"{statement} INTO ship_matches(name, vehicle_id, vehicle_name, manufacturer, source) VALUES (?, ?, ?, ?, ?)", row)


def retry_unidentified(path: Path | None = None) -> None:
    target = path or collection_path()
    if not target.exists():
        return
    with closing(_connect(target)) as connection, connection:
        connection.execute("DELETE FROM ship_matches WHERE source = 'llm' AND vehicle_id IS NULL")


class ShipLogCollector:
    def __init__(self, path: Path | None = None):
        self.storage = path
        self._path: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""

    def scan(self, path: Path) -> None:
        try:
            with path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if path != self._path or identity != self._identity or stat.st_size < self._offset:
                    self._path, self._identity, self._offset, self._partial = path, identity, 0, b""
                handle.seek(self._offset)
                data = handle.read(SCAN_BYTES)
        except OSError:
            return
        if not data:
            return
        complete, separator, partial = (self._partial + data).rpartition(b"\n")
        if not separator:
            partial = (self._partial + data)[-SCAN_BYTES:]
            lines = []
        else:
            lines = [line.rstrip(b"\r").decode("utf-8", "replace") for line in complete.split(b"\n")]
        if collect_lines(lines, self.storage):
            self._offset += len(data)
            self._partial = partial