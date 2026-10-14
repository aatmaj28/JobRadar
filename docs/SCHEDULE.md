# Schedule: on time runs with cron-job.org

GitHub's own scheduler can't be trusted for this. From Oct 9 to Oct 13, 2026, it started **19 of 202** scheduled slots (9%). The median run arrived 76 minutes late, and the worst 6.8 hours late. Moving the crons off round minutes (they've been at :07, :22, :37 and :52 since `fdbbf0a`) didn't help.

So the radar is started from outside:

* **On time:** cron-job.org (free) calls GitHub's `workflow_dispatch` API on the schedule below. Runs it starts show up as `run (cron-job.org)` in the Actions tab and start within seconds.
* **Backup:** GitHub's own crons still fire a few times a day, at :44. Each one first checks whether the radar ran in the last 130 minutes and stops if it did, which costs 1 billed minute. If cron-job.org is down, they keep the radar going a few times a day.
* **Never overlapping:** the workflow's `concurrency: job-radar` group makes a second run wait for the first.

Nothing is missed when a run is late or skipped. Each run picks up every posting it hasn't seen yet, so lateness only delays the email.

## Minutes budget (GitHub Free: 2,000 a month for private repos)

GitHub bills each run rounded up to whole minutes. Measured on this repo's scheduled runs:
* **Average:** 67 seconds, which bills 1.42 minutes.
* **Busy weekday runs:** 85 seconds, which bills 1.71 minutes.
* **Where the time goes:** setup is about 10 seconds; polling about 850 boards is the rest.

| Schedule | Runs a month | Minutes a month |
|---|---|---|
| Every 15 min weekdays 8am to 11:30pm ET, every 2 h weekends (the old one) | 1,421 | 2,000 to 2,600: **over Free** |
| **Every 30 min weekdays, every 2 h weekends (default)** | 747 | 1,060 to 1,370 |
| Every 20 min weekdays, every 2 h weekends | 1,094 | 1,550 to 2,000: too tight |
| Backup crons when cron-job.org works (34 a week, 1 minute each) | 148 | 148 |

The default leaves about 500 minutes a month for backup checks, test and validate runs, and backlog runs (about 7 minutes each).

**What 30 minutes costs you:**
* **Speed:** a new posting reaches you about 15 minutes after the radar can see it on average, at most 30 (with 15-minute runs it was about 7.5, at most 15).
* **Discovered boards:** the boards found through the Simplify feed are polled a third per run, so each one is checked about every 90 minutes instead of 45.
* **Your companies:** the companies.yaml boards are still checked every run.

On GitHub Pro, which the Student Pack includes, you get 3,000 minutes. With Pro you can switch to `--every 15` (about 2,000 to 2,600 a month plus backups), but check Settings > Billing after a week.

## Setup (about 10 minutes)

### 1. A GitHub token that can only start this workflow

1. github.com > your photo > **Settings** > **Developer settings** > **Personal access tokens** > **Fine-grained tokens** > **Generate new token**.
2. **Token name:** `JobRadar cron-job.org`. **Expiration:** custom, 1 year, then put a reminder in your calendar to rotate it.
3. **Resource owner:** aatmaj28. **Repository access:** *Only select repositories* > **JobRadar**.
4. **Permissions** > **Repository permissions** > **Actions:** *Read and write*. Leave everything else at *No access*. GitHub adds *Metadata: Read-only* by itself.
5. **Generate token** and copy it (it starts with `github_pat_`).

This token can start, cancel and re-run this repo's workflows and nothing else. It can't read your code, secrets or other repos.

### 2. A cron-job.org account and API key

1. Sign up at https://cron-job.org (free) and confirm your email.
2. In the console: **Settings** > **API** > create an API key and copy it.

### 3. Create the two jobs

From this folder on your laptop:

```
# macOS / Linux
CRONJOB_API_KEY=paste_here GITHUB_DISPATCH_TOKEN=github_pat_paste_here .venv/bin/python scripts/cron_job_org.py

# Windows (cmd)
set CRONJOB_API_KEY=paste_here
set GITHUB_DISPATCH_TOKEN=github_pat_paste_here
py scripts\cron_job_org.py
```

* **What it does:** checks the GitHub token, then creates **JobRadar weekdays** (8:07am to 11:37pm ET, every 30 min) and **JobRadar weekends** (9:07am to 7:07pm ET, every 2 hours). It prints their next run times.
* **Running it again:** updates the same two jobs.
* **Options:** `--every 15` or `--every 20` changes the weekday interval, and `--dry` shows what it would send without changing anything.
* **Or use `.env`:** put `CRONJOB_API_KEY` and `GITHUB_DISPATCH_TOKEN` in `.env` (gitignored, placeholders are already there) and run just `.venv/bin/python scripts/cron_job_org.py`.
* **Where the token lives:** cron-job.org stores it in the jobs' request headers. Don't paste it anywhere else. If you upload `.env` with `gh secret set -f .env` again, remove these two lines first: GitHub doesn't need them.

<details><summary>Or set the jobs up by hand in the cron-job.org console</summary>

Create a cron job with these settings. Labels in the console may differ a little.

* **URL:** `https://api.github.com/repos/aatmaj28/JobRadar/actions/workflows/radar.yml/dispatches`
* **Schedule:** custom. Weekdays Mon to Fri, hours 8 to 23, minutes 7 and 37. Time zone **America/New_York**.
* **Request method:** POST
* **Headers:**
  * `Authorization: Bearer github_pat_...`
  * `Accept: application/vnd.github+json`
  * `X-GitHub-Api-Version: 2022-11-28`
  * `Content-Type: application/json`
* **Request body:** `{"ref": "main", "inputs": {"mode": "run", "source": "cron-job.org"}}`
* **Notifications:** on failure, and when the job gets disabled.

Then copy it as **JobRadar weekends**: Sat and Sun, hours 9, 11, 13, 15, 17 and 19, minute 7.
</details>

### 4. Check it

* **cron-job.org:** each execution's history should show HTTP 200 or 204 from GitHub.
* **GitHub:** the Actions tab should show `run (cron-job.org)` within a minute of each :07 and :37.
* **HTTP 401 or 403:** the token expired or lacks Actions write.
* **HTTP 404:** the token isn't limited to JobRadar, or the URL is wrong.

cron-job.org emails you when a job fails, and it switches a job off after 25 failures in a row.

## Changing the schedule later

* **Interval:** run the script again with a different `--every`.
* **Backup crons:** edit them in `.github/workflows/radar.yml`. They're in UTC and cover 8am to 11:30pm ET whether daylight saving is on or not, so there's nothing to swap in November.
