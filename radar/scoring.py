"""Fit scoring.

KeywordScorer is free and always available. ClaudeScorer reads the whole job
description against Aatmaj's master files and is far more accurate. It falls
back to KeywordScorer for any job where the API call fails.
"""
from __future__ import annotations

import calendar
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .models import Job
from .profile import Profile
from .text import clip, dehyphen, sentences


@dataclass
class Score:
    score: int
    why: str = ""
    level_fit: str = ""
    lead_with: list = field(default_factory=list)
    hook: str = ""
    red_flags: list = field(default_factory=list)
    by: str = "keywords"

    def to_dict(self) -> dict:
        return asdict(self)


SKILLS = [
    "python", "pytorch", "tensorflow", "llm", "large language model", "rag", "retrieval",
    "langchain", "langgraph", "llamaindex", "agent", "agentic", "multi agent", "fastapi", "flask",
    "django", "react", "typescript", "next.js", "docker", "aws", "azure", "vllm", "ollama",
    "vector", "qdrant", "pinecone", "faiss", "pgvector", "postgres", "sql", "redis", "rabbitmq",
    "celery", "nginx", "computer vision", "nlp", "natural language", "transformer", "hugging face",
    "reinforcement learning", "evaluation", "evals", "fine tun", "prompt", "ci/cd", "github actions",
    "mlops", "inference", "embedding", "rest api", "microservice", "gpu", "cuda", "scikit",
    "pandas", "statistic", "time series", "data pipeline", "etl", "pydantic", "mcp",
]
AI_TITLE = re.compile(
    r"\b(ai|ml|machine learning|deep learning|llm|gen ?ai|generative|nlp|computer vision|"
    r"applied scientist|research engineer|data scientist|agents?)\b",
    re.I,
)
SWE_TITLE = re.compile(
    r"software|full ?stack|back ?end|developer|\bsde\b|platform|product engineer|"
    r"forward deployed|member of technical staff",
    re.I,
)
EARLY = re.compile(
    r"new grad|new college|entry level|early career|university (grad|graduate|hire)|recent grad|"
    r"graduate program|\b(engineer|scientist|developer)\s*(i|1)\b|\bassociate\b",
    re.I,
)
YEARS = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:\+|plus|or more)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:\+|plus|or more)?\s*(?:years?|yrs?)\b",
    re.I,
)
_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_WORDS = re.compile(r"\b(" + "|".join(_WORD_NUMS) + r")\b(?:\s*\(\d{1,2}\))?", re.I)
PHD_REQUIRED = re.compile(r"(phd|ph\.d\.?)\s+(is\s+)?(required|needed)|must have a (phd|ph\.d)", re.I)

# Section headings: "Preferred Qualifications" on its own line, or "Preferred:" starting a sentence.
_HEAD_LABEL = re.compile(r"^([A-Za-z][\w’'&/() -]{0,60}?)\s*:")
_PREFERRED_HEAD = re.compile(r"\b(?:preferred|prefer|nice[\s-]*to[\s-]*haves?|bonus|pluses|plus)\b", re.I)
_BASIC_HEAD = re.compile(r"\b(?:basic|minimum|required|requirements?|must[\s-]*haves?)\b", re.I)

# "N years" only counts as required experience when it reads like one: "3+ years in", "building ...",
# "relevant work experience", "of" or "with" when the sentence mentions experience, or "Experience: 3 years"
_YEARS_AFTER = re.compile(r"""^(?:\(s\))?['’]?\s*(?:
    (?P<strong>in\b(?!\s+(?:business|operation|existence|a\s+row)\b)
      | (?!during\b)[a-z]+ing\b
      | (?:(?!(?:but|our|we|their|that|which|is|are|was|has|have)\b)[\w/&-]+,?\s+){0,12}?(?:experience|exp)\b)
  | (?:of|with|as|across)\b)""",
    re.I | re.X)
_YEARS_PREFERRED = re.compile(r"^(?:\(s\))?['’]?\s*(?:of\s+)?(?:[\w/+#.-]+\s+){0,4}?(?:is\s+|are\s+|would\s+be\s+)?"
                              r"\(?(?:preferred|a\s+plus|nice\s+to\s+have|a\s+bonus|desired)\b", re.I)
