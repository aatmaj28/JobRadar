"""The radar's memory, kept in state/radar.json (saved between runs with the GitHub Actions cache)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _blank() -> dict:
    return {
        "version": 1,
        "created": int(time.time()),
        "runs": 0,
        "seen": {},  # job key -> last time we saw it open (unix)
        "fingerprints": {},  # company|title -> last pinged (unix)
        "boards": {},  # board key -> {"seeded": ts, "fails": n, "dead": bool, "errors": n}
        "simplify": {"etag": None, "seeded": False},
        "discovered": {},  # board key -> {"source", "slug", "company", "count", "last"}
        "daily": {"date": "", "preps": 0, "pings": 0},
    }


class State:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data = data or _blank()

    @classmethod
    def load(cls, path: Path) -> "State":
        path = Path(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                merged = {**_blank(), **data}
                return cls(path, merged)
            except (ValueError, OSError):
                pass
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    # seen jobs
    def is_seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, keys, now: int | None = None) -> None:
        """Remember keys as of now. Jobs still open keep getting refreshed, closed ones age out."""
        now = now or int(time.time())
        seen = self.data["seen"]
        for k in keys:
            seen[k] = now

    # one role posted in many cities
    def fingerprint_recent(self, fp: str, days: float, now: int | None = None) -> bool:
        ts = self.data["fingerprints"].get(fp)
        return ts is not None and (now or time.time()) - ts < days * 86400

    def mark_fingerprint(self, fp: str, now: int | None = None) -> None:
        self.data["fingerprints"][fp] = now or int(time.time())

    # boards
    def board(self, key: str) -> dict:
        return self.data["boards"].setdefault(key, {"seeded": 0, "fails": 0, "dead": False, "errors": 0})

    # daily counters
    def today(self, date: str) -> dict:
        daily = self.data["daily"]
        if daily.get("date") != date:
            daily.update({"date": date, "preps": 0, "pings": 0})
        return daily

    def prune(self, keep_days: int = 120, now: int | None = None) -> None:
        cutoff = (now or int(time.time())) - keep_days * 86400
        self.data["seen"] = {k: v for k, v in self.data["seen"].items() if v >= cutoff}
        self.data["fingerprints"] = {k: v for k, v in self.data["fingerprints"].items() if v >= cutoff}
