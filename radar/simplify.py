"""SimplifyJobs New Grad Positions feed.

The repo github.com/SimplifyJobs/New-Grad-Positions keeps every listing in
.github/scripts/listings.json. We use it two ways:
  * as a job source (new US new grad roles across thousands of companies), and
  * to discover company boards on Greenhouse/Lever/Ashby/Workday that are
    actively hiring new grads, which we then poll directly for *all* their
    roles, minutes after they post.
"""
from __future__ import annotations

import time

from .ats_urls import parse_ats_url
from .models import Board, Job
from .text import iso_from_unix

FEED_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/"
    ".github/scripts/listings.json"
)


def fetch_listings(http, etag: str | None = None, url: str = FEED_URL):
    """Returns (listings or None if unchanged, etag)."""
    headers = {"Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    r = http.get(url, headers=headers, timeout=60)
    if r.status_code == 304:
        return None, etag
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("unexpected Simplify feed format")
    return data, r.headers.get("ETag")


def jobs_from_listings(listings: list[dict], max_age_days: int = 45, now: float | None = None) -> list[Job]:
    now = now or time.time()
    cutoff = now - max_age_days * 86400
    jobs = []
    for item in listings:
        if not item.get("active") or item.get("is_visible") is False:
            continue
        if (item.get("date_posted") or 0) < cutoff:
            continue
        url = item.get("url") or ""
        ref = parse_ats_url(url)
        common = dict(
            company=(item.get("company_name") or "").strip(),
            title=(item.get("title") or "").strip(),
            location="; ".join(item.get("locations") or []),
            url=url,
            posted_at=iso_from_unix(item.get("date_posted")),
            via="simplify",
        )
        if ref and ref.job_id:
            jobs.append(Job(source=ref.source, board=ref.board_id, job_id=ref.job_id, extra={"slug": ref.slug, "sponsorship": item.get("sponsorship")}, **common))
        else:
            jobs.append(Job(source="simplify", board="newgrad", job_id=str(item.get("id") or url), extra={"sponsorship": item.get("sponsorship")}, **common))
    return jobs


def discover_boards(listings: list[dict], since_days: int = 150, now: float | None = None) -> dict[str, dict]:
    """Every ATS board that posted a new grad role recently -> {board_key: info}."""
    now = now or time.time()
    cutoff = now - since_days * 86400
    found: dict[str, dict] = {}
    for item in listings:
        if (item.get("date_posted") or 0) < cutoff:
            continue
        ref = parse_ats_url(item.get("url"))
        if not ref or not ref.slug:
            continue
        info = found.setdefault(
            ref.board_key,
            {"source": ref.source, "slug": ref.slug, "company": (item.get("company_name") or "").strip(), "count": 0, "last": 0},
        )
        info["count"] += 1
        info["last"] = max(info["last"], int(item.get("date_posted") or 0))
    return found


def boards_from_discovered(discovered: dict[str, dict], limit: int) -> list[Board]:
    ranked = sorted(discovered.values(), key=lambda d: (-d.get("count", 0), -d.get("last", 0)))
    return [Board(d["source"], d["slug"], d.get("company") or d["slug"], origin="discovered") for d in ranked[:limit]]
