"""Application tracker: a Google Sheet behind a tiny Apps Script web app (scripts/tracker_apps_script.gs)."""
from __future__ import annotations


class SheetTracker:
    def __init__(self, url: str, token: str, http, log=print):
        self.url, self.token, self.http, self.log = url, token, http, log

    def add(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        try:
            r = self.http.post(self.url, json={"token": self.token, "rows": rows}, timeout=30)
            if not r.ok:
                self.log(f"  sheet tracker HTTP {r.status_code}")
                return 0
            try:
                return int(r.json().get("added", 0))
            except ValueError:
                return 0
        except Exception as exc:
            self.log(f"  sheet tracker failed: {exc}")
            return 0


def row(job, score, origin: str) -> dict:
    return {
        "key": job.key,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "score": score.score,
        "why": score.why,
        "source": origin,
        "url": job.url,
    }