_NOT_EXPERIENCE = re.compile(r"^(?:\(s\))?['’]?\s*(?:ago|old)\b", re.I)
_EXPERIENCE = re.compile(r"\bexperience\b|\bexp\.", re.I)
_WITHOUT_EXPERIENCE = re.compile(r"\b(?:without|no)\s+(?:any\s+|prior\s+)?(?:work\s+)?experience\b", re.I)
_OR_NUMBER = re.compile(r"\bor\b(?=\s+(?:\w+\s+){0,2}?\d)", re.I)
_DEGREES = (
    ("master", re.compile(r"\bmaster['’]?s?\b|\bm\.s\.|\bms\b|\bm\.?sc\b|\bm\.?eng\b|\bmba\b", re.I)),
    ("bachelor", re.compile(r"\bbachelor['’]?s?\b|\bb\.s\.|\bbs\b|\bb\.?sc\b|\bb\.a\.|\bba\b|\bb\.?eng\b|\bundergraduate\b", re.I)),
    ("phd", re.compile(r"\bph\.?\s?d\b|\bdoctora(?:te|l)\b", re.I)),
)
_DEGREE = dict(_DEGREES)


def _section_kind(label: str) -> str:
    preferred, basic = bool(_PREFERRED_HEAD.search(label)), bool(_BASIC_HEAD.search(label))
    return "preferred" if preferred and not basic else "basic" if basic and not preferred else "other"


def tagged_sentences(text: str) -> list[tuple[str, str]]:
    """(section, sentence) pairs, where section is "preferred", "basic" or "other" from the nearest heading above.
    A heading on a bullet ("• Bonus: Rust") only labels that bullet."""
    kind, out = "other", []
    for s, line_start, bullet in sentences(text):
        m = _HEAD_LABEL.match(s)
        if m and len(m.group(1).split()) <= 6:
            label = m.group(1)
        elif line_start and not bullet and len(s.split()) <= 6 and not re.search(r"\d|[.!?,]$", s):
            label = s
        else:
            label = None
        if label is not None and bullet:
            out.append((_section_kind(label), s))
            continue
        if label is not None:
            kind = _section_kind(label)
        out.append((kind, s))
    return out


def _required_years(s: str, loose: bool = False) -> list[tuple[int, int]]:
    """(N, position) for every "N years" in one sentence that describes required experience.
    loose: inside a degree path, any N years counts."""
    found = []
    for m in YEARS.finditer(s):
        after = s[m.end():]
        if _NOT_EXPERIENCE.match(after) or _YEARS_PREFERRED.match(after):
            continue
        a = _YEARS_AFTER.match(after)
        if loose or (a and (a.group("strong") or _EXPERIENCE.search(s))) or _EXPERIENCE.search(s[:m.start()]):
            if int(m.group(1)) <= 15:
                found.append((int(m.group(1)), m.start()))
    return found


def _degree_paths(s: str) -> int | None:
    """Years for the Master's path (or the Bachelor's when there's no Master's path) when a sentence lists degree
    paths: "PhD and 1+ years OR Master's and 5+ years OR ...", or "Bachelor's degree, OR 3+ years of experience",
    where the degree path needs no years. None when the sentence isn't a list of degree paths.
    A path runs from its degree to the next degree or "or N years". One that is just a name ("Master's or
    Bachelor's with 5+ years") takes the next path's number; otherwise years named before the first degree
    apply, or none."""
    mentions = []
    for start, end, name in sorted((m.start(), m.end(), name) for name, pat in _DEGREES for m in pat.finditer(s)):
        if not mentions or start >= mentions[-1][1]:
            mentions.append((start, end, name))
    if not mentions:
        return None
    alternatives = [m.start() for m in _OR_NUMBER.finditer(s) if m.start() > mentions[0][0]]
    if len({name for _, _, name in mentions}) < 2 and not alternatives:
        return None
    cuts = sorted([(st, name) for st, _, name in mentions] + [(a, None) for a in alternatives])
    prefix = [n for n, _ in _required_years(s[:cuts[0][0]])]
    years: dict[int, int] = {}
    for k in range(len(cuts) - 1, -1, -1):
        start, name = cuts[k]
        if name is None:
            continue
        span = s[start:cuts[k + 1][0] if k + 1 < len(cuts) else len(s)]
        own = [n for n, _ in _required_years(span, loose=True)]
        if own:
            years[k] = min(own)
        elif _WITHOUT_EXPERIENCE.search(span):
            years[k] = 0
        elif len(span.split()) <= 3 and k + 1 < len(cuts) and cuts[k + 1][1] and k + 1 in years:
            years[k] = years[k + 1]
        else:
            years[k] = min(prefix) if prefix else 0
    for want in ("master", "bachelor"):
        picked = [years[k] for k, (_, name) in enumerate(cuts) if name == want]
        if picked:
            return min(picked)
    return None


