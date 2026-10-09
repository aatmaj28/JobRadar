"""Loads config.yaml and companies.yaml."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .ats_urls import parse_ats_url
from .models import Board

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "me": {"file_prefix": "Aatmaj_Salunke"},
    "profile": {"dir": "profile", "files": {}},
    "titles": {"include": [], "exclude": []},
    "location": {"allow_unspecified_remote": True},
    "work_authorization": {"skip_no_sponsorship": True, "skip_citizenship_or_clearance": True},
    "entry_level": {"max_years_required": 2},
    "llm": {"provider": "groq", "groq_plan": "free"},
    "skip_companies": [],
    "max_job_age_days": 21,
    "sources": {
        "simplify": True,
        "simplify_max_age_days": 45,
        "curated": True,
        "discovered": True,
        "discovered_buckets": 3,
        "discovered_limit": 900,
    },
    "http": {"workers": 24, "timeout": 20},
    "scoring": {
        "use_ai": True,
        "model": "openai/gpt-oss-20b",
        "batch_size": 6,
        "max_ai_jobs_per_run": 12,
        "min_score_to_ping": 6,
        "dealbreakers": [],
    },
    "prep": {
        "enabled": True,
        "model": "openai/gpt-oss-120b",
        "min_score": 8,
        "cover_letter": False,
        "max_per_run": 3,
        "max_per_day": 10,
    },
    "notify": {"max_pings_per_run": 10, "max_pings_per_day": 30},
    "referrals": {"links": [], "messages": [], "default_hook": ""},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None = None) -> dict:
    path = Path(path or ROOT / "config.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _merge(DEFAULTS, data or {})


def load_companies(path: Path | None = None) -> list[Board]:
    """companies.yaml: greenhouse/lever/ashby map slug -> name, workday maps name -> career site URL."""
    path = Path(path or ROOT / "companies.yaml")
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    boards: dict[str, Board] = {}
    for source in ("greenhouse", "lever", "ashby"):
        for slug, name in (data.get(source) or {}).items():
            b = Board(source, str(slug).strip(), str(name or slug).strip())
            boards[b.key] = b
    for name, url in (data.get("workday") or {}).items():
        ref = parse_ats_url(str(url))
        if ref and ref.source == "workday":
            b = Board("workday", ref.slug, str(name).strip())
            boards[b.key] = b
    return list(boards.values())
