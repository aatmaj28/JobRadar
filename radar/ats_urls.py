"""Recognize ATS links.

Used two ways:
  1. A Simplify listing that points at Greenhouse/Lever/Ashby/Workday gets the
     same Job.key as the same posting found by polling that board directly,
     so you never get pinged twice for one job.
  2. Discovery: every ATS link in the Simplify feed tells us about a company
     board worth polling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")  # Workday locales look like en-US
WD_HOST = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", re.I)
WD_SITE_HOST = re.compile(r"^(wd\d+)\.myworkdaysite\.com$", re.I)


@dataclass(frozen=True)
class AtsRef:
    source: str  # greenhouse | lever | ashby | workday
    slug: str  # what the API needs
    job_id: str | None

    @property
    def board_id(self) -> str:
        if self.source == "workday":
            _host, tenant, site = self.slug.split("/", 2)
            return f"{tenant}/{site}".lower()
        return self.slug.lower()

    @property
    def board_key(self) -> str:
        return f"{self.source}:{self.slug}".lower()


def _digits(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"\d+", s)
    return m.group(0) if m else None


def parse_ats_url(url: str | None) -> AtsRef | None:
    if not url:
        return None
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [unquote(p) for p in u.path.split("/") if p]
    query = parse_qs(u.query)

    if host in GREENHOUSE_HOSTS:
        if parts and parts[0] == "embed":
            token = (query.get("for") or [None])[0]
            job_id = _digits((query.get("token") or [None])[0])
        else:
            token = parts[0] if parts else None
            job_id = _digits(parts[2]) if len(parts) >= 3 and parts[1] == "jobs" else None
        return AtsRef("greenhouse", token, job_id) if token else None

    if host == "jobs.lever.co" and parts:
        return AtsRef("lever", parts[0], parts[1] if len(parts) > 1 else None)

    if host == "jobs.ashbyhq.com" and parts:
        return AtsRef("ashby", parts[0], parts[1] if len(parts) > 1 else None)

    m = WD_HOST.match(host)
    if m:
        tenant = m.group(1)
        rest = parts[1:] if parts and LOCALE.match(parts[0]) else parts
        if not rest:
            return None
        site = rest[0]
        job_id = rest[-1] if any(p in ("job", "details") for p in rest[1:-1]) else None
        return AtsRef("workday", f"{host}/{tenant}/{site}", job_id)

    m = WD_SITE_HOST.match(host)
    if m:
        rest = parts[1:] if parts and LOCALE.match(parts[0]) else parts
        if len(rest) >= 3 and rest[0] == "recruiting":
            tenant, site = rest[1], rest[2]
            job_id = rest[-1] if any(p in ("job", "details") for p in rest[3:-1]) else None
            return AtsRef("workday", f"{host}/{tenant}/{site}", job_id)
    return None


def workday_parts(slug: str) -> tuple[str, str, str]:
    host, tenant, site = slug.split("/", 2)
    return host, tenant, site


def workday_api_base(slug: str) -> str:
    host, tenant, site = workday_parts(slug)
    return f"https://{host}/wday/cxs/{tenant}/{site}"


def workday_public_base(slug: str) -> str:
    host, tenant, site = workday_parts(slug)
    if host.endswith("myworkdaysite.com"):
        return f"https://{host}/recruiting/{tenant}/{site}"
    return f"https://{host}/{site}"
