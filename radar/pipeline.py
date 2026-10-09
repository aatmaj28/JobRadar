"""One radar run: poll -> filter -> score -> ping -> track -> queue resumes. Plus prep, test and validate."""
from __future__ import annotations

import csv
import html
import json
import re
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import simplify
from .config import ROOT, load_companies
from .filters import TitleFilter, company_ok, employment_ok, recent_enough, us_location, work_auth_blocker
from .models import Board, Job
from .prep import paste_block, tex_available
from .referral import drafts, search_links
from .scoring import KeywordScorer, Score, min_years_required
from .sources import BoardGone, describe, fetch_board
from .state import State
from .text import age_days, now_utc, posted_label
from .tracker import row as tracker_row

try:
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # Windows without tzdata
    EASTERN = timezone(timedelta(hours=-5))

NEW_BOARD_RECENT_DAYS = 2  # a board found later: only ping what it posted in the last 2 days
SEED_LIMIT_PER_RUN = 1200


@dataclass
class Ctx:
    cfg: dict
    state: State
    http: object
    notifier: object
    profile: object
    scorer: object
    keyword_scorer: KeywordScorer
    tracker: object | None = None
    llm: object | None = None
    prep_enabled: bool = False
    root: Path = ROOT
    out_dir: Path = ROOT / "out"
    log: object = print
    now: datetime = field(default_factory=now_utc)


class Filters:
    def __init__(self, cfg: dict):
        self.title = TitleFilter(cfg["titles"]["include"], cfg["titles"]["exclude"])
        self.skip = cfg.get("skip_companies") or []
        self.allow_remote = bool(cfg["location"]["allow_unspecified_remote"])
        self.max_age = float(cfg.get("max_job_age_days", 21))
        wa = cfg.get("work_authorization") or {}
        self.skip_no_sponsor = bool(wa.get("skip_no_sponsorship", True))
        self.skip_citizen = bool(wa.get("skip_citizenship_or_clearance", True))
        self.max_years = (cfg.get("entry_level") or {}).get("max_years_required", 2)
        self.dropped = {"no sponsorship or citizenship": 0, "asks for too many years": 0}

    def basic(self, job: Job) -> bool:
        return bool(
            self.title(job.title)
            and employment_ok(job)
            and company_ok(job, self.skip)
            and recent_enough(job, self.max_age)
            and us_location(job, self.allow_remote) is not False
            and not work_auth_blocker(job, self.skip_no_sponsor, self.skip_citizen)
        )

    def final(self, job: Job) -> bool:
        verdict = us_location(job, self.allow_remote)
        if not employment_ok(job) or not (verdict is True or (verdict is None and self.allow_remote)):
            return False
        if work_auth_blocker(job, self.skip_no_sponsor, self.skip_citizen):
            self.dropped["no sponsorship or citizenship"] += 1
            return False
        years = min_years_required(job.description or "")
        if self.max_years is not None and years is not None and years > int(self.max_years):
            self.dropped["asks for too many years"] += 1
            return False
        return True


def local_date(now: datetime) -> str:
    return now.astimezone(EASTERN).strftime("%Y-%m-%d")


def _bucket(key: str, n: int) -> int:
    return zlib.crc32(key.encode()) % max(1, n)


def collect_boards(ctx: Ctx) -> list[Board]:
    src, st = ctx.cfg["sources"], ctx.state
    boards: dict[str, Board] = {}
    if src.get("curated", True):
        for b in load_companies(ctx.root / "companies.yaml"):
            boards[b.key] = b
    if src.get("discovered", True) and st.data["discovered"]:
        n = int(src.get("discovered_buckets", 3))
        current = st.data["runs"] % max(1, n)
        budget = SEED_LIMIT_PER_RUN
        for b in simplify.boards_from_discovered(st.data["discovered"], int(src.get("discovered_limit", 900))):
            if b.key in boards:
                continue
            seeded = bool((st.data["boards"].get(b.key) or {}).get("seeded"))
            if not seeded and budget > 0:
                boards[b.key] = b
                budget -= 1
            elif seeded and _bucket(b.key, n) == current:
                boards[b.key] = b
    return [b for b in boards.values() if not (st.data["boards"].get(b.key) or {}).get("dead")]