def _sentence_years(s: str) -> list[int]:
    if not YEARS.search(s):
        return []
    path = _degree_paths(s)
    if path is not None:
        return [path]
    found = _required_years(s)
    if not found:
        return []
    # Several numbers in one sentence: alternatives when an "or" leads into a number ("3+ years, or 1+ with ..."),
    # otherwise nested ("8+ years in X or Y, including 5+ years building ..."), where the bigger one is the requirement.
    ors = [m.start() for m in _OR_NUMBER.finditer(s)]
    clause_of = {sum(1 for o in ors if o < pos) for _, pos in found}
    nums = [n for n, _ in found]
    return [min(nums)] if len(clause_of) > 1 else [max(nums)]


def min_years_required(text: str) -> int | None:
    """Lowest required "N years of experience" in a posting. Preferred, Nice to have, Bonus and Plus sections
    don't count, and degree path lists use the Master's path."""
    text = _WORDS.sub(lambda m: str(_WORD_NUMS[m.group(1).lower()]), text or "")
    found = []
    for kind, s in tagged_sentences(text):
        if kind != "preferred":
            found += _sentence_years(s)
    return min(found) if found else None


def phd_in_basic(text: str) -> bool:
    """A PhD under Basic or Minimum Qualifications, when that section offers no Master's or Bachelor's path
    (postings often list "Bachelor's and 2 years; Master's and 0 years; PhD and 0 years" on separate lines)."""
    basic = [s for kind, s in tagged_sentences(text or "") if kind == "basic"]
    if any(_DEGREE["master"].search(s) or _DEGREE["bachelor"].search(s) for s in basic):
        return False
    return any(_DEGREE["phd"].search(s) for s in basic)


def phd_required(title: str, text: str) -> bool:
    if PHD_REQUIRED.search(title or "") or phd_in_basic(text):
        return True
    return any(PHD_REQUIRED.search(s) and not (_DEGREE["master"].search(s) or _DEGREE["bachelor"].search(s))
               for s, _, _ in sentences(text))


_MONTH_WORDS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
                "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
                "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
                "spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}
_START_NOW = re.compile(r"\bstart(?:ing)?(?:\s+date)?\s*(?::|is)?\s*(?:immediately|asap|as\s+soon\s+as\s+possible)\b"
                        r"|\bimmediate\s+start\b", re.I)
_DATE = (r"(?P<mon>" + "|".join(sorted(_MONTH_WORDS, key=len, reverse=True)) + r")\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?"
         r"(?P<year>20\d\d)|(?P<num>\d{1,2})/(?:\d{1,2}/)?(?P<year2>20\d\d)")
_START_DATE = re.compile(
    r"\b(?P<cue>start(?:s|ing)?(?:\s+date)?|begin(?:s|ning)?|commenc(?:e|es|ing))\s*(?::|is|will\s+be|of)?\s*"
    r"(?:(?:on|in|by|from|around|the)\s+){0,2}(?P<date>" + _DATE + r"|(?P<year3>20\d\d))\b",
    re.I,
)
_ANY_DATE = re.compile(r"\b(?:" + _DATE + r")\b", re.I)


def _when(m) -> tuple[int, int] | None:
    if m.group("mon"):
        return int(m.group("year")), _MONTH_WORDS[m.group("mon").lower()]
    if m.group("num"):
        return int(m.group("year2")), int(m.group("num"))
    return None


