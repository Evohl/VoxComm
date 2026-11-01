from __future__ import annotations

from pathlib import Path

INITIAL_TAIL_BYTES = 256 * 1024


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