def poll_boards(ctx: Ctx, boards: list[Board]):
    st = ctx.state

    def work(board: Board):
        seeded = bool((st.data["boards"].get(board.key) or {}).get("seeded"))
        try:
            return board, fetch_board(ctx.http, board, st.is_seen if seeded else None), None
        except Exception as exc:
            return board, [], exc

    with ThreadPoolExecutor(max_workers=int(ctx.cfg["http"]["workers"])) as pool:
        return list(pool.map(work, boards))


def _dedupe(jobs: list[Job]) -> list[Job]:
    """One entry per role: the same posting from two sources, or one role listed per city."""
    by_fp: dict[str, Job] = {}
    keys = set()
    for j in jobs:
        if j.key in keys:
            continue
        keys.add(j.key)
        first = by_fp.get(j.fingerprint)
        if first is None:
            by_fp[j.fingerprint] = j
        elif j.location and j.location not in first.location:
            first.location = f"{first.location}; {j.location}" if first.location else j.location
    return list(by_fp.values())


def run(ctx: Ctx) -> dict:
    st, cfg, log = ctx.state, ctx.cfg, ctx.log
    now_ts = int(ctx.now.timestamp())
    st.data["runs"] += 1
    first_run = st.data["runs"] == 1
    filt = Filters(cfg)
    observed: list[Job] = []
    backlog: list[Job] = []
    stats = {"run": st.data["runs"], "boards_ok": 0, "boards_missing": 0, "boards_down": 0,
             "simplify_listings": 0, "new": 0, "matched": 0, "pinged": 0, "queued_resumes": 0}

    # 1. Simplify new grad feed (also refreshes the discovered boards)
    if cfg["sources"].get("simplify", True):
        try:
            listings, etag = simplify.fetch_listings(ctx.http, st.data["simplify"].get("etag"))
        except Exception as exc:
            log(f"Simplify feed failed: {exc}")
            listings = None
        if listings is not None:
            st.data["simplify"]["etag"] = etag
            st.data["discovered"] = simplify.discover_boards(listings)
            s_jobs = simplify.jobs_from_listings(listings, int(cfg["sources"].get("simplify_max_age_days", 45)))
            stats["simplify_listings"] = len(s_jobs)
            if not st.data["simplify"]["seeded"]:
                backlog += [j for j in s_jobs if filt.basic(j)]
                st.mark_seen([j.key for j in s_jobs], now_ts)
                st.data["simplify"]["seeded"] = True
            else:
                observed += s_jobs

    # 2. Company boards
    boards = collect_boards(ctx)
    log(f"Checking {len(boards)} boards...")
    for board, jobs, err in poll_boards(ctx, boards):
        info = st.board(board.key)
        if isinstance(err, BoardGone):
            info["fails"] += 1
            stats["boards_missing"] += 1
            if info["fails"] >= 3:
                info["dead"] = True
                log(f"  switched off {board.key} (not found 3 runs in a row)")
            continue
        if err is not None:
            info["errors"] = info.get("errors", 0) + 1
            stats["boards_down"] += 1
            continue
        info["fails"] = 0
        stats["boards_ok"] += 1
        if not info.get("seeded"):
            info["seeded"] = now_ts
            if first_run:
                backlog += [j for j in jobs if filt.basic(j)]
                st.mark_seen([j.key for j in jobs], now_ts)
            else:
                recent = [j for j in jobs if (age_days(j.posted_at, ctx.now) or 99) <= NEW_BOARD_RECENT_DAYS]
                st.mark_seen([j.key for j in jobs if j not in recent], now_ts)
                observed += recent
            continue
        observed += jobs

    # 3. What's new since last time
    fresh, keys_now = [], set()
    for j in observed:
        if j.key not in keys_now:
            keys_now.add(j.key)
            if not st.is_seen(j.key):
                fresh.append(j)
    st.mark_seen(keys_now, now_ts)
    stats["new"] = len(fresh)

    # 4. Free filters, one entry per role, then full details and the strict checks
    kept = [j for j in _dedupe([j for j in fresh if filt.basic(j)]) if not st.fingerprint_recent(j.fingerprint, 14, now_ts)]
    if kept:
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda j: describe(ctx.http, j), kept))
    kept = [j for j in kept if filt.final(j)]
    stats["matched"] = len(kept)
    stats.update({f"skipped: {k}": v for k, v in filt.dropped.items() if v})

    # 5. Score: best keyword matches go to the AI first
    kw = {j.key: ctx.keyword_scorer.score(j) for j in kept}
    kept.sort(key=lambda j: -kw[j.key].score)
    scores = ctx.scorer.score_many(kept) if ctx.scorer is not ctx.keyword_scorer else [kw[j.key] for j in kept]
    for j in kept:
        st.mark_fingerprint(j.fingerprint, now_ts)
    ranked = sorted(zip(kept, scores), key=lambda p: (-p[1].score, p[0].company))

    # 6. Ping, track, queue resumes
    daily = st.today(local_date(ctx.now))
    to_ping = [(j, s) for j, s in ranked if s.score >= int(cfg["scoring"]["min_score_to_ping"])]
    room = max(0, int(cfg["notify"]["max_pings_per_day"]) - daily["pings"])
    limit = min(int(cfg["notify"]["max_pings_per_run"]), room)
    pending, rows = [], []
    for j, s in to_ping[:limit]:
        resume_it = (
            ctx.prep_enabled
            and s.score >= int(cfg["prep"]["min_score"])
            and len(pending) < int(cfg["prep"]["max_per_run"])
            and daily["preps"] + len(pending) < int(cfg["prep"]["max_per_day"])
        )
        links = search_links(j, cfg["referrals"]["links"])
        notes = drafts(j, s.hook, cfg["referrals"]["messages"], cfg["referrals"]["default_hook"])
        labels = [ctx.profile.label(x) for x in s.lead_with]
        refs = ctx.notifier.job(j, s, links, notes, labels, resume_it, paste_block(j))
        rows.append(tracker_row(j, s, "Simplify" if j.via else j.source.title()))
        if resume_it:
            pending.append({"job": j.to_dict(), "score": s.to_dict(), "refs": refs or {}})
        stats["pinged"] += 1
    overflow = to_ping[limit:]
    if overflow:
        _send_overflow(ctx, overflow)
        rows += [tracker_row(j, s, "Simplify" if j.via else j.source.title()) for j, s in overflow]
    daily["pings"] += stats["pinged"]
    if ctx.tracker and rows:
        ctx.tracker.add(rows)

    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    (ctx.out_dir / "pending.json").write_text(json.dumps(pending, indent=1), encoding="utf-8")
    stats["queued_resumes"] = len(pending)

    # 7. First run: everything already open goes out as one spreadsheet
    if first_run:
        _send_backlog(ctx, backlog, len(boards))
    st.prune(now=now_ts)
    return stats


