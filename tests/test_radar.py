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
from radar.scoring import KeywordScorer, LLMScorer, min_years_required  # noqa: E402
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
