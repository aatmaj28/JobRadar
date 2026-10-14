"""python -m radar [run|prep|test|validate|backlog|reset] [--dry]"""
from __future__ import annotations

import argparse
import os
import sys

from . import pipeline
from .config import ROOT, load_config
from .http import Http
from .llm import make_llm
from .notify import Discord, Email, Notifier, Telegram
from .pipeline import Ctx
from .prep import Preparer
from .profile import Profile
from .scoring import KeywordScorer, LLMScorer
from .state import State
from .tracker import SheetTracker

STATE_FILE = ROOT / "state" / "radar.json"


def load_dotenv(path) -> None:
    """Local runs: read KEY=value lines from .env (never commit that file)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def build(cfg: dict, dry: bool) -> Ctx:
    env = os.environ
    http = Http(timeout=float(cfg["http"]["timeout"]))
    profile = Profile.load(ROOT / cfg["profile"]["dir"], cfg["profile"].get("files"))
    missing = profile.missing()
    if missing:
        sys.exit(f"Missing profile files: {missing}")
    keyword = KeywordScorer(profile, cfg["referrals"]["default_hook"], cfg.get("earliest_start"))
    llm = make_llm(cfg, env)
    scorer = keyword
    if llm is not None and cfg["scoring"].get("use_ai", True):
        sc = cfg["scoring"]
        scorer = LLMScorer(llm, sc["model"], profile, sc.get("dealbreakers") or [], keyword,
                           batch_size=int(sc.get("batch_size", 6)), max_jobs=int(sc.get("max_ai_jobs_per_run", 12)))
    email = None
    if env.get("GMAIL_ADDRESS") and env.get("GMAIL_APP_PASSWORD"):
        email = Email(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"].replace(" ", ""), env.get("NOTIFY_EMAIL") or None)
    telegram = Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], http) if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID") else None
    discord = Discord(env["DISCORD_WEBHOOK_URL"], http) if env.get("DISCORD_WEBHOOK_URL") else None
    notifier = Notifier(email, telegram, discord, dry=dry)
    tracker = SheetTracker(env["SHEET_WEBHOOK_URL"], env.get("SHEET_TOKEN", ""), http) if env.get("SHEET_WEBHOOK_URL") and not dry else None
    ctx = Ctx(cfg=cfg, state=State.load(STATE_FILE), http=http, notifier=notifier, profile=profile, scorer=scorer,
              keyword_scorer=keyword, tracker=tracker, llm=llm)
    if llm is not None and cfg["prep"].get("enabled", True):
        ctx.prep_enabled = Preparer(llm, cfg["prep"]["model"], profile, ctx.out_dir, cfg["me"]["file_prefix"]).fits()
    return ctx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m radar", description="Job radar")
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "prep", "test", "validate", "backlog", "reset"])
    parser.add_argument("--dry", action="store_true", help="print pings here instead of sending them")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    cfg = load_config()

    if args.mode == "reset":
        State(STATE_FILE).save()  # a blank memory, so the saved cache really is fresh
        print("Memory cleared. The next run starts fresh and sends the open roles spreadsheet again.")
        return 0

    ctx = build(cfg, args.dry)
    if args.mode in ("run", "prep") and not ctx.notifier.enabled and not args.dry:
        # Without email secrets the first run would quietly learn every open job and you'd never get
        # the open roles email, so wait until GMAIL_ADDRESS and GMAIL_APP_PASSWORD exist.
        print("No email secrets yet (GMAIL_ADDRESS and GMAIL_APP_PASSWORD), so this run is skipped. "
              "Add them, or use --dry to print instead.")
        set_output("prep_needed", "false")
        return 0
    if not ctx.notifier.enabled and not args.dry:
        print("No email secrets found, so messages print here instead.")
    if args.mode == "run":
        stats = pipeline.run(ctx)
        ctx.state.save()
        set_output("prep_needed", "true" if stats["queued_resumes"] else "false")
    elif args.mode == "prep":
        preparer = Preparer(ctx.llm, cfg["prep"]["model"], ctx.profile, ctx.out_dir, cfg["me"]["file_prefix"])
        stats = pipeline.prep(ctx, preparer)
        ctx.state.save()
    elif args.mode == "test":
        stats = pipeline.test(ctx)
    elif args.mode == "backlog":
        stats = pipeline.backlog(ctx)
    else:
        stats = pipeline.validate(ctx)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