def _send_overflow(ctx: Ctx, overflow) -> None:
    subject = f"Job radar: {len(overflow)} more matches"
    items = [(j, s) for j, s in overflow[:60]]
    body = "".join(f'<li><a href="{html.escape(j.url)}">{html.escape(j.title)}</a>, {html.escape(j.company)} (fit {s.score})</li>' for j, s in items)
    text = "\n".join(f"{s.score}/10  {j.title}, {j.company}  {j.url}" for j, s in items)
    ctx.notifier.text(subject, f"<p>More matches from this run (over today's ping limit):</p><ul>{body}</ul>", text)


def _send_backlog(ctx: Ctx, backlog: list[Job], n_boards: int) -> None:
    jobs = _dedupe(backlog)
    scored = sorted(((j, ctx.keyword_scorer.score(j)) for j in jobs),
                    key=lambda p: (-p[1].score, age_days(p[0].posted_at, ctx.now) or 99))[:1500]
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.out_dir / f"open_roles_{local_date(ctx.now)}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Fit (keyword estimate)", "Company", "Role", "Location", "Posted", "Link"])
        for j, s in scored:
            w.writerow([s.score, j.company, j.title, j.location, posted_label(j.posted_at, ctx.now), j.url])
    text = (f"Job radar is live. It's watching {n_boards} company boards plus the Simplify new grad feed.\n"
            f"{len(scored)} matching US roles are open right now. They're in the attached spreadsheet, best guesses first. "
            "These haven't been checked for sponsorship wording yet, so skim each posting.\n"
            "From now on you'll get one email per new posting that fits.")
    ctx.notifier.text("✅ Job radar is live: open roles inside", "<p>" + html.escape(text).replace("\n", "<br>") + "</p>", text,
                      [path] if scored else [])


