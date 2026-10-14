# Investigation: why Pfizer "Translational AI Engineer" got 10/10

Date: 2026-10-13. Read only: no code or config was changed. Everything below was checked against the code at commit `635edb7`, the live posting, and the GitHub Actions logs.

> **Status:** the problems in this report are fixed in `41aa876` and the commit that added this file under `docs/`. Function names and line numbers below refer to `635edb7`. For example, `min_years_required` is now `required_years` in `radar/scoring.py`.

**Posting used.** The request didn't include the JD text, so I used the live posting: Pfizer req 4962398, "Translational AI Engineer", Cambridge MA, from Pfizer's Workday board (`pfizer.wd1.myworkdayjobs.com/PfizerCareers`). The radar reaches it through `companies.yaml`, not through Simplify; the job isn't in the Simplify feed. The relevant lines of the description, after the radar converts it to text:

```
BASIC QUALIFICATIONS:
• PhD with 1+ years of experience OR Master's degree with 5+ years of software or ML engineering experience - OR Bachelors degree and 6 + years of experience and/or equivalent demonstrated experience shipping AI/ML systems for scientific applications.
...
Candidates must be authorized to be employed in the U.S. by any employer.
U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available for this role now or in the future.
... This position requires permanent work authorization in the United States.
```

The last line is the wording quoted in the request. It sits inside a long legal paragraph near the end of the posting. (Correction: the first version of this report said the line wasn't in the posting, because my line search cut that paragraph off.) None of these three lines is caught today (section 5).

## Summary

Three separate failures combined, and any one of them should have stopped this posting:

1. **The sponsorship check missed it.** The regex expects "sponsorship" followed directly by "(is) not available". Pfizer puts "(such as TN, O-1, H-1B, etc.)" between them, so it doesn't match. "Authorized to be employed in the U.S. by any employer" has no pattern at all.
2. **The years check took the easiest degree path.** `min_years_required` returns the **lowest** number in the posting: 1 (the PhD path), not 5 (your Master's path). That passes the 2 year limit and also earns +1 in the keyword score.
3. **The AI scorer never ran.** In 15 of the 17 scheduled runs that had matches, Groq scoring crashed with `invalid literal for int() with base 10: 'JOB 1'` and the whole batch fell back to keyword scores. The keyword score ignores eligibility entirely, so the posting reached 10/10. Even when the AI does run, it only sees a 1,400 character excerpt, and that excerpt doesn't include the sponsorship line.

Also found:
* There is no start date filter anywhere. "January 2027" only appears in the AI prompt, which isn't running.
* GitHub has run the schedule about every 4 hours, not every 15 minutes: 19 scheduled runs in 5 days.

## 1. Pipeline overview

Everything runs on **GitHub Actions** (`.github/workflows/radar.yml`), never on your laptop unless you run it by hand.

* **Schedule.** The crons on lines 9 to 12 aim for every 15 minutes from 8am to 11:30pm ET on weekdays, and every 2 hours on weekends.
* **Memory.** `state/radar.json` holds what the radar has already seen. It's restored from and saved to the Actions cache around each run (lines 57 and 77).
* **What actually happens.** In practice GitHub has started 19 scheduled runs since Oct 9, several hours apart and often hours late (failure mode 7.10).

One `run` (`radar/pipeline.py:151`, `run()`):

| Step | What happens | Code |
|---|---|---|
| 1. Simplify feed | Downloads `SimplifyJobs/New-Grad-Positions` listings.json. It adds those jobs, carrying Simplify's own `sponsorship` tag, and records every Greenhouse, Lever, Ashby and Workday board it links to. | `pipeline.py:162`, `simplify.py:24-87` |
| 2. Company boards | Polls the 210 boards in `companies.yaml` plus up to 2,000 discovered boards, about a third of them per run, through each ATS's public JSON API. Pfizer comes from here, via the Workday API. | `pipeline.py:181`, `radar/sources/*.py` |
| 3. New since last run | Keeps only job keys not already in memory, then marks everything seen. | `pipeline.py:211-218` |
| 4a. Free filters | `Filters.basic`: title include and exclude list, job type field, skip list, age of 21 days or less, US location, sponsorship check on the title and the Simplify tag. Then one entry per company and title, and nothing pinged for the same role in the last 14 days. | `pipeline.py:67-75, 222` |
| 4b. Details | Fetches each remaining job's full description from its ATS. For Workday this also fills `timeType` and the country. | `pipeline.py:225`, `sources/workday.py:62` |
| 4c. Strict filters | `Filters.final`: location and job type again, sponsorship and citizenship on the full text, then years asked must be 2 or less. | `pipeline.py:77-90, 226` |
| 5. Scoring | Keyword score for every kept job, sorted best first. The top `max_ai_jobs_per_run` (6) are sent to Groq `openai/gpt-oss-20b` in one batch; the rest keep their keyword score. | `pipeline.py:230-236`, `scoring.py:98-236` |
| 6. Email | Score 6 or higher (`min_score_to_ping`) gets one Gmail email each, up to 10 per run and 30 per day. Extras go out in one summary email. | `pipeline.py:238-262`, `notify.py:104` |
| 7. Google Sheet | Each emailed or overflow job becomes one row through the Apps Script web app (`SHEET_WEBHOOK_URL`). | `pipeline.py:265`, `tracker.py:9` |
| 8. Resumes | Queued only if the AI plan can fit the resume prompt. On Groq free it can't, so emails carry a paste block instead. | `pipeline.py:244-256`, `prep.py` |

**Important:** all filtering happens in steps 4a to 4c, **before** scoring. Scoring never removes a job; it only decides whether the score clears 6.

## 2. Years of experience

**It's regex only; Groq isn't involved.** The filter and the keyword score use the same function, `radar/scoring.py:58-77`:

```python
YEARS = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:\+|plus|or more)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:\+|plus|or more)?\s*(?:years?|yrs?)\b",
    re.I,
)

def min_years_required(text: str) -> int | None:
    """Lowest "N years of experience" in a posting, so "3+ years, or 1+ with a Master's" counts as 1."""
    text = _WORDS.sub(lambda m: str(_WORD_NUMS[m.group(1).lower()]), text or "")   # "five (5)" -> "5"
    found = []
    for m in YEARS.finditer(text):
        window = text[max(0, m.start() - 40): m.end() + 60].lower()
        if "experience" in window or "exp." in window:
            years = int(m.group(1))
            if years <= 15:
                found.append(years)
    return min(found) if found else None
```

`Filters.final` (`pipeline.py:84-87`) drops the job if that number is above `entry_level.max_years_required` (2).

**Why it picked 1 for Pfizer.** All three degree paths match, and the function returns the smallest:

| Match | Number | Counted | Why |
|---|---|---|---|
| `1+ years` | 1 | yes | "experience" follows right after (PhD path) |
| `5+ years` | 5 | yes | Master's path |
| `6 + years` | 6 | yes | Bachelor's path |

`min([1, 5, 6]) = 1`, and 1 is within the limit of 2, so the job passes. Taking the minimum is deliberate: the docstring calls it "lowest", and the backlog patch on Oct 8 kept it. The function has no idea which degree path applies to you. With your M.S. the right number is 5, and the job would have been dropped.

## 3. Fit score

**This email used the keyword score.** Every email shows "(keyword estimate)" when `score.by == "keywords"` (`notify.py:97`). The keyword scorer, `scoring.py:98-130`:

```python
s = 5
if AI_TITLE.search(title): s += 2          # "ai", "ml", "data scientist", ...
elif SWE_TITLE.search(title): s += 1
if EARLY.search(title) or EARLY.search(low[:4000]): s += 1   # "new grad", "entry level", "engineer i", ...
jd_skills = {k for k in SKILLS if k in low}  # 62 fixed substrings, scoring.py:33-42
s += min(2, len(jd_skills) // 5)
years = min_years_required(job.description or "")
if years is not None:
    if years >= 5: s -= 4
    elif years >= 3: s -= 2
    elif years <= 2: s += 1
if PHD_REQUIRED.search(low): s -= 3          # only "PhD required/needed" or "must have a PhD"
s = max(1, min(10, s))
why = f"{len(jd_skills)} of your skills show up" + (f", asks for {years}+ years" if years is not None else "")
```

"17 of your skills show up" is the number of `SKILLS` substrings found anywhere in the title and description. Plain substring matching means "react" also matches "reaction" and "agent" matches "agentic".

Pfizer, step by step:

| Rule | Points |
|---|---|
| Base | 5 |
| Title has "AI" | +2 |
| Early career wording | +0 (none found) |
| 17 skills found, 17 // 5 = 3, capped at 2 | +2 |
| Years = 1, which is 2 or less | +1 |
| "PhD required" | +0 (it says "PhD with 1+ years", which doesn't match) |
| **Total** | **10** |

The skills found: agent, agentic, aws, azure, ci/cd, etl, evaluation, langchain, llamaindex, postgres, prompt, python, pytorch, rag, react, retrieval, typescript.

**Does experience or eligibility affect the score?**
* **Keyword score:** experience does, through the years adjustment above and a PhD penalty that rarely triggers. **Eligibility doesn't at all:** nothing in the keyword score reads sponsorship, citizenship or start date.
* **AI score:** both do, but only through the prompt. The dealbreakers in `config.yaml:80-84` cap the score at 3 for no sponsorship, citizenship, more than 2 years, or a PhD requirement. The system prompt (`scoring.py:136-150`) says:

  > He wants an entry level (new grad, 0 to 2 years) full time role in the US starting January 2027. Work authorization: F-1 OPT ... he will need H-1B sponsorship after that. ... Dealbreakers. If one clearly applies, the score is at most 3 ...

  For Pfizer the model would get this block (`scoring.py:185-187`; the full request is about 2,950 tokens):

  ```
  JOB 1
  Company: Pfizer
  Title: Translational AI Engineer
  Location: United States - Massachusetts - Cambridge
  Description excerpt: ROLE SUMMARY: The technical engineering counterpart to AIDE's applied workflow roles, ... … QUALIFICATIONS: • PhD with 1+ years of experience OR Master's degree with 5+ years ... • Strong Python engineer fluent in modern AI/ML tools, ... frameworks such as
  ```

  The excerpt is 1,402 of 11,535 characters (`focus_jd`, `scoring.py:155-163`: 35% from the start plus 65% from the first "qualifications" style heading). It includes the degree and years line but **not** the sponsorship or authorization lines near the end of the posting. So the AI could have marked it "too senior" from the years, but couldn't see the sponsorship dealbreaker.

**Why the AI didn't score it.** Production logs show this line in 15 of the 17 scheduled runs that had matches, including every run on Oct 13:

```
AI scoring failed for a batch, using keywords: invalid literal for int() with base 10: 'JOB 1'
```

The prompt labels each job "JOB 1", "JOB 2", and so on, and the model copies that into its reply as `"job": "JOB 1"`. The parser does `int(it.get("job", 0) or 0)` (`scoring.py:209`), which raises. The generic `except Exception` (`scoring.py:234-235`) then sends the whole batch back to keyword scores. I reproduced this offline with a fake reply containing `"job": "JOB 1"`, and it raises the same `ValueError`. Separately, only the top 6 keyword matches per run are ever offered to the AI (`max_ai_jobs_per_run: 6`); runs with 12 to 20 matches score the rest by keywords regardless.

## 4. Filters

| Your rule | Where it lives | What it actually checks | When |
|---|---|---|---|
| Entry level, 0 to 2 years | `pipeline.py:84-87` with `scoring.py:67` | Lowest "N years ... experience" in the description is 2 or less | `final`, after details, **before** scoring |
| No Level II or senior | `config.yaml:27-39` exclude list, used by `filters.py:103` | **Title only**: senior/sr, staff, principal, lead, manager and up, architect, II to VI, "engineer 2" to 6, level 2+, L4+, PhD/postdoc | `basic`, before details and scoring |
| Full time only | Title exclude (`config.yaml:38`) and `employment_ok` (`filters.py:93-100`) | Title words, plus the ATS's own job type field: Lever `commitment`, Ashby `employmentType`, Workday `timeType` (only after details). Greenhouse has no such field. The description text isn't checked. | `basic` and `final` |
| No internships | Title exclude (`config.yaml:37`) and `employment_ok` | Same as full time | `basic` and `final` |
| Start January 2027 or later | **Nowhere** | Only mentioned in the AI prompt. Workday's `startDate` field is the posting's start date (2026-10-13 here), not the job's. | Not enforced |
| Sponsorship and citizenship | `filters.py:125-170` | Section 5 | `basic` (title and Simplify tag) and `final` (full text) |
| US only, 21 days or newer | `filters.py:79`, `filters.py:118` | Location text and country, posted date | `basic` and `final` |

The AI dealbreakers in `config.yaml:80-84` repeat these rules, but they only lower a score and they only run for the top 6 jobs, so they act as a backup and not as a filter.

## 5. Sponsorship and work authorization

**Yes, there is a check:** `work_auth_blocker`, `filters.py:157-170`.
1. If the job came from Simplify, it reads Simplify's tag (`no sponsor`, `not offer sponsor`, `citizen`).
2. It runs `_CITIZEN` and `_ITAR_LIST` on the title and description.
3. It runs `_NO_SPONSOR` on the same text.

```python
_NO_SPONSOR = re.compile(r"""
  (?:will\s+not|won['’]?t|cannot|can['’]?t|unable\s+to|do(?:es)?\s+not|(?:are\s+)?not\s+able\s+to)\s+
      (?:currently\s+)?(?:provide|offer|support)?\s*(?:visa\s+|employment\s+|immigration\s+|h-?1b\s+)?sponsor
| (?:visa\s+|employment\s+|immigration\s+)?sponsorship\s+(?:is\s+)?(?:not|un)\s*available
| not\s+(?:be\s+)?eligible\s+for\s+(?:visa\s+|immigration\s+|employment\s+)?sponsorship
| \bno\s+(?:visa\s+|immigration\s+|employment\s+)?sponsorship
| without\s+(?:the\s+)?(?:need\s+for\s+)?(?:current\s+or\s+future\s+|future\s+|employer\s+|company\s+)?
      (?:visa\s+|employment\s+|immigration\s+)?sponsorship
""", re.I | re.X)
```

**Why it missed Pfizer:**
* **The sponsorship line.** "U.S. work visa sponsorship **(such as TN, O-1, H-1B, etc.)** is not available": the second alternative needs `sponsorship\s+(?:is\s+)?not`, so the parenthetical between them breaks it. None of the other alternatives fit this sentence.
* **The authorization lines.** "Candidates must be authorized to be employed in the U.S. by any employer" and "This position requires permanent work authorization in the United States" have no pattern at all. "By any employer" and "permanent work authorization" are both common ways of saying no sponsorship.
* **No Simplify tag.** The job came from the Workday board in companies.yaml, so step 1 had nothing to read. Simplify isn't listing this posting.

Common phrasings tested against the current code:

| Phrase | Result |
|---|---|
| U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available ... | missed |
| Candidates must be authorized to be employed in the U.S. by any employer. | missed |
| Requires permanent work authorization in the United States. | missed |
| Applicants must be currently authorized to work in the United States on a full-time basis. | missed |
| Sponsorship will not be provided for this position. | missed |
| Visa sponsorship is not offered for this role. | missed |
| We are not sponsoring visas at this time. | missed |
| This role is not eligible for H-1B sponsorship. | missed ("H-1B" isn't one of the allowed words before "sponsorship") |
| We are unable to sponsor visas for this role. | blocked |
| Sponsorship is not available. | blocked |
| No visa sponsorship available. | blocked |

## 6. Reproduction

I ran the real `radar` code on the live posting from my machine's scratchpad, with no `.env`, no email, no Sheet and no Groq call. The script fetches the posting the same way a run does, then calls the same functions in order:

```
== 1. Listing (what the board fetch gives)
   title='Translational AI Engineer' location='United States - Massachusetts - Cambridge' posted='Posted Today' description chars=0
== 2. Free filters before details (Filters.basic)
   title filter: True   employment_ok: True   us_location: True   recent_enough: True   work_auth_blocker(title only): None
   basic() -> True
== 3. Details (workday.describe)
   description chars=11580 commitment='Full time' country='United States of America'
== 4. Strict checks (Filters.final)
   work_auth_blocker: None
   line: 'Candidates must be authorized to be employed in the U.S. by any employer.'
     _NO_SPONSOR match: False   _CITIZEN match: False
   line: 'U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available for this role now or in the future.'
     _NO_SPONSOR match: False   _CITIZEN match: False
   min_years_required: 1  (max allowed 2)
     match '1+ years'  -> 1  counted=True
     match '5+ years'  -> 5  counted=True
     match '6 + years' -> 6  counted=True
   final() -> True   dropped={'no sponsorship or citizenship': 0, 'asks for too many years': 0}
== 5. Keyword score (KeywordScorer.score)
   base 5; AI title ('AI') +2; 17 skills -> +2; years 1 -> +1; PHD_REQUIRED match: None; raw 10 -> clamped 10
   Score: 10/10 by=keywords why='17 of your skills show up, asks for 1+ years' red_flags=[] lead_with=['EXP-8', 'EXP-1', 'PROJ-12']
== 6. What the Groq scorer would be sent (built offline, not sent)
   tokens for system + this one job ~ 2950; description excerpt 1402 of 11535 chars
   excerpt contains the PhD/Master's/Bachelor's years line: True
   excerpt contains the sponsorship line: False
   excerpt contains the "authorized to be employed" line: False
```

This matches the email exactly: 10/10, keyword estimate, "17 of your skills show up, asks for 1+ years".

## 7. Other likely failure modes

Each item was tested against the current code unless noted.

1. **Degree path "OR" lists.** The lowest number always wins, regardless of your degree. "PhD 1+ OR Master's 5+ OR Bachelor's 6+" gives 1 (Pfizer). "Bachelor's 4+ or Master's 2+" gives 2, which is right for you by luck. Many pharma, biotech and research postings use this format.
2. **Preferred counted as required.** The scan covers the whole description, preferred sections included. "Minimum Qualifications: 5+ years ... Preferred: 1+ year of experience with Kubernetes" gives **1**, so the job passes. "Required: 4 years ... (2+ years preferred)" gives **2** and also passes.
3. **"Experience" too far from the number.** The window is 40 characters before and 60 after. Amazon's standard "3+ years of non-internship professional software development experience" gives **None**: "experience" is 62 characters away, so the years filter is skipped entirely. "6+ years building backend systems" also gives None. When years is None, the filter doesn't apply and the score gets neither the bonus nor the penalty.
4. **Unrelated numbers.** "Founded 2 years ago by experienced engineers. You have 6+ years building backend systems." gives **2**, because "experienced" is inside the window and the real requirement has no "experience" near it.
5. **Years buried in "Basic Qualifications".** Section headings are ignored completely: no requirement section is preferred and no "preferred" section is discounted. Workday, Greenhouse, Lever and Ashby return the full text. Custom career sites read by the generic reader are cut at 12,000 characters and are often JavaScript shells with no text, giving None.
6. **Sponsorship wording.** 8 of the 11 common phrasings above get through. Any posting from a companies.yaml board relies on these regexes alone, since only Simplify jobs carry a sponsorship tag.
7. **The AI sees too little.** The 1,400 character excerpt almost never includes the sponsorship and EEO block at the end of a posting. So even a working AI scorer would usually miss sponsorship dealbreakers.
8. **AI scoring is effectively off.** It crashes on "JOB 1" (section 3), and only the top 6 jobs per run are offered to it. The only AI scores so far can be from runs 5 and 16, at most 6 jobs each, since those are the only runs with matches and no crash. Every other email has been a keyword estimate, and the keyword score never looks at eligibility.
9. **PhD detection is narrow.** `PHD_REQUIRED` only matches "PhD required/needed" and "must have a PhD". "PhD in Computer Science or related field" in Basic Qualifications isn't caught. The title exclude only covers PhD in the title.
10. **The schedule runs hours apart.** About 63 runs a day are expected on weekdays, but GitHub started 19 scheduled runs from Oct 9 to Oct 13, and several fired at times outside every cron window, such as 07:18 and 10:26 UTC (3:18am and 6:26am ET). That means GitHub delayed them by hours. New postings reach you hours late, not within 15 minutes.
11. **Job type depends on ATS fields.** Greenhouse jobs have no job type field, so contract or fixed term roles only get caught if the title says so.
12. **Seniority is title only.** "Software Engineer" with "5+ years leading teams" relies entirely on the years extraction, so failure modes 1 to 4 matter for seniority too.
13. **No start date check.** A role starting in November 2026 or "immediately" passes. Only the AI prompt mentions January 2027.

## Evidence

All of this is in the scratchpad, outside the repo.

| What | Detail |
|---|---|
| Posting | Workday `PfizerCareers` req 4962398, fetched 2026-10-13 |
| Simplify check | Simplify feed has 19 Pfizer listings, none with id 4962398 |
| GitHub runs read | 26 runs (19 scheduled, 7 manual) since 2026-10-09 |
| `JOB 1` error | In runs 3, 4, 6 to 9, 12 to 15 and 17 to 21. Absent in runs 5 and 16, and in runs 10 and 11, which had no matches. |
