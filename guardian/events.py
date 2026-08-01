"""Append-only event log — the audit trail and the dashboard's data source.

Every decision, judge review, wallet result, snapshot and system event is one
JSON line. The dashboard polls this file; nothing is ever rewritten or deleted.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(self, type_: str, **fields: Any) -> dict:
        event = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "type": type_,
            **fields,
        }
        line = json.dumps(event, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return event

    def tail(self, n: int = 500, after: int = 0) -> list[dict]:
        events: list[dict] = []
        with self._lock:
            if not self.path.exists():
                return events
            lines = self.path.read_text(encoding="utf-8").splitlines()
        for line in lines[after:]:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events[-n:]

    def cursor(self) -> int:
        if not self.path.exists():
            return 0
        with self._lock:
            try:
                return sum(1 for _ in self.path.open(encoding="utf-8"))
            except OSError:
                return 0

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()