def prep(ctx: Ctx, preparer) -> dict:
    """Build resumes queued by the last run, compile them, and reply in each job's email thread."""
    path = ctx.out_dir / "pending.json"
    items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not items:
        return {"built": 0}
    kinds = ["resume"] + (["cover"] if ctx.cfg["prep"].get("cover_letter") else [])
    tasks = [(it, Job.from_dict(it["job"]), k) for it in items for k in kinds]

    def draft(task):
        it, job, kind = task
        try:
            return it, job, kind, preparer.draft(job, kind), None
        except Exception as exc:
            return it, job, kind, None, exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        drafted = list(pool.map(draft, tasks))
    can_compile = tex_available(wait_seconds=480)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda d: d[3] and preparer.finish(d[3], can_compile), drafted))

    built = 0
    for it in items:
        job = Job.from_dict(it["job"])
        mine = [d for d in drafted if d[1].key == job.key]
        files, notes = [], []
        for _it, _job, kind, doc, err in mine:
            if doc is None:
                notes.append(f"{kind} failed: {err}")
                continue
            files.append(doc.pdf_path if doc.ok and doc.pdf_path else doc.tex_path)
            ids = list(dict.fromkeys(re.findall(r"\b(?:EXP|PROJ)-\d+\b", doc.rationale)))
            if ids:
                notes.append(f"{kind} uses " + ", ".join(ids))
            if doc.note:
                notes.append(f"{kind}: {doc.note}")
        if not files:  # model failed: fall back to the paste block
            text = "Couldn't build the resume automatically (" + "; ".join(notes) + ").\n\nPaste this into your Resume Maker project:\n\n" + paste_block(job)
            ctx.notifier.files([], f"Resume for {job.company}", "<pre style='white-space:pre-wrap'>" + html.escape(text) + "</pre>", text, it.get("refs"))
            continue
        built += 1
        text = f"Tailored for {job.title} at {job.company}.\n" + "\n".join(notes) + "\n\nGive it a 30 second look before you upload it."
        ctx.notifier.files(files, f"Resume for {job.company}", "<p>" + html.escape(text).replace("\n", "<br>") + "</p>", text, it.get("refs"))
    daily = ctx.state.today(local_date(ctx.now))
    daily["preps"] += len(items)
    path.write_text("[]", encoding="utf-8")
    return {"built": built, "queued": len(items), "compiled_here": can_compile}