def year_month(value) -> tuple[int, int] | None:
    """"2027-01" or (2027, 1) -> (2027, 1)."""
    if not value:
        return None
    if isinstance(value, (tuple, list)):
        return int(value[0]), int(value[1])
    m = re.match(r"^\s*(\d{4})-(\d{1,2})", str(value))
    return (int(m.group(1)), int(m.group(2))) if m else None


def start_date_flag(text: str, earliest: tuple[int, int] | None = None) -> str:
    """Red flag text if the posting wants you to start immediately or before the earliest start month."""
    if _START_NOW.search(text or ""):
        return "asks to start immediately"
    if earliest:
        for s, _, _ in sentences(text):
            m = _START_DATE.search(s)
            if not m:
                continue
            when = _when(m)
            if when is None and "date" in m.group("cue").lower():  # a bare year only after "start date"
                when = (int(m.group("year3")), 1)
            if when is None:
                continue
            latest = max([when] + [_when(d) for d in _ANY_DATE.finditer(s, m.end())])
            if latest < earliest:
                return f"start date {m.group('date').strip(' .,')} is before {calendar.month_name[earliest[1]]} {earliest[0]}"
    return ""


class KeywordScorer:
    def __init__(self, profile: Profile, default_hook: str = "", earliest_start=None):
        self.profile = profile
        self.default_hook = default_hook
        self.earliest = year_month(earliest_start)
        self._entry_skills = {
            eid: {s for s in SKILLS if s in e.text.lower()} for eid, e in profile.entries.items()
        }

    def lead_with(self, jd_skills: set[str]) -> list[str]:
        def best(prefix: str, n: int) -> list[str]:
            ranked = sorted(
                ((len(jd_skills & skills), eid) for eid, skills in self._entry_skills.items() if eid.startswith(prefix)),
                reverse=True,
            )
            return [eid for overlap, eid in ranked[:n] if overlap > 0]

        return best("EXP", 2) + best("PROJ", 1)

    def red_flags(self, job: Job, years=None) -> list[str]:
        """Problems read straight from the text. Shown in the email and they lower the score, but never block."""
        desc = job.description or ""
        years = min_years_required(desc) if years is None else years
        flags = []
        if years is not None and years >= 3:
            flags.append(f"asks for {years}+ years")
        if phd_required(job.title, desc):
            flags.append("PhD required")
        start = start_date_flag(desc, self.earliest)
        if start:
            flags.append(start)
        return flags

    def score(self, job: Job) -> Score:
        title = job.title or ""
        text = f"{title}\n{job.description or ''}"
        low = text.lower()
        s = 5
        if AI_TITLE.search(title):
            s += 2
        elif SWE_TITLE.search(title):
            s += 1
        if EARLY.search(title) or EARLY.search(low[:4000]):
            s += 1
        jd_skills = {k for k in SKILLS if k in low}
        s += min(2, len(jd_skills) // 5)
        years = min_years_required(job.description or "")
        if years is not None:
            if years >= 5:
                s -= 4
            elif years >= 3:
                s -= 2
            elif years <= 2:
                s += 1
        flags = self.red_flags(job, years)
        if "PhD required" in flags:
            s -= 3
        s = max(1, min(10, s))
        if flags:  # a keyword estimate never rates a red flagged job highly, even when the AI batch failed
            s = min(s, 3)
        why = f"{len(jd_skills)} of your skills show up"
        why += f", asks for {years}+ years" if years is not None else ""
        if not job.description:
            why = "No description available, scored from the title only"
        return Score(score=s, why=why, lead_with=self.lead_with(jd_skills), hook=self.default_hook, red_flags=flags, by="keywords")

    def score_many(self, jobs: list[Job]) -> list[Score]:
        return [self.score(j) for j in jobs]


SCORER_RULES = """You screen job postings for Aatmaj Amol Salunke, using the candidate summary above.
Facts: M.S. in Artificial Intelligence, Northeastern University, graduating December 2026. He wants an entry level (new grad, 0 to 2 years) full time role in the US starting January 2027. Work authorization: F-1 OPT from December 2026 with STEM OPT eligibility (3 years total); he will need H-1B sponsorship after that.

Score each job from 1 to 10 for how likely he is to get an interview:
9 or 10: entry level role that closely matches his strongest work
7 or 8: good match with a few gaps
5 or 6: plausible but a stretch
1 to 4: poor match (more senior than entry level, a different field, or a dealbreaker)

Dealbreakers. If one clearly applies, the score is at most 3 and you name it in red_flags:
{dealbreakers}

For each job return an object:
{"job": <job number>, "score": <1 to 10>, "level_fit": "good" or "stretch" or "too senior", "why": "<at most 20 words>", "lead_with": ["<up to 3 IDs like EXP-8 or PROJ-12, best first, only IDs from the summary>"], "hook": "<one first person sentence of at most 110 characters for a LinkedIn note to someone at the company: specific to the role, only facts from the summary, casual, no hyphens or dashes>", "red_flags": ["<short phrases>"]}
Reply with JSON only, shaped like {"results": [ ... ]}."""

_FOCUS = re.compile(r"(requirements|qualifications|what you.ll (need|bring)|you (have|bring|are)|minimum|preferred|about you|skills)", re.I)
_KEY_FACTS = (  # in priority order when they don't all fit
    re.compile(r"sponsor|visa|h-?1b|authori[sz]|citizen|green\s+card|permanent\s+resident|u\.?\s?s\.?\s+persons?|clearance|\bitar\b", re.I),
    re.compile(r"\d\s*\+?\s*(?:years?|yrs?)\b|\b(?:bachelor|master|ph\.?\s?d|doctora|degree)", re.I),
    re.compile(_START_NOW.pattern + "|" + _START_DATE.pattern + r"|\bstart\s+date\b|\bavailable\s+to\s+start\b", re.I),
)
_EEO = re.compile(r"without regard to|regardless of|equal (?:employment )?opportunit|discriminat|protected veteran", re.I)


def focus_jd(text: str, n: int) -> str:
    """At most n characters for the AI: every sentence about years, degree, sponsorship, authorization,
    citizenship or start date, wherever it appears, then as much of the qualifications section as fits."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= n:
        return flat
    sents = [clip(re.sub(r"\s+", " ", s), 400) for s, _, _ in sentences(text)]
    prio = {}
    for i, s in enumerate(sents):
        if _EEO.search(s) and not re.search(r"sponsor|visa|authori[sz]", s, re.I):
            continue  # equal opportunity boilerplate mentions citizenship but says nothing about this job
        p = next((k for k, pat in enumerate(_KEY_FACTS) if pat.search(s)), None)
        if p is not None:
            prio[i] = p
    used, picked = 0, set()
    for p in range(len(_KEY_FACTS)):
        for i in sorted(k for k, v in prio.items() if v == p):
            if used + len(sents[i]) + 1 <= n:
                picked.add(i)
                used += len(sents[i]) + 1
    offsets, pos = [], 0
    for s in sents:
        offsets.append(pos)
        pos += len(s) + 1
    start = next((i for i, s in enumerate(sents) if offsets[i] >= pos * 0.15 and _FOCUS.search(s)), 0)
    fill = []
    used += 3  # the " … " between the two parts
    for i in list(range(start, len(sents))) + list(range(0, start)):
        if i in picked:
            continue
        if used + len(sents[i]) + 1 > n:
            break
        fill.append(sents[i])
        used += len(sents[i]) + 1
    key = " ".join(sents[i] for i in sorted(picked))
    return clip(" … ".join(x for x in (key, " ".join(fill)) if x), n)


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(cleaned[start: end + 1])


def job_number(value) -> int | None:
    """The model's "job" field: 1, 1.0, "1", "JOB 1", "Job 1" or "job #1"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else None
    m = re.fullmatch(r"\s*(?:job\s*#?\s*)?(\d+)\s*", str(value or ""), re.I)
    return int(m.group(1)) if m else None


class LLMScorer:
    """Scores jobs in small batches so each request fits Groq's free plan."""

    def __init__(self, llm, model: str, profile: Profile, dealbreakers: list[str], fallback: KeywordScorer,
                 batch_size: int = 6, max_jobs: int = 12, jd_chars: int = 1400, log=print):
        self.llm, self.model, self.profile, self.fallback = llm, model, profile, fallback
        self.batch_size, self.max_jobs, self.jd_chars, self.log = batch_size, max_jobs, jd_chars, log
        rules = "\n".join(f"- {d}" for d in dealbreakers) or "- none"
        self.system = [profile.digest(), SCORER_RULES.replace("{dealbreakers}", rules)]
        self.disabled = False

    def _block(self, n: int, job: Job) -> str:
        desc = focus_jd(job.description, self.jd_chars) or "(no description available, judge from the title)"
        return f"JOB {n}\nCompany: {job.company}\nTitle: {job.title}\nLocation: {job.location}\nDescription excerpt: {desc}"

    def _to_score(self, item: dict) -> Score:
        if "score" not in item:
            raise ValueError("no score in the reply")
        lead = [str(x).upper().strip() for x in item.get("lead_with") or []]
        return Score(
            score=max(1, min(10, int(round(float(item.get("score", 1)))))),
            why=clip(str(item.get("why", "")), 220),
            level_fit=str(item.get("level_fit", "")),
            lead_with=[x for x in lead if x in self.profile.entries][:3],
            hook=clip(dehyphen(str(item.get("hook", ""))), 140),
            red_flags=[clip(str(x), 80) for x in (item.get("red_flags") or [])][:4],
            by="ai",
        )

    def _batch(self, jobs: list[Job]) -> list[Score | None]:
        user = "Score each job.\n\n" + "\n\n".join(self._block(i + 1, j) for i, j in enumerate(jobs))
        text = self.llm.chat(self.model, self.system, [{"role": "user", "content": user}],
                             max_tokens=400 + 220 * len(jobs), json_mode=True)
        data = parse_json_object(text)
        items = data.get("results") or data.get("jobs") or []
        by_n = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            n = job_number(it.get("job"))
            if n is None:
                self.log(f"  AI reply had a result with an unreadable job number {it.get('job')!r}, ignoring it")
                continue
            by_n[n] = it
        if len(jobs) == 1 and not by_n and "score" in data:
            by_n = {1: data}
        out: list[Score | None] = []
        for n, job in enumerate(jobs, 1):
            item = by_n.get(n)
            if item is None:
                self.log(f"  AI reply left out job {n} ({job.title} at {job.company}), using keywords for it")
                out.append(None)
                continue
            try:
                out.append(self._to_score(item))
            except (TypeError, ValueError) as exc:  # one bad result only costs that job its AI score
                self.log(f"  AI reply for job {n} ({job.title} at {job.company}) was unusable, using keywords for it: {exc}")
                out.append(None)
        return out

    def _with_flags(self, score: Score, job: Job) -> Score:
        """Red flags found in the text go on the AI score too: listed first, and the score drops by 3."""
        flags = self.fallback.red_flags(job)
        if flags:
            score.red_flags = (flags + [f for f in score.red_flags if f not in flags])[:4]
            score.score = max(1, score.score - 3)
        return score

    def score_many(self, jobs: list[Job]) -> list[Score]:
        from .llm import DailyLimit, TooLarge

        results: list[Score | None] = [None] * len(jobs)
        todo = list(range(min(len(jobs), self.max_jobs)))
        size = self.batch_size
        while todo and not self.disabled:
            batch, todo = todo[:size], todo[size:]
            try:
                for i, sc in zip(batch, self._batch([jobs[i] for i in batch])):
                    results[i] = sc
            except DailyLimit:
                self.disabled = True
                self.log("  AI scoring hit today's limit, using keyword scores for the rest of the day")
            except TooLarge:
                if size > 1:
                    size = max(1, size // 2)
                    todo = batch + todo
                else:
                    self.log(f"  one job was too long to score with AI: {jobs[batch[0]].title}")
            except Exception as exc:
                self.log(f"  AI scoring failed for a batch, using keywords: {exc}")
        return [self._with_flags(r, j) if r else self.fallback.score(j) for r, j in zip(results, jobs)]
