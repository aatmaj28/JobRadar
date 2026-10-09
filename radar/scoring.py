"""Fit scoring.

KeywordScorer is free and always available. ClaudeScorer reads the whole job
description against Aatmaj's master files and is far more accurate. It falls
back to KeywordScorer for any job where the API call fails.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .models import Job
from .profile import Profile
from .text import clip, dehyphen


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
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*years?", re.I)
PHD_REQUIRED = re.compile(r"(phd|ph\.d\.?)\s+(is\s+)?(required|needed)|must have a (phd|ph\.d)", re.I)


def min_years_required(text: str) -> int | None:
    found = []
    for m in YEARS.finditer(text or ""):
        window = text[max(0, m.start() - 40): m.end() + 60].lower()
        if "experience" in window or "exp." in window:
            years = int(m.group(1))
            if years <= 15:
                found.append(years)
    return min(found) if found else None


class KeywordScorer:
    def __init__(self, profile: Profile, default_hook: str = ""):
        self.profile = profile
        self.default_hook = default_hook
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
        flags = []
        if PHD_REQUIRED.search(low):
            s -= 3
            flags.append("PhD required")
        if years is not None and years >= 3:
            flags.append(f"asks for {years}+ years")
        s = max(1, min(10, s))
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


def focus_jd(text: str, n: int) -> str:
    """Keep the part of a long description that matters most for fit: the start and the requirements."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    m = _FOCUS.search(text, int(len(text) * 0.15))
    head = text[: int(n * 0.35)]
    tail_start = m.start() if m else int(len(text) * 0.4)
    return head + " … " + text[tail_start: tail_start + int(n * 0.65)]


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(cleaned[start: end + 1])


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

    def _to_score(self, item: dict | None) -> Score | None:
        if not isinstance(item, dict) or "score" not in item:
            return None
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
        by_n = {int(it.get("job", 0) or 0): it for it in items if isinstance(it, dict)}
        if len(jobs) == 1 and not by_n and "score" in data:
            by_n = {1: data}
        return [self._to_score(by_n.get(i + 1)) for i in range(len(jobs))]

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
        return [r or self.fallback.score(j) for r, j in zip(results, jobs)]
