"""Text and date helpers."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone


def html_to_text(s: str | None) -> str:
    """Turn job description HTML into readable plain text."""
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse sends escaped HTML
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


_ABBREV_END = re.compile(r"(?:\b[A-Za-z]\.)+$|\b(?:e\.g|i\.e|etc|vs|incl|approx|inc|jr|sr|dr|mr|mrs|st|no)\.$", re.I)
_BULLET = re.compile(r"^(?:[•·▪●◦*]|[-–]\s|\d{1,2}[.)]\s)\s*")


def sentences(text: str | None) -> list[tuple[str, bool, bool]]:
    """Split description text into (sentence, starts its line, line is a bullet). A new line always ends a
    sentence; so do . ! ? ; except after abbreviations like "U.S." and "8 U.S.C."."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        bullet = bool(_BULLET.match(line))
        line = _BULLET.sub("", line, count=1).strip()
        start, first = 0, True
        for m in re.finditer(r"[.!?;](?=\s|$)", line):
            if line[m.start()] == "." and _ABBREV_END.search(line[start:m.end()]):
                continue
            piece = line[start:m.end()].strip()
            if piece:
                out.append((piece, first, bullet))
                first = False
            start = m.end()
        tail = line[start:].strip()
        if tail:
            out.append((tail, first, bullet))
    return out


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def iso_from_unix(ts) -> str | None:
    try:
        return iso(datetime.fromtimestamp(float(ts), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def iso_from_ms(ms) -> str | None:
    try:
        return iso_from_unix(float(ms) / 1000.0)
    except (TypeError, ValueError):
        return None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(posted_at: str | None, now: datetime | None = None) -> float | None:
    dt = parse_iso(posted_at)
    if dt is None:
        return None
    return max(0.0, ((now or now_utc()) - dt).total_seconds() / 86400.0)


_REL = re.compile(r"(\d+)\+?\s*days?\s*ago", re.I)


def workday_posted(text: str | None, now: datetime | None = None) -> str | None:
    """Workday says 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    if not text:
        return None
    now = now or now_utc()
    t = text.lower()
    if "today" in t:
        return iso(now)
    if "yesterday" in t:
        return iso(now - timedelta(days=1))
    m = _REL.search(t)
    if m:
        return iso(now - timedelta(days=int(m.group(1))))
    return None


def posted_label(posted_at: str | None, now: datetime | None = None) -> str:
    d = age_days(posted_at, now)
    if d is None:
        return "posted date unknown"
    if d < 1:
        return "posted today"
    if d < 2:
        return "posted yesterday"
    return f"posted {int(d)} days ago"


def dehyphen(s: str) -> str:
    """Aatmaj prefers copy without hyphens or dashes."""
    s = re.sub(r"\s+[-–—]+\s+", ", ", s)
    s = re.sub(r"[–—]", ", ", s)
    s = re.sub(r"(?<=\w)-(?=\w)", " ", s)
    s = re.sub(r",\s*,", ",", s)
    return re.sub(r"\s{2,}", " ", s).strip()
