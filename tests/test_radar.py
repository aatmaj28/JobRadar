"""Offline tests: no network, no secrets. Run: python -m unittest discover -s tests -v"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import pipeline, simplify  # noqa: E402
from radar.ats_urls import parse_ats_url  # noqa: E402
from radar.config import load_config  # noqa: E402
from radar.filters import TitleFilter, us_location, work_auth_blocker  # noqa: E402
from radar.llm import DailyLimit, GroqLLM, TooLarge  # noqa: E402
from radar.models import Board, Job  # noqa: E402
from radar.notify import Notifier, email_job  # noqa: E402
from radar.prep import Preparer, compile_tex, extract_latex, make_safe  # noqa: E402
from radar.profile import Profile  # noqa: E402
from radar.referral import drafts  # noqa: E402
from radar.scoring import (KeywordScorer, LLMScorer, focus_jd, job_number, min_years_required, phd_in_basic,  # noqa: E402
                           start_date_flag, year_month)
from radar.sources import ashby, greenhouse, lever, workday  # noqa: E402
from radar.state import State  # noqa: E402

CFG = load_config(ROOT / "config.yaml")
PROFILE = Profile.load(ROOT / "profile")


class Resp:
    def __init__(self, status=200, data=None, text=None, headers=None):
        self.status_code, self._data = status, data
        self.text = text if text is not None else (json.dumps(data) if data is not None else "")
        self.headers = headers or {"Content-Type": "application/json"}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _find(self, method, url):
        for (m, prefix), resp in self.routes.items():
            if m == method and url.startswith(prefix):
                return resp(url) if callable(resp) else resp
        return Resp(404, {"error": "not found"})

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._find("GET", url)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        return self._find("POST", url)


class FakeLLM:
    max_request_tokens = 200000

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def chat(self, model, system_parts, messages, max_tokens=1500, json_mode=False, reasoning=None):
        self.calls.append(messages[-1]["content"])
        reply = self.replies.pop(0) if isinstance(self.replies, list) else self.replies
        return reply(messages) if callable(reply) else reply


def job(title="Software Engineer", desc="", location="San Francisco, CA", extra=None):
    return Job("greenhouse", "acme", str(abs(hash(title + desc + location))), "Acme", title, location=location,
               url="https://boards.greenhouse.io/acme/jobs/1", description=desc, extra=extra or {})


class TitleAndLocationTests(unittest.TestCase):
    def setUp(self):
        self.f = TitleFilter(CFG["titles"]["include"], CFG["titles"]["exclude"])

    def test_keeps_target_titles(self):
        for t in ["Software Engineer, New Grad 2026", "Machine Learning Engineer", "AI Engineer", "Applied AI Engineer",
                  "Forward Deployed Engineer", "Member of Technical Staff", "Data Scientist", "AI Solutions Engineer",
                  "Full Stack Engineer", "Backend Engineer", "Research Engineer", "LLM Engineer", "Software Engineer 2027 Start",
                  "Software Development Engineer I", "Computer Vision Engineer"]:
            self.assertTrue(self.f(t), t)

    def test_drops_everything_else(self):
        for t in ["Senior Software Engineer", "Staff ML Engineer", "Software Engineer II", "Machine Learning Engineer 2",
                  "Software Engineer Intern", "Data Engineer", "Quant Developer", "Engineering Manager",
                  "Software Engineer (TS/SCI)", "Sr. AI Engineer", "Lead Data Scientist", "AI Product Manager",
                  "Research Scientist, PhD", "Software Engineer, Co-op", "Contract ML Engineer"]:
            self.assertFalse(self.f(t), t)

    def test_us_location(self):
        cases = {"San Francisco, CA": True, "New York, NY; London, UK": True, "Remote - US": True, "US, CA, Santa Clara": True,
                 "California - San Francisco": True, "Seattle": True, "Toronto, ON, Canada": False, "London, UK": False,
                 "Bengaluru, India": False, "Remote - Canada": False, "Remote": True, "Vancouver, BC": False,
                 "Portland, OR": True, "Remote (EMEA)": False}
        for loc, want in cases.items():
            self.assertEqual(us_location(job(location=loc)), want, loc)
        self.assertIsNone(us_location(job(location="2 Locations")))
        self.assertTrue(us_location(job(location="2 Locations", extra={"country": "United States of America"})))


class WorkAuthTests(unittest.TestCase):
    def test_skips_no_sponsorship_and_citizenship(self):
        for text in ["We are unable to sponsor visas for this role.", "Visa sponsorship is not available.",
                     "Must be authorized to work in the US without the need for employer sponsorship now or in the future.",
                     "Must be a U.S. citizen.", "Requires an active Secret clearance.", "Green card holders only.",
                     "ITAR REQUIREMENTS: To conform to U.S. Government export regulations, applicant must be a (i) U.S. citizen "
                     "or national, (ii) U.S. lawful, permanent resident (aka green card holder), (iii) Refugee under "
                     "8 U.S.C. 1157, or (iv) Asylee under 8 U.S.C. 1158.",
                     "This position is subject to ITAR and requires the candidate to be a U.S. person."]:
            self.assertIsNotNone(work_auth_blocker(job(desc=text)), text)

    def test_keeps_friendly_wording(self):
        for text in ["We sponsor H-1B visas for qualified candidates.",
                     "Equal opportunity employer without regard to race, citizenship status, or national origin.",
                     "This role may require an export license under U.S. export control laws.",
                     "Experience with ITAR compliance tooling is a plus.",
                     "We do not discriminate on the basis of citizenship status. This includes U.S. citizens, nationals, "
                     "lawful permanent residents, refugees, and asylees.",
                     "Applicants are protected from citizenship status discrimination, whether they are a U.S. citizen "
                     "or national, a lawful permanent resident, or work authorized.",
                     "Familiarity with ITAR and EAR export rules. We hire without regard to race, religion, citizenship "
                     "status, or national origin.", ""]:
            self.assertIsNone(work_auth_blocker(job(desc=text)), text)

    def test_blocks_every_no_sponsorship_phrasing(self):
        # INVESTIGATION.md section 5 table (all but the vague "currently authorized" line), then the new ones
        for text in ["U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available for this role now or in the future.",
                     "Candidates must be authorized to be employed in the U.S. by any employer.",
                     "Requires permanent work authorization in the United States.",
                     "Sponsorship will not be provided for this position.",
                     "Visa sponsorship is not offered for this role.",
                     "We are not sponsoring visas at this time.",
                     "This role is not eligible for H-1B sponsorship.",
                     "We are unable to sponsor visas for this role.",
                     "Sponsorship is not available.",
                     "No visa sponsorship available.",
                     "Must be authorized to work for any employer in the United States.",
                     "Must be able to work in the U.S. without sponsorship now or in the future.",
                     "Applicants who require sponsorship now or in the future will not be considered.",
                     "Sponsorship for this role is not available now or in the future.",
                     "We cannot offer visa sponsorship, now or in the future.",
                     "U.S. citizens only.",
                     "Green card holders only."]:
            self.assertIsNotNone(work_auth_blocker(job(desc=text)), text)

    def test_vague_authorization_lines_pass(self):
        for text in ["Must be currently authorized to work in the United States.",
                     "Authorized to work on a full time basis.",
                     "Applicants must be currently authorized to work in the United States on a full-time basis.",
                     "Will you now or in the future require visa sponsorship?",
                     "We build products today and plan to keep doing so now or in the future."]:
            self.assertIsNone(work_auth_blocker(job(desc=text)), text)

    def test_pfizer_posting_is_dropped_twice(self):
        desc = ("BASIC QUALIFICATIONS:\n• PhD with 1+ years of experience OR Master’s degree with 5+ years of software or ML "
                "engineering experience - OR Bachelors degree and 6 + years of experience and/or equivalent demonstrated "
                "experience shipping AI/ML systems for scientific applications.\n"
                "Candidates must be authorized to be employed in the U.S. by any employer.\n"
                "U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available for this role now or in the future.")
        self.assertIsNotNone(work_auth_blocker(job("Translational AI Engineer", desc)))
        self.assertEqual(min_years_required(desc), 5)
        filt = pipeline.Filters(CFG)
        self.assertFalse(filt.final(job("Translational AI Engineer", desc, location="Cambridge, MA")))

    def test_simplify_tags(self):
        self.assertTrue(work_auth_blocker(job(extra={"sponsorship": "No sponsorship"})))
        self.assertTrue(work_auth_blocker(job(extra={"sponsorship": "US Citizenship Required"})))

    def test_years(self):
        self.assertEqual(min_years_required("0-2 years of experience with Python"), 0)
        self.assertEqual(min_years_required("3+ years of professional experience"), 3)
        self.assertIsNone(min_years_required("We have been around for 10 years."))
        self.assertEqual(min_years_required("Five (5) years of industry experience"), 5)
        self.assertEqual(min_years_required("3 or more years of experience with Python"), 3)
        self.assertEqual(min_years_required("Experience: 4+ yrs building APIs"), 4)
        self.assertEqual(min_years_required("BS with 3+ years, or MS with 1+ years of experience"), 1)
        self.assertIsNone(min_years_required("Start in 2026. Years of experience are a plus."))


class YearsTests(unittest.TestCase):
    """INVESTIGATION.md section 7, items 1 to 4."""

    def check(self, cases):
        for text, want in cases:
            self.assertEqual(min_years_required(text), want, text)

    def test_degree_paths_use_the_masters_path(self):
        self.check([
            ("PhD with 1+ years of experience OR Master’s degree with 5+ years of software or ML engineering experience "
             "- OR Bachelors degree and 6 + years of experience", 5),
            ("Bachelor's with 4+ years of experience, or Master's with 2+ years of experience", 2),
            ("PhD with 1+ years of experience or Bachelor's degree with 4+ years of experience", 4),  # no Master's path
            ("Master's or Bachelor's degree with 5+ years of experience.", 5),  # the years apply to both
            ("Bachelor's degree and 4 years of experience, or a Master's degree.", 0),  # the Master's path needs none
            ("BS/MS in Computer Science or a related field and 2+ years of experience.", 2),
            ("Bachelor’s degree in Computer Science or equivalent practical experience, and 3 years of experience.", 3),
        ])

    def test_preferred_sections_dont_count(self):
        self.check([
            ("Minimum Qualifications: 5+ years of software experience. Preferred: 1+ year of experience with Kubernetes.", 5),
            ("Required: 4 years of experience. Nice to have: experience with Go (2+ years preferred).", 4),
            ("BASIC QUALIFICATIONS:\n• 3+ years of experience in Python\nPREFERRED QUALIFICATIONS:\n• 1+ year of experience with Rust", 3),
            ("Requirements\n• 4+ years of experience\nNice to have\n• 1+ year of experience with Go", 4),
            ("Bonus points:\n• 1 year of experience with CUDA\nWhat you need:\n• 3+ years of experience with Python", 3),
            ("Plus: 1+ year of experience with Rust. Required: 3+ years of experience.", 3),
            ("3+ years of experience with Kubernetes is a plus.", None),
        ])

    def test_experience_anywhere_in_the_sentence(self):
        self.check([
            ("Basic Qualifications: Bachelor's degree. 3+ years of non-internship professional software development experience.", 3),
            ("You have 6+ years building backend systems.", 6),
            ("5 years professional experience", 5),
            ("2+ years’ experience in ML", 2),
            ("Minimum experience: 3 years.", 3),
        ])

    def test_real_postings(self):
        # Lines from live postings that an earlier draft of this parser got wrong
        self.check([
            ("Master’s degree with 15 or more years of prior relevant experience or Doctorate with 13 or more years of relevant experience.", 15),
            ("Bachelor's degree AND 5 or more years of experience in Epidemiology, OR MPH/MS AND 3 or more years of experience.", 3),
            ("Have 1 - 3 years of experience and a minimum of a BS and/or MS in Computer Science", 1),
            ("6+ years in data science, with significant time on growth or experimentation", 6),
            ("In lieu of Associates degree(s) listed above, High School/GED AND 2 year(s) related work experience", 2),
            ("Bachelor’s degree and a minimum of 10 years’ business, analytical, technical, digital product, transformation, "
             "or product ownership experience", 10),
            ("5+ years of experience building web applications in a production environment (startup or fast-paced product team preferred)", 5),
            ("8+ years in software engineering or technical consulting, including 5+ years building enterprise cloud or AI solutions", 8),
            ("Experience — 3–5 years total;", 3),
            ("Proven experience (7+ years preferred) designing enterprise web applications.", None),
            ("A minimum of 2 years data science or related work experience is required with Bachelor's degree or Master's degree "
             "without experience.", 0),
            ("Bachelor’s degree, OR 3+ years of relevant work experience.", 0),
            ("You possess a bachelor's degree in Computer Science, Electrical Engineering, or a closely related field or 3+ "
             "years of equivalent experience.", 0),
            ("Doctorate degree in sensor analytics, atmospheric science, or applied data science AND 5+ years of experience OR "
             "Master's degree in sensor analytics, atmospheric science, or applied data science AND 7+ years of experience OR "
             "Bachelor's degree in applied data science AND 10+ years of experience", 7),
            ("Bachelor’s, master’s or PhD in computer science, physics or related discipline with at least 5 years of industry "
             "experience.", 5),
            ("Tuition Reimbursement After 1 Year of Service\n6–9 years of software engineering experience", 6),
        ])

    def test_only_requirement_phrases_count(self):
        self.check([
            ("Founded 2 years ago by experienced engineers. You have 6+ years building backend systems.", 6),
            ("Founded 2 years ago by experienced engineers.", None),
            ("We have been around for 10 years and our experience shows.", None),
        ])


class UrlAndSourceTests(unittest.TestCase):
    def test_parse_urls(self):
        r = parse_ats_url("https://job-boards.greenhouse.io/anthropic/jobs/4567?gh_src=x")
        self.assertEqual((r.source, r.slug, r.job_id), ("greenhouse", "anthropic", "4567"))
        r = parse_ats_url("https://boards.greenhouse.io/embed/job_app?for=stripe&token=99")
        self.assertEqual((r.slug, r.job_id), ("stripe", "99"))
        r = parse_ats_url("https://jobs.lever.co/plaid/ab-12/apply")
        self.assertEqual((r.source, r.slug, r.job_id), ("lever", "plaid", "ab-12"))
        r = parse_ats_url("https://capitalone.wd1.myworkdayjobs.com/en-US/Capital_One/job/McLean-VA/SWE_R123?utm=x")
        self.assertEqual((r.slug, r.job_id, r.board_id), ("capitalone.wd1.myworkdayjobs.com/capitalone/Capital_One", "SWE_R123", "capitalone/capital_one"))
        self.assertIsNone(parse_ats_url("https://careers.google.com/jobs/123"))

    def test_greenhouse_lever_ashby(self):
        http = FakeHttp({
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/7"): Resp(200, {"content": "&lt;p&gt;Build LLM agents&lt;/p&gt;"}),
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): Resp(200, {"jobs": [{"id": 7, "title": "AI Engineer", "absolute_url": "u", "location": {"name": "Boston, MA"}, "updated_at": "2026-10-07T10:00:00Z"}]}),
            ("GET", "https://api.lever.co/v0/postings/plaid"): Resp(200, [{"id": "x1", "text": "Software Engineer", "hostedUrl": "h", "createdAt": 1791400000000, "categories": {"location": "New York, NY", "commitment": "Full-time"}, "country": "US", "descriptionPlain": "Python"}]),
            ("GET", "https://api.ashbyhq.com/posting-api/job-board/openai"): Resp(200, {"jobs": [{"id": "a1", "title": "Research Engineer", "location": "San Francisco", "jobUrl": "j", "publishedAt": "2026-10-07T00:00:00Z", "descriptionPlain": "RL", "employmentType": "FullTime", "isListed": True}]}),
        })
        g = greenhouse.fetch(http, Board("greenhouse", "acme", "Acme"))
        greenhouse.describe(http, g[0])
        self.assertEqual((g[0].key, g[0].description), ("greenhouse:acme:7", "Build LLM agents"))
        self.assertEqual(lever.fetch(http, Board("lever", "plaid", "Plaid"))[0].extra["country"], "US")
        self.assertEqual(ashby.fetch(http, Board("ashby", "openai", "OpenAI"))[0].title, "Research Engineer")

    def test_workday(self):
        base = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Ext"
        http = FakeHttp({
            ("POST", base + "/jobs"): Resp(200, {"total": 1, "jobPostings": [{"title": "AI Engineer", "externalPath": "/job/US-CA-Santa-Clara/AI-Engineer_JR1", "locationsText": "2 Locations", "postedOn": "Posted Today"}]}),
            ("GET", base + "/job/"): Resp(200, {"jobPostingInfo": {"jobDescription": "<p>Train models</p>", "location": "US, CA, Santa Clara", "additionalLocations": ["US, WA, Seattle"], "country": {"descriptor": "United States of America"}, "timeType": "Full time"}}),
        })
        jobs = workday.fetch(http, Board("workday", "nvidia.wd5.myworkdayjobs.com/nvidia/Ext", "NVIDIA"))
        workday.describe(http, jobs[0])
        self.assertEqual(jobs[0].key, "workday:nvidia/ext:ai-engineer_jr1")
        self.assertIn("Seattle", jobs[0].location)
        self.assertTrue(us_location(jobs[0]))

    def test_simplify_feed(self):
        now = time.time()
        listings = [
            {"company_name": "Acme", "title": "AI Engineer", "locations": ["Boston, MA"], "url": "https://boards.greenhouse.io/acme/jobs/7", "date_posted": now, "active": True, "is_visible": True, "id": "s1"},
            {"company_name": "Google", "title": "Software Engineer", "locations": ["NYC"], "url": "https://careers.google.com/x", "date_posted": now, "active": True, "id": "s2", "sponsorship": "No sponsorship"},
            {"company_name": "Old", "title": "ML Engineer", "locations": ["NYC"], "url": "https://jobs.lever.co/old/1", "date_posted": now - 400 * 86400, "active": True, "id": "s3"},
        ]
        jobs = simplify.jobs_from_listings(listings)
        self.assertEqual([j.key for j in jobs], ["greenhouse:acme:7", "simplify:newgrad:s2"])
        self.assertEqual(jobs[1].extra["sponsorship"], "No sponsorship")
        self.assertEqual(set(simplify.discover_boards(listings)), {"greenhouse:acme"})


class CopyAndScoringTests(unittest.TestCase):
    def test_referral_notes(self):
        j = job(title="Machine Learning Engineer - New Grad (Remote)")
        for note in drafts(j, "I built a hybrid RAG system at LiquidPixels with a layered eval harness.", CFG["referrals"]["messages"], CFG["referrals"]["default_hook"]):
            self.assertLessEqual(len(note), 300)
            self.assertNotIn("-", note)
            self.assertIn("Aatmaj Amol Salunke", note)

    def test_keyword_scorer(self):
        ks = KeywordScorer(PROFILE)
        good = ks.score(job("Machine Learning Engineer, New Grad", "Python PyTorch LLM RAG agents FastAPI Docker AWS vector 0-2 years of experience"))
        bad = ks.score(job("Software Engineer", "Java. 6+ years of experience required."))
        self.assertGreater(good.score, bad.score)
        self.assertTrue(all(x in PROFILE.entries for x in good.lead_with))

    def test_llm_scorer_batches(self):
        reply = json.dumps({"results": [{"job": 1, "score": 9, "level_fit": "good", "why": "Agents and RAG", "lead_with": ["EXP-8", "EXP-99", "PROJ-12"], "hook": "I built a dual-path LLM system.", "red_flags": []},
                                        {"job": 2, "score": 3, "level_fit": "too senior", "why": "Senior scope", "lead_with": [], "hook": "", "red_flags": ["5 years"]}]})
        llm = FakeLLM([reply])
        sc = LLMScorer(llm, "m", PROFILE, ["requires a PhD"], KeywordScorer(PROFILE))
        out = sc.score_many([job("AI Engineer"), job("ML Engineer"), ])
        self.assertEqual([s.score for s in out], [9, 3])
        self.assertEqual(out[0].lead_with, ["EXP-8", "PROJ-12"])
        self.assertNotIn("-", out[0].hook)
        self.assertEqual(len(llm.calls), 1)

    def test_llm_reads_any_job_label(self):
        self.assertEqual([job_number(x) for x in [1, 2.0, "3", "JOB 4", "Job 5", "job #6", "x", None, True, 1.5]],
                         [1, 2, 3, 4, 5, 6, None, None, None, None])
        reply = json.dumps({"results": [{"job": label, "score": 8, "why": "fits"} for label in ["JOB 1", "Job 2", "3", 4]]})
        sc = LLMScorer(FakeLLM([reply]), "m", PROFILE, [], KeywordScorer(PROFILE))
        out = sc.score_many([job(f"AI Engineer {n}") for n in "ABCD"])
        self.assertEqual([(s.score, s.by) for s in out], [(8, "ai")] * 4)

    def test_one_bad_result_only_costs_that_job(self):
        reply = json.dumps({"results": [{"job": "JOB 1", "score": 9, "why": "fits"}, {"job": "JOB 2", "score": "high"},
                                        {"job": "JOB 3", "why": "no score"}]})
        logs = []
        sc = LLMScorer(FakeLLM([reply]), "m", PROFILE, [], KeywordScorer(PROFILE), log=logs.append)
        out = sc.score_many([job("AI Engineer"), job("ML Engineer"), job("Data Scientist"), job("LLM Engineer")])
        self.assertEqual([s.by for s in out], ["ai", "keywords", "keywords", "keywords"])
        self.assertEqual(out[0].score, 9)
        self.assertTrue(any("job 2 (ML Engineer at Acme)" in l and "'high'" in l for l in logs), logs)
        self.assertTrue(any("job 3 (Data Scientist at Acme)" in l and "no score" in l for l in logs), logs)
        self.assertTrue(any("left out job 4 (LLM Engineer at Acme)" in l for l in logs), logs)

    def test_ai_excerpt_keeps_the_key_lines(self):
        filler = " ".join(f"You will own part {i} of our platform and work closely with partner teams." for i in range(40))
        desc = (f"ROLE SUMMARY: {filler}\nQUALIFICATIONS:\n• Strong Python and PyTorch skills.\n• Master's degree with 5+ years of "
                f"experience.\n{filler}\nWe are an equal opportunity employer without regard to citizenship status.\n"
                "U.S. work visa sponsorship (such as TN, O-1, H-1B, etc.) is not available for this role.\n"
                "Candidates must be authorized to be employed in the U.S. by any employer.\nStart date: November 2026.")
        excerpt = focus_jd(desc, 1400)
        self.assertLessEqual(len(excerpt), 1400)
        for line in ["Master's degree with 5+ years", "sponsorship (such as TN", "by any employer", "Start date: November 2026",
                     "Strong Python and PyTorch"]:
            self.assertIn(line, excerpt)
        self.assertNotIn("equal opportunity", excerpt)

    def test_red_flags_cap_the_keyword_score(self):
        ks = KeywordScorer(PROFILE, earliest_start="2027-01")
        strong = "Python PyTorch LLM RAG agents FastAPI Docker AWS vector embedding inference evaluation. 0-2 years of experience."
        self.assertGreaterEqual(ks.score(job("AI Engineer, New Grad", strong)).score, 8)
        for extra, flag in [("Basic Qualifications:\n• PhD in Computer Science.", "PhD required"),
                            ("Candidates must be able to start immediately.", "asks to start immediately"),
                            ("Start date: November 2026.", "start date November 2026 is before January 2027")]:
            s = ks.score(job("AI Engineer, New Grad", f"{strong}\n{extra}"))
            self.assertLessEqual(s.score, 3, extra)
            self.assertIn(flag, s.red_flags)
        self.assertEqual(ks.score(job("AI Engineer, New Grad", f"{strong}\nStart date: January 2027.")).red_flags, [])

    def test_red_flags_lower_ai_scores_and_show_in_the_email(self):
        reply = json.dumps({"results": [{"job": 1, "score": 9, "why": "fits", "red_flags": []}]})
        sc = LLMScorer(FakeLLM([reply]), "m", PROFILE, [], KeywordScorer(PROFILE, earliest_start="2027-01"))
        j = job("AI Engineer", "Python agents. Minimum Qualifications: PhD in Machine Learning. Start date: Fall 2026.")
        s = sc.score_many([j])[0]
        self.assertEqual((s.score, s.by), (6, "ai"))
        self.assertEqual(s.red_flags, ["PhD required", "start date Fall 2026 is before January 2027"])
        _subject, html_body, text = email_job(j, s, [], [], [], False, "")
        self.assertIn("PhD required; start date Fall 2026 is before January 2027", html_body)
        self.assertNotRegex(" ".join(s.red_flags), r"[-–—]")

    def test_phd_and_start_date_detection(self):
        self.assertTrue(phd_in_basic("Basic Qualifications:\n• PhD in Computer Science or a related field"))
        self.assertTrue(phd_in_basic("Minimum Qualifications: PhD in ML."))
        self.assertFalse(phd_in_basic("Basic Qualifications:\n• PhD or Master's in CS"))
        self.assertFalse(phd_in_basic("Preferred Qualifications:\n• PhD in CS"))
        self.assertFalse(phd_in_basic("PhD students are welcome to apply."))
        self.assertFalse(phd_in_basic("Minimum Qualifications:\n• Master's Level Degree and 0 years related work experience;\n"
                                      "• Doctorate (Academic) Degree and 0 years related work experience;"))
        ks = KeywordScorer(PROFILE)
        self.assertNotIn("PhD required", ks.red_flags(job("ML Engineer", "Master's or PhD Required")))
        self.assertIn("PhD required", ks.red_flags(job("ML Engineer", "PhD required.")))
        jan = year_month("2027-01")
        cases = {"Start date: November 2026.": "start date November 2026 is before January 2027",
                 "Expected start date is Fall 2026.": "start date Fall 2026 is before January 2027",
                 "The program begins on 07/13/2026.": "start date 07/13/2026 is before January 2027",
                 "Start: ASAP": "asks to start immediately", "This role starts in January 2027.": "",
                 "Start date: Summer 2027.": "", "We started in 2015.": "", "We're a start-up founded in 2015.": "",
                 "Starting in 2026 we grew fast.": "", "The anticipated start date is Fall 2026 or Spring 2027.": ""}
        for text, want in cases.items():
            self.assertEqual(start_date_flag(text, jan), want, text)

    def test_email_format(self):
        s = KeywordScorer(PROFILE).score(job("AI Engineer", "Python"))
        subject, html_body, text = email_job(job("AI Engineer", "Python"), s, [("NEU alumni", "https://x")], ["note one", "note two"], ["LiquidPixels (EXP-8)"], False, "1. Company Name: Acme")
        self.assertIn("AI Engineer", subject)
        self.assertIn("Paste into your Resume Maker project", html_body)
        self.assertIn("note one", text)


class GroqTests(unittest.TestCase):
    class Session:
        def __init__(self, responses):
            self.responses, self.bodies = responses, []

        def post(self, url, json=None, timeout=None, headers=None):
            self.bodies.append(json)
            return self.responses.pop(0)

    def ok(self, text):
        return Resp(200, {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 10}})

    def test_retry_then_success(self):
        s = self.Session([Resp(429, {"error": "tpm"}, headers={"retry-after": "0"}), self.ok("<think>x</think>OK")])
        self.assertEqual(GroqLLM("k", session=s).chat("openai/gpt-oss-20b", ["sys"], [{"role": "user", "content": "hi"}], json_mode=True), "OK")
        self.assertEqual(s.bodies[0]["reasoning_effort"], "low")

    def test_limits(self):
        llm = GroqLLM("k", session=self.Session([Resp(429, text="Rate limit reached on tokens per day (TPD)", headers={"retry-after": "500"})]))
        with self.assertRaises(DailyLimit):
            llm.chat("m", ["s"], [{"role": "user", "content": "x"}])
        with self.assertRaises(TooLarge):
            GroqLLM("k", session=self.Session([])).chat("m", ["x" * 40000], [{"role": "user", "content": "y"}])

    def test_drops_json_mode_when_rejected(self):
        s = self.Session([Resp(400, text='{"error":{"code":"json_validate_failed"}}'), self.ok('{"a": 1}')])
        GroqLLM("k", session=s).chat("m", ["s"], [{"role": "user", "content": "x"}], json_mode=True)
        self.assertNotIn("response_format", s.bodies[1])

    def test_resume_prompt_does_not_fit_free_plan(self):
        self.assertFalse(Preparer(GroqLLM("k", plan="free"), "m", PROFILE, Path(tempfile.mkdtemp()), "A").fits())
        self.assertTrue(Preparer(GroqLLM("k", plan="developer"), "m", PROFILE, Path(tempfile.mkdtemp()), "A").fits())


TEX = r"""\documentclass[letterpaper,10.5pt]{article}
\usepackage[top=0.4in,bottom=0.4in,left=0.45in,right=0.45in]{geometry}
\usepackage{lmodern}\usepackage{titlesec}\usepackage{enumitem}\usepackage{hyperref}
\pagestyle{empty}
\begin{document}
\section*{Experience} Built agents → shipped 3× faster.
\end{document}"""


@unittest.skipUnless(shutil.which("pdflatex"), "pdflatex not installed")
class LatexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        stub = self.tmp / "stub"
        stub.mkdir()
        (stub / "lmodern.sty").write_text("\\ProvidesPackage{lmodern}\n")  # stand in if the real font package is missing
        os.environ["TEXINPUTS"] = f"{stub}:"

    def test_extract_safe_compile(self):
        tex = make_safe(extract_latex("Rationale: EXP-1, EXP-8\n```latex\n" + TEX + "\n```"))
        self.assertNotIn("→", tex)
        path = self.tmp / "Aatmaj_Salunke_Resume_Acme.tex"
        path.write_text(tex)
        result = compile_tex(path)
        self.assertTrue(result.ok, result.log_tail)
        self.assertEqual(result.pages, 1)


class BacklogTests(unittest.TestCase):
    def test_backlog_applies_every_check(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "companies.yaml").write_text("greenhouse:\n  acme: Acme\n")
        descs = {"1": "Build LLM agents. 0-2 years of experience.", "2": "Requires 5+ years of professional experience.",
                 "3": "We are unable to sponsor visas.", "4": "Three (3) years of experience required."}
        http = FakeHttp({
            ("GET", "https://raw.githubusercontent.com/"): Resp(200, []),
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/"): lambda url: Resp(200, {"content": descs[url.rsplit("/", 1)[1]]}),
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): Resp(200, {"jobs": [
                {"id": i, "title": t, "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}", "location": {"name": "Boston, MA"}}
                for i, t in [(1, "AI Engineer"), (2, "Machine Learning Engineer"), (3, "Software Engineer"), (4, "Data Scientist")]]}),
        })
        notifier = Notifier(dry=True, log=lambda *a: None)
        ks = KeywordScorer(PROFILE)
        ctx = pipeline.Ctx(cfg=json.loads(json.dumps(CFG)), state=State(tmp / "s.json"), http=http, notifier=notifier,
                           profile=PROFILE, scorer=ks, keyword_scorer=ks, root=tmp, out_dir=tmp / "out", log=lambda *a: None)
        stats = pipeline.backlog(ctx)
        self.assertEqual((stats["kept"], stats["removed_for_years"], stats["removed_for_sponsorship"]), (1, 2, 1))
        rows = Path(stats["csv"]).read_text().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("AI Engineer", rows[1])
        self.assertTrue(any(k == "text" and "fully checked" in s for k, s in notifier.sent))


class PipelineTests(unittest.TestCase):
    def test_end_to_end(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "companies.yaml").write_text("greenhouse:\n  acme: Acme\nworkday:\n  NVIDIA: https://nvidia.wd5.myworkdayjobs.com/Ext\n")
        now = time.time()
        gh_jobs = [{"id": 1, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Boston, MA"}}]
        listings = [{"company_name": "Lever Co", "title": "AI Engineer", "locations": ["NYC"], "url": "https://jobs.lever.co/leverco/l1", "date_posted": now, "active": True, "id": "s1"}]
        descs = {"2": "Build LLM agents in Python. 0-2 years of experience.", "3": "We are unable to sponsor visas.", "4": "Build models."}
        http = FakeHttp({
            ("GET", "https://raw.githubusercontent.com/"): lambda url: Resp(200, listings, headers={"ETag": str(len(listings))}),
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/"): lambda url: Resp(200, {"content": descs.get(url.rsplit("/", 1)[1], "")}),
            ("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"): lambda url: Resp(200, {"jobs": gh_jobs}),
            ("POST", "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Ext/jobs"): Resp(200, {"jobPostings": []}),
            ("GET", "https://api.lever.co/v0/postings/leverco"): Resp(200, []),
        })
        notifier = Notifier(dry=True, log=lambda *a: None)
        reply = lambda msgs: json.dumps({"results": [{"job": n, "score": 8, "level_fit": "good", "why": "fits", "lead_with": ["EXP-8"], "hook": "", "red_flags": []} for n in range(1, 7)]})
        llm = FakeLLM(reply)
        cfg = json.loads(json.dumps(CFG))
        ks = KeywordScorer(PROFILE)
        ctx = pipeline.Ctx(cfg=cfg, state=State(tmp / "state.json"), http=http, notifier=notifier, profile=PROFILE,
                           scorer=LLMScorer(llm, "m", PROFILE, [], ks), keyword_scorer=ks, llm=llm, prep_enabled=True,
                           root=tmp, out_dir=tmp / "out", log=lambda *a: None)

        first = pipeline.run(ctx)  # learns what's open, sends the spreadsheet
        self.assertEqual(first["pinged"], 0)
        self.assertTrue(any(k == "text" and "live" in s for k, s in notifier.sent))
        self.assertTrue(list((tmp / "out").glob("open_roles_*.csv")))

        gh_jobs += [
            {"id": 2, "title": "Machine Learning Engineer, New Grad", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "location": {"name": "San Francisco, CA"}},
            {"id": 3, "title": "AI Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/3", "location": {"name": "New York, NY"}},
            {"id": 4, "title": "Senior ML Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/4", "location": {"name": "Boston, MA"}},
            {"id": 5, "title": "Machine Learning Engineer, New Grad", "absolute_url": "https://boards.greenhouse.io/acme/jobs/5", "location": {"name": "Seattle, WA"}},
        ]
        second = pipeline.run(ctx)
        pings = [s for k, s in notifier.sent if k == "job"]
        self.assertEqual(len(pings), 1, pings)  # sponsorship skip, senior skip, one ping per role
        self.assertIn("Machine Learning Engineer, New Grad", pings[0])
        self.assertEqual(second["queued_resumes"], 1)
        self.assertEqual(second.get("skipped: no sponsorship or citizenship"), 1)

        if shutil.which("pdflatex"):
            stub = tmp / "stub"
            stub.mkdir()
            (stub / "lmodern.sty").write_text("\\ProvidesPackage{lmodern}\n")
            os.environ["TEXINPUTS"] = f"{stub}:"
            ctx.llm = FakeLLM(["Picked EXP-8, EXP-9, EXP-1 and PROJ-12, PROJ-17\n```latex\n" + TEX + "\n```"])
            stats = pipeline.prep(ctx, Preparer(ctx.llm, "m", PROFILE, ctx.out_dir, "Aatmaj_Salunke"))
            self.assertEqual(stats["built"], 1)
            self.assertTrue(list((tmp / "out").rglob("Aatmaj_Salunke_Resume_Acme.pdf")))


        third = pipeline.run(ctx)  # nothing new, nothing sent
        self.assertEqual(third["pinged"], 0)


if __name__ == "__main__":
    unittest.main()
