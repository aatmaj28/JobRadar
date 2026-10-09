"""Referral helpers. Only builds links and text, never touches LinkedIn itself."""
from __future__ import annotations

import re
from urllib.parse import quote

from .models import Job
from .text import dehyphen

NOTE_LIMIT = 300  # LinkedIn connection notes are short; keep drafts under this


def short_role(title: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", "", title or "")
    t = re.sub(r"\b(R|JR|REQ)[-_]?\d{4,}\b", "", t, flags=re.I)
    return dehyphen(t).strip(" ,")


def search_links(job: Job, templates: list[dict]) -> list[tuple[str, str]]:
    company = quote(job.company or "")
    return [(t["label"], t["url"].replace("{company}", company)) for t in templates]


def drafts(job: Job, hook: str, templates: list[str], default_hook: str) -> list[str]:
    """Short notes in Aatmaj's voice. Falls back to a shorter hook if a note runs long."""
    role, company = short_role(job.title), (job.company or "").strip()
    out = []
    for template in templates:
        text = ""
        for h in (hook, default_hook, ""):
            text = template.format(role=role, company=company, hook=dehyphen(h or "")).strip()
            text = re.sub(r"\s{2,}", " ", text)
            if len(text) <= NOTE_LIMIT:
                break
        out.append(text[:NOTE_LIMIT])
    return out
