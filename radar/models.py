"""Small data classes shared across the radar."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Board:
    """One company job board on one applicant tracking system (ATS)."""

    source: str  # greenhouse | lever | ashby | workday
    slug: str  # token / site / org, or "host/tenant/site" for workday
    company: str
    origin: str = "curated"  # curated | discovered

    @property
    def key(self) -> str:
        return f"{self.source}:{self.slug}".lower()


@dataclass
class Job:
    source: str  # greenhouse | lever | ashby | workday | simplify
    board: str  # board id inside the source
    job_id: str
    company: str
    title: str
    location: str = ""
    url: str = ""
    posted_at: str | None = None  # ISO 8601, when known
    description: str = ""
    via: str = ""  # "simplify" when found through the Simplify feed
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id. The same posting gets the same key no matter where we found it."""
        return f"{self.source}:{self.board}:{self.job_id}".lower()

    @property
    def fingerprint(self) -> str:
        """Company + title. Catches one role posted once per city."""
        return f"{norm_company(self.company)}|{norm_title(self.title)}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**d)


_COMPANY_NOISE = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|holdings|technologies|technology)\b\.?"
)


def norm_company(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = _COMPANY_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
