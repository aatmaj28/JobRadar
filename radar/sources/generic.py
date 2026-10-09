"""Best effort description for postings on custom career sites (Google, Amazon, ...).

Many of these pages are rendered by JavaScript, so this sometimes gets little
text. That's fine: scoring still works from the title, just with less signal.
"""
from __future__ import annotations

from ..models import Job
from ..text import clip, html_to_text

MAX_CHARS = 12000


def fetch(http, board, is_seen=None):  # pragma: no cover - not a board source
    return []


def describe(http, job: Job) -> None:
    if not job.url.startswith("http"):
        return
    r = http.get(job.url, timeout=12, headers={"Accept": "text/html,application/xhtml+xml"})
    if not r.ok or "html" not in r.headers.get("Content-Type", ""):
        return
    text = html_to_text(r.text[:400_000])
    if len(text) > 400:  # ignore "please enable JavaScript" shells
        job.description = clip(text, MAX_CHARS)
