from __future__ import annotations

import os
from pathlib import Path

INITIAL_TAIL_BYTES = 256 * 1024
RECENT_LOG_MAX_BYTES = 16 * 1024 * 1024
RECENT_LOG_CHUNK_BYTES = 64 * 1024


class LogReader:
    """Returns complete lines appended to a log since the previous call."""

    def __init__(self, path: Path, initial_tail_bytes: int = INITIAL_TAIL_BYTES):
        self.path = path
        self._initial_tail_bytes = initial_tail_bytes
        self._offset: int | None = None
        self._partial = b""

    def read_new_lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        drop_fragment = False
        if self._offset is None:
            self._offset = max(0, size - self._initial_tail_bytes)
            drop_fragment = self._offset > 0
        elif size < self._offset:
            self._offset = 0
            self._partial = b""
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            data = handle.read()
        self._offset += len(data)
        data = self._partial + data
        if drop_fragment:
            _, _, data = data.partition(b"\n")
        complete, separator, self._partial = data.rpartition(b"\n")
        if not separator:
            return []
        return [line.rstrip(b"\r").decode("utf-8", "replace") for line in complete.split(b"\n")]


class RecentLogReader:
    def __init__(self, path: Path, max_lines: int = 10000):
        self.path = path
        self.max_lines = max_lines
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._partial = b""

    def read_new_lines(self) -> tuple[bool, list[str]]:
        try:
            with self.path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                reset = (self._identity != identity or stat.st_size < self._offset
                         or len(self._partial) + stat.st_size - self._offset > RECENT_LOG_MAX_BYTES)
                if reset:
                    self._identity = identity
                    self._partial = b""
                    offset = stat.st_size
                    chunks = []
                    newline_count = 0
                    lower_bound = max(0, stat.st_size - RECENT_LOG_MAX_BYTES)
                    while offset > lower_bound and newline_count <= self.max_lines:
                        size = min(RECENT_LOG_CHUNK_BYTES, offset - lower_bound)
                        offset -= size
                        handle.seek(offset)
                        chunk = handle.read(size)
                        chunks.append(chunk)
                        newline_count += chunk.count(b"\n")
                    data = b"".join(reversed(chunks))
                    if offset:
                        _, _, data = data.partition(b"\n")
                else:
                    handle.seek(self._offset)
                    data = self._partial + handle.read(stat.st_size - self._offset)
                self._offset = stat.st_size
        except OSError:
            return False, []
        complete, separator, self._partial = data.rpartition(b"\n")
        if not separator:
            return reset, []
        lines = complete.split(b"\n")[-self.max_lines:]
        return reset, [line.rstrip(b"\r").decode("utf-8", "replace") for line in lines]
