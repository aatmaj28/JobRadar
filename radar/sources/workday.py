"""Workday career sites.

Every *.myworkdayjobs.com site is backed by a public JSON API:
  POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
  body {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
With an empty search the newest postings come first, so a couple of pages
per run is enough to catch everything new.
"""
from __future__ import annotations

from ..ats_urls import workday_api_base, workday_parts, workday_public_base
from ..models import Board, Job
from ..text import html_to_text, workday_posted
from . import BoardDown, BoardGone

PAGE = 20
MAX_PAGES_STEADY = 3  # stop early once a page has nothing new
MAX_PAGES_SEED = 5  # first look at a board: learn the newest 100 postings


def fetch(http, board: Board, is_seen=None) -> list[Job]:
    _host, tenant, site = workday_parts(board.slug)
    api = workday_api_base(board.slug) + "/jobs"
    public = workday_public_base(board.slug)
    seeding = is_seen is None
    max_pages = MAX_PAGES_SEED if seeding else MAX_PAGES_STEADY
    jobs: list[Job] = []
    for page in range(max_pages):
        body = {"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": ""}
        r = http.post(api, json=body, headers={"Content-Type": "application/json"})
        if r.status_code in (404, 410):
            raise BoardGone(board.key)
        if r.status_code == 422:  # Workday maintenance window
            raise BoardDown(f"{board.key} maintenance")
        if r.status_code >= 400:
            raise BoardDown(f"{board.key} HTTP {r.status_code}")
        posts = r.json().get("jobPostings") or []
        fresh_on_page = 0
        for p in posts:
            path = p.get("externalPath") or ""
            if not path:
                continue
            job = Job(
                source="workday",
                board=f"{tenant}/{site}".lower(),
                job_id=path.rstrip("/").rsplit("/", 1)[-1],
                company=board.company,
                title=(p.get("title") or "").strip(),
                location=(p.get("locationsText") or "").strip(),
                url=public + path,
                posted_at=workday_posted(p.get("postedOn")),
                extra={"slug": board.slug, "path": path, "posted_text": p.get("postedOn") or ""},
            )
            jobs.append(job)
            if seeding or not is_seen(job.key):
                fresh_on_page += 1
        if len(posts) < PAGE or fresh_on_page == 0:
            break
    return jobs


def describe(http, job: Job) -> None:
    slug = job.extra.get("slug")
    path = job.extra.get("path")
    if not slug:
        return
    if not path:  # found via Simplify: rebuild the API path from the public link
        marker = "/job/" if "/job/" in job.url else "/details/"
        if marker not in job.url:
            return
        path = "/job/" + job.url.split(marker, 1)[1].split("?", 1)[0]
    r = http.get(workday_api_base(slug) + path)
    if not r.ok:
        return
    info = r.json().get("jobPostingInfo") or {}
    job.description = html_to_text(info.get("jobDescription"))
    country = (info.get("country") or {}).get("descriptor")
    if country:
        job.extra["country"] = country
    locations = [info.get("location")] + list(info.get("additionalLocations") or [])
    locations = [x for x in locations if x]
    if locations and (not job.location or "location" in job.location.lower()):
        job.location = "; ".join(locations)
    if info.get("timeType"):
        job.extra["commitment"] = info["timeType"]
    if info.get("externalUrl"):
        job.url = info["externalUrl"]
