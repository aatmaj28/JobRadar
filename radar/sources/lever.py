"""Lever public postings API: api.lever.co/v0/postings/<site>?mode=json"""
from __future__ import annotations

from ..models import Board, Job
from ..text import html_to_text, iso_from_ms
from . import BoardDown, BoardGone

API = "https://api.lever.co/v0/postings/{site}"


def _description(p: dict) -> str:
    parts = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
    for block in p.get("lists") or []:
        parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
    parts.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
    return "\n\n".join(x.strip() for x in parts if x and x.strip())


def _job(p: dict, site: str, company: str) -> Job:
    cats = p.get("categories") or {}
    locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
    return Job(
        source="lever",
        board=site.lower(),
        job_id=str(p.get("id")),
        company=company,
        title=(p.get("text") or "").strip(),
        location="; ".join(locs),
        url=p.get("hostedUrl") or p.get("applyUrl") or "",
        posted_at=iso_from_ms(p.get("createdAt")),
        description=_description(p),
        extra={
            "slug": site,
            "country": p.get("country"),
            "commitment": cats.get("commitment"),
            "workplace": p.get("workplaceType"),
        },
    )


def fetch(http, board: Board, is_seen=None) -> list[Job]:
    r = http.get(API.format(site=board.slug), params={"mode": "json"})
    if r.status_code == 404:
        raise BoardGone(board.key)
    if r.status_code >= 400:
        raise BoardDown(f"{board.key} HTTP {r.status_code}")
    data = r.json()
    if isinstance(data, dict):  # {"ok": false, "error": "Document not found"}
        raise BoardGone(board.key)
    return [_job(p, board.slug, board.company) for p in data if p.get("id")]


def describe(http, job: Job) -> None:
    site = job.extra.get("slug") or job.board
    r = http.get(API.format(site=site) + f"/{job.job_id}", params={"mode": "json"})
    if r.ok and isinstance(r.json(), dict):
        fresh = _job(r.json(), site, job.company)
        job.description = fresh.description
        job.extra.setdefault("country", fresh.extra.get("country"))
        job.extra.setdefault("commitment", fresh.extra.get("commitment"))
