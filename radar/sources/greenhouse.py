"""Greenhouse public job board API: boards-api.greenhouse.io/v1/boards/<token>/jobs"""
from __future__ import annotations

from ..models import Board, Job
from ..text import html_to_text
from . import BoardDown, BoardGone

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(http, board: Board, is_seen=None) -> list[Job]:
    r = http.get(API.format(token=board.slug))
    if r.status_code == 404:
        raise BoardGone(board.key)
    if r.status_code >= 400:
        raise BoardDown(f"{board.key} HTTP {r.status_code}")
    jobs = []
    for j in r.json().get("jobs") or []:
        if not j.get("id"):
            continue
        jobs.append(
            Job(
                source="greenhouse",
                board=board.slug.lower(),
                job_id=str(j["id"]),
                company=board.company or j.get("company_name") or board.slug,
                title=(j.get("title") or "").strip(),
                location=((j.get("location") or {}).get("name") or "").strip(),
                url=j.get("absolute_url") or "",
                posted_at=j.get("first_published") or j.get("updated_at"),
                extra={"slug": board.slug},
            )
        )
    return jobs


def describe(http, job: Job) -> None:
    token = job.extra.get("slug") or job.board
    r = http.get(API.format(token=token) + f"/{job.job_id}")
    if r.ok:
        job.description = html_to_text(r.json().get("content") or "")
