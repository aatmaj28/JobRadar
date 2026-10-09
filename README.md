# Job Radar

Finds new US entry level AI, ML and software roles within about 15 minutes of posting, scores each one against your master files, and emails you the good ones with everything you need to apply fast.

**Each email has:** fit score and why, which EXP and PROJ entries to lead with, an Apply button, LinkedIn searches for NEU alumni, MUJ alumni and recruiters at that company, two short referral notes, and a block you paste straight into your Resume Maker project (or the finished resume PDF, see below).

**It skips** senior and level II+ roles, internships, contract roles, anything asking for more than 2 years, and postings that say no sponsorship, citizens only, or need a clearance.

**Where jobs come from:** about 220 companies in companies.yaml (checked every 15 minutes), plus up to 2,000 more Greenhouse, Lever, Ashby and Workday boards found in the SimplifyJobs New Grad feed (checked about every 45 minutes), plus the feed itself (that's how Google, Meta, Amazon and Microsoft roles come in).

## Setup (about 20 minutes)

1. **Private repo.** Create a new **private** GitHub repo called `job-radar` and push this folder (in Claude Code: "push this folder to a new private GitHub repo called job-radar"). It must stay private: your contact details and work authorization answers are in it.
2. **Gmail app password.** Signed in as aatmajsalunke@gmail.com: Google Account > Security > turn on 2 Step Verification > search "App passwords" > create one called Job Radar > copy the 16 characters. Never paste it into code.
3. **Groq key.** console.groq.com/keys (you already have one).
4. **Tracker sheet.** Make a Google Sheet as aatmajsalunke@gmail.com, then follow the 4 steps at the top of `scripts/tracker_apps_script.gs`.
5. **Secrets.** Repo > Settings > Secrets and variables > Actions > New repository secret:
   - `GROQ_API_KEY`
   - `GMAIL_ADDRESS` = aatmajsalunke@gmail.com
   - `GMAIL_APP_PASSWORD`
   - `SHEET_WEBHOOK_URL` and `SHEET_TOKEN`
   - optional: `NOTIFY_EMAIL` if you want the emails sent to a different inbox
6. **Turn it on.** Actions tab > job-radar > Run workflow:
   - mode **test**: you get one test email confirming Groq, email and the sheet
   - mode **validate**: an email listing any companies.yaml entries that look wrong
   - mode **run**: the first real run. It quietly learns what's already posted and emails you a spreadsheet of every matching role open right now. Apply to those first.
   After that it runs by itself all day.
7. **Autofill.** Install the Simplify Copilot browser extension for Workday and Greenhouse forms. Keep ANSWER_BANK.md open for the rest.

## Resumes: paste block now, automatic PDFs later

Groq's free plan allows about 8K tokens per request for these models, and your resume prompt plus files is about 22K. So on the free plan each email ends with a paste block in your Resume Maker project's exact input format: paste, get the LaTeX, Overleaf, done. No more copying job descriptions by hand.

For finished PDFs in the same email thread, either add a card on Groq (Developer plan) and set `llm.groq_plan: developer` in config.yaml. That's roughly a cent per resume on gpt-oss-120b. Or add a Claude key and set `llm.provider: anthropic`. The radar compiles the LaTeX, fixes compile errors and second pages, and replies in the job's thread.

## Daily use
- Apply from the email within the hour, then send one of the referral notes.
- In the sheet, set Status to Applied. It stamps the date and emails you a follow up reminder a week later at 8am.
- Getting too many or too few emails? Change `scoring.min_score_to_ping` in config.yaml (6 is the default).

## Handy settings (config.yaml)
- `titles`: what to include and exclude
- `entry_level.max_years_required`: 2
- `work_authorization`: both skips are on
- `notify.max_pings_per_day`: 30, anything extra arrives as one summary email
- `skip_companies`: names you never want to see

## Run it on your laptop (Windows)
```
py -m pip install -r requirements.txt
copy .env.example .env      (then fill it in)
py -m radar test --dry
py -m radar run --dry
```
`--dry` prints the emails instead of sending them.

## Good to know
- Sponsorship wording varies, so a few postings may slip through. The AI score also treats them as dealbreakers, and you can always skip one.
- Companies that don't exist switch themselves off after 3 misses. Run mode validate once a month.
- GitHub's free plan gives private repos 2,000 Actions minutes a month. Checking every 15 minutes from 8am to 11:30pm on weekdays uses roughly 1,450 to 1,800, so glance at Settings > Billing after the first week. The GitHub Student Pack includes Pro, which gives 3,000. GitHub can also delay scheduled runs by a few minutes when it's busy.
- GitHub schedules run on UTC time. After Nov 1, swap in the winter lines from the comment at the top of .github/workflows/radar.yml to keep 8am to 11:30pm ET.
- Mode backlog rebuilds the open roles spreadsheet with every check applied (years asked and sponsorship) and emails it. It reads every description, so it takes about 5 minutes.
- The radar's memory lives in the Actions cache. Mode reset wipes it, and the next run sends the open roles spreadsheet again.
- Update LinkedIn to match your resume: DASH title "AI Software Engineer Research Assistant", LiquidPixels title "AI Training Data Engineer", and Borealis as "AI Engineer, Borealis Global Advisory LLC" from May 2025.