def test(ctx: Ctx) -> dict:
    """Checks every connection once, without touching what the radar remembers."""
    out = {}
    llm_note = "no AI key set, keyword scoring only"
    if ctx.llm is not None:
        try:
            reply = ctx.llm.chat(ctx.cfg["scoring"]["model"], ["You are a test."], [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=300)
            llm_note = f"AI key works ({ctx.cfg['scoring']['model']} replied: {reply[:20]!r})"
        except Exception as exc:
            llm_note = f"AI key problem: {exc}"
    out["ai"] = llm_note
    out["resumes"] = "automatic PDFs" if ctx.prep_enabled else "paste blocks (resume prompt is too big for the current AI plan)"
    if ctx.tracker:
        added = ctx.tracker.add([{"key": f"test:{int(ctx.now.timestamp())}", "company": "TEST ROW", "title": "Delete me",
                                  "location": "", "score": 0, "why": "connection test", "source": "test", "url": ""}])
        out["sheet"] = "row added" if added else "sheet didn't confirm, check the web app URL and token"
    else:
        out["sheet"] = "not set up"
    text = "Job radar test.\n\n" + "\n".join(f"{k}: {v}" for k, v in out.items())
    ctx.notifier.text("✅ Job radar test", "<p>" + html.escape(text).replace("\n", "<br>") + "</p>", text)
    return out


def validate(ctx: Ctx) -> dict:
    """Checks every company in companies.yaml once and reports the ones that look wrong."""
    boards = load_companies(ctx.root / "companies.yaml")
    with ThreadPoolExecutor(max_workers=int(ctx.cfg["http"]["workers"])) as pool:
        results = list(pool.map(lambda b: _probe(ctx, b), boards))
    ok = [b for b, status in results if status == "ok"]
    missing = [b for b, status in results if status == "missing"]
    down = [b for b, status in results if status not in ("ok", "missing")]
    text = (f"{len(ok)} of {len(boards)} company boards work.\n\nNot found (fix or delete in companies.yaml):\n"
            + "\n".join(f"  {b.company}: {b.source} {b.slug}" for b in missing)
            + ("\n\nTemporarily unreachable:\n" + "\n".join(f"  {b.company}: {b.source} {b.slug}" for b in down) if down else ""))
    ctx.notifier.text(f"Job radar: {len(ok)}/{len(boards)} company boards OK", "<pre>" + html.escape(text) + "</pre>", text)
    return {"ok": len(ok), "missing": len(missing), "down": len(down)}


def _probe(ctx: Ctx, board: Board):
    try:
        fetch_board(ctx.http, board, lambda key: True)
        return board, "ok"
    except BoardGone:
        return board, "missing"
    except Exception as exc:
        return board, f"down: {exc}"


def backlog(ctx: Ctx) -> dict:
    """Rebuild the open roles spreadsheet with every check (years asked, sponsorship). Memory isn't touched."""
    cfg, log = ctx.cfg, ctx.log
    filt = Filters(cfg)
    jobs: list[Job] = []
    discovered = ctx.state.data.get("discovered") or {}
    if cfg["sources"].get("simplify", True):
        try:
            listings, _etag = simplify.fetch_listings(ctx.http)
            jobs += simplify.jobs_from_listings(listings, int(cfg["sources"].get("simplify_max_age_days", 45)))
            discovered = simplify.discover_boards(listings)
        except Exception as exc:
            log(f"Simplify feed failed: {exc}")
    boards = {b.key: b for b in load_companies(ctx.root / "companies.yaml")} if cfg["sources"].get("curated", True) else {}
    if cfg["sources"].get("discovered", True):
        for b in simplify.boards_from_discovered(discovered, int(cfg["sources"].get("discovered_limit", 900))):
            boards.setdefault(b.key, b)

    def work(board: Board):
        try:
            return fetch_board(ctx.http, board, None)
        except Exception:
            return []

    log(f"Rebuilding open roles from {len(boards)} boards...")
    with ThreadPoolExecutor(max_workers=int(cfg["http"]["workers"])) as pool:
        for found in pool.map(work, list(boards.values())):
            jobs += found
    candidates = _dedupe([j for j in jobs if filt.basic(j)])
    log(f"Reading {len(candidates)} job descriptions...")
    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(lambda j: describe(ctx.http, j), candidates))
    kept = [j for j in candidates if filt.final(j)]
    scored = sorted(((j, ctx.keyword_scorer.score(j)) for j in kept),
                    key=lambda p: (-p[1].score, age_days(p[0].posted_at, ctx.now) or 99))[:1500]

    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.out_dir / f"open_roles_checked_{local_date(ctx.now)}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Fit (keyword estimate)", "Company", "Role", "Location", "Posted", "Years asked", "Link"])
        for j, sc in scored:
            years = min_years_required(j.description or "")
            w.writerow([sc.score, j.company, j.title, j.location, posted_label(j.posted_at, ctx.now),
                        "not stated" if years is None else f"{years}+", j.url])
    too_many = filt.dropped["asks for too many years"]
    no_sponsor = filt.dropped["no sponsorship or citizenship"]
    text = (f"{len(scored)} open roles passed every check. They're in the attached spreadsheet, best fits first.\n"
            f"Removed {too_many} that ask for more than {filt.max_years} years and {no_sponsor} that rule out sponsorship "
            "or need citizenship or a clearance.\n"
            "The Years asked column shows the lowest number each posting mentions.")
    ctx.notifier.text("📋 Open roles, fully checked", "<p>" + html.escape(text).replace("\n", "<br>") + "</p>", text, [path])
    return {"boards": len(boards), "checked": len(candidates), "kept": len(scored),
            "removed_for_years": too_many, "removed_for_sponsorship": no_sponsor, "csv": str(path)}
