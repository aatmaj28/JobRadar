"""Ashby public posting API: api.ashbyhq.com/posting-api/job-board/<org>"""
from __future__ import annotations

from urllib.parse import quote

from ..models import Board, Job
from ..text import html_to_text
from . import BoardDown, BoardGone

API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def _jobs(data: dict, org: str, company: str) -> list[Job]:
    out = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False or not j.get("id"):
            continue
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        address = (j.get("address") or {}).get("postalAddress") or {}
        out.append(
            Job(
                source="ashby",
                board=org.lower(),
                job_id=str(j["id"]),
                company=company,
                title=(j.get("title") or "").strip(),
                location="; ".join(x for x in locs if x),
                url=j.get("jobUrl") or j.get("applyUrl") or "",
                posted_at=j.get("publishedAt"),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                extra={
                    "slug": org,
                    "country": address.get("addressCountry"),
                    "employment_type": j.get("employmentType"),
                    "remote": j.get("isRemote"),
                },
            )
        )
    return out


def fetch(http, board: Board, is_seen=None) -> list[Job]:
    r = http.get(API.format(org=quote(board.slug)))
    if r.status_code == 404:
        raise BoardGone(board.key)
    if r.status_code >= 400:
        raise BoardDown(f"{board.key} HTTP {r.status_code}")
    return _jobs(r.json(), board.slug, board.company)


def describe(http, job: Job) -> None:
    org = job.extra.get("slug") or job.board
    r = http.get(API.format(org=quote(org)))
    if not r.ok:
        return
    for fresh in _jobs(r.json(), org, job.company):
        if fresh.job_id.lower() == job.job_id.lower():
            job.description = fresh.description
            job.extra.setdefault("country", fresh.extra.get("country"))
            job.extra.setdefault("employment_type", fresh.extra.get("employment_type"))
            return
