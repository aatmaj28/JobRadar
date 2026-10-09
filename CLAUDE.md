# Job radar: notes for Claude Code

Aatmaj's personal job alert pipeline. Runs on GitHub Actions on a schedule (see .github/workflows/radar.yml).

## Layout
- radar/sources/: one fetcher per ATS (greenhouse, lever, ashby, workday) plus a best effort generic page reader
- radar/simplify.py: SimplifyJobs New Grad feed, and discovery of company boards from its links
- radar/filters.py: title, US location, job type, and work authorization checks (F-1 OPT, needs H-1B later)
- radar/scoring.py: free keyword scorer + batched AI scorer (fits Groq's free plan, about 8K tokens per request)
- radar/prep.py: resume builder that reuses profile/RESUME_MAKER_PROMPT.txt and the three FILES unchanged
- radar/notify.py: Gmail email pings (resume replies land in the same thread); Telegram/Discord optional
- radar/pipeline.py: run, prep, test, validate. radar/state.py: memory saved via the Actions cache

## Commands
- Tests (offline, no network): `python -m unittest discover -s tests -v`
- Local dry run (prints instead of emailing): `python -m radar run --dry` (Windows: `py -m radar run --dry`)
- Secrets for local runs go in .env (copy .env.example). Never commit .env.

## Rules
- profile/ holds Aatmaj's source of truth. Don't change resume content, metrics or the prompts unless he asks.
- User facing copy (emails, outreach notes, answer bank) avoids hyphens and dashes; he prefers it that way.
- Keep AI requests under llm.max_request_tokens (7000 on Groq free). Batch small, fall back to keyword scoring.
- Keep the repo private: it contains his contact details and work authorization answers.
