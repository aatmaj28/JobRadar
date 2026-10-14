"""Create (or update) the cron-job.org jobs that start the radar on time. See docs/SCHEDULE.md.

    CRONJOB_API_KEY=...  GITHUB_DISPATCH_TOKEN=...  python scripts/cron_job_org.py [--every 30] [--dry]

CRONJOB_API_KEY        cron-job.org > Settings > API keys
GITHUB_DISPATCH_TOKEN  fine grained GitHub token: only this repository, Actions: Read and write
--every                minutes between weekday runs: 30 (default, fits GitHub Free), 20 or 15
--dry                  print what would be created, change nothing

Each job POSTs GitHub's workflow_dispatch API, in America/New_York time, so daylight saving is handled.
Running it again updates the same two jobs instead of adding more. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = "aatmaj28/JobRadar"
WORKFLOW = "radar.yml"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
CRON_API = "https://api.cron-job.org"
TZ = "America/New_York"
WEEKDAY_MINUTES = {30: [7, 37], 20: [7, 27, 47], 15: [7, 22, 37, 52]}  # never on the hour


def call(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode() or "{}"
            return r.status, json.loads(text)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def jobs(github_token: str, every: int) -> list[dict]:
    request = {
        "url": DISPATCH_URL,
        "enabled": True,
        "saveResponses": True,
        "requestTimeout": 30,
        "requestMethod": 1,  # POST
        "notification": {"onFailure": True, "onDisable": True},
        "extendedData": {
            "headers": {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
            "body": json.dumps({"ref": "main", "inputs": {"mode": "run", "source": "cron-job.org"}}),
        },
    }
    weekdays = {"timezone": TZ, "expiresAt": 0, "mdays": [-1], "months": [-1], "wdays": [1, 2, 3, 4, 5],
                "hours": list(range(8, 24)), "minutes": WEEKDAY_MINUTES[every]}           # 8:07am to 11:37pm
    weekends = {"timezone": TZ, "expiresAt": 0, "mdays": [-1], "months": [-1], "wdays": [0, 6],
                "hours": [9, 11, 13, 15, 17, 19], "minutes": [7]}                         # 9:07am to 7:07pm
    return [{**request, "title": "JobRadar weekdays", "schedule": weekdays},
            {**request, "title": "JobRadar weekends", "schedule": weekends}]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--every", type=int, choices=sorted(WEEKDAY_MINUTES), default=30)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    cron_key, gh_token = os.environ.get("CRONJOB_API_KEY", ""), os.environ.get("GITHUB_DISPATCH_TOKEN", "")
    wanted = jobs(gh_token or "<GITHUB_DISPATCH_TOKEN>", args.every)
    if args.dry:
        for j in wanted:
            shown = json.loads(json.dumps(j))
            shown["extendedData"]["headers"]["Authorization"] = "Bearer <hidden>"
            print(json.dumps(shown, indent=1))
        return 0
    if not cron_key or not gh_token:
        sys.exit("Set CRONJOB_API_KEY and GITHUB_DISPATCH_TOKEN first (see docs/SCHEDULE.md).")

    # 1. The GitHub token can see this workflow (Actions read comes with Actions write)
    status, _ = call("GET", f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}", gh_token)
    if status != 200:
        sys.exit(f"GitHub token check failed (HTTP {status}). It needs access to {REPO} with Actions: Read and write.")
    print(f"GitHub token can reach {REPO} {WORKFLOW}")

    # 2. Create both jobs, or update them if they already exist
    status, listing = call("GET", f"{CRON_API}/jobs", cron_key)
    if status != 200:
        sys.exit(f"cron-job.org API key check failed (HTTP {status}): {listing}")
    existing = {j.get("title"): j.get("jobId") for j in listing.get("jobs", [])}
    ids = []
    for job in wanted:
        if job["title"] in existing:
            status, out = call("PATCH", f"{CRON_API}/jobs/{existing[job['title']]}", cron_key, {"job": job})
            job_id = existing[job["title"]]
        else:
            status, out = call("PUT", f"{CRON_API}/jobs", cron_key, {"job": job})
            job_id = out.get("jobId")
        if status != 200 or not job_id:
            sys.exit(f"Saving {job['title']!r} failed (HTTP {status}): {out}")
        ids.append((job["title"], job_id))
        print(f"{'Updated' if job['title'] in existing else 'Created'} {job['title']!r} (job {job_id})")

    # 3. Show when they fire next
    eastern = ZoneInfo(TZ)
    for title, job_id in ids:
        status, hist = call("GET", f"{CRON_API}/jobs/{job_id}/history", cron_key)
        nxt = [datetime.fromtimestamp(t, eastern).strftime("%a %b %d %I:%M %p ET") for t in hist.get("predictions", [])]
        print(f"  {title}: next runs {', '.join(nxt) if nxt else '(see the cron-job.org dashboard)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
