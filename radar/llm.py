"""LLM clients. Groq (default) and Claude share one small interface:

    chat(model, system_parts, messages, max_tokens, json_mode) -> str

Groq's free plan allows about 8K tokens per minute and 200K per day per model,
so every request is kept small. A request that can't fit raises TooLarge, and
hitting the daily cap raises DailyLimit; callers fall back gracefully.
"""
from __future__ import annotations

import re
import time

import requests


class LLMError(Exception):
    pass


class TooLarge(LLMError):
    """The request is bigger than this plan allows in one go."""


class DailyLimit(LLMError):
    """Daily quota used up. Try again tomorrow."""


def estimate_tokens(*texts: str) -> int:
    return int(sum(len(t or "") for t in texts) / 3.6) + 50


_THINK = re.compile(r"<think>.*?</think>", re.S)
_WAIT = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)


def _retry_after(r) -> float:
    try:
        return float(r.headers.get("retry-after"))
    except (TypeError, ValueError):
        m = _WAIT.search(r.text or "")
        if m:
            return int(m.group(1) or 0) * 60 + float(m.group(2))
    return 20.0


class GroqLLM:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, plan: str = "free", log=print, timeout: int = 120, session=None):
        self.key = api_key
        self.plan = plan
        self.log = log
        self.timeout = timeout
        self.session = session or requests.Session()
        # free plan: ~8K tokens per minute per model, and one request must fit inside that
        self.max_request_tokens = 7000 if plan == "free" else 120000
        self.used: dict[str, int] = {}

    def chat(self, model, system_parts, messages, max_tokens=1500, json_mode=False, reasoning="low") -> str:
        system = "\n\n".join(p for p in system_parts if p)
        if estimate_tokens(system, *[m["content"] for m in messages]) + max_tokens > self.max_request_tokens:
            raise TooLarge(f"request needs more than {self.max_request_tokens} tokens on the Groq {self.plan} plan")
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}] + list(messages)}
        optional = {}
        if json_mode:
            optional["response_format"] = {"type": "json_object"}
        if model.startswith("openai/gpt-oss") and reasoning:
            optional["reasoning_effort"] = reasoning
        for attempt in range(6):
            r = self.session.post(self.URL, json={**body, **optional}, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.key}"})
            if r.status_code == 200:
                data = r.json()
                self.used[model] = self.used.get(model, 0) + int((data.get("usage") or {}).get("total_tokens") or 0)
                text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                return _THINK.sub("", text).strip()
            msg = (r.text or "")[:600]
            low = msg.lower()
            if r.status_code == 413 or "request too large" in low:
                raise TooLarge(msg)
            if r.status_code == 429:
                wait = _retry_after(r)
                if "per day" in low or "(tpd)" in low or "(rpd)" in low or wait > 75:
                    raise DailyLimit(msg)
                time.sleep(wait + 0.5)
                continue
            if r.status_code == 400 and optional:
                if "json_validate_failed" in low or "response_format" in low:
                    optional.pop("response_format", None)
                    continue
                if "reasoning_effort" in low:
                    optional.pop("reasoning_effort", None)
                    continue
            if r.status_code >= 500:
                time.sleep(3 + attempt * 3)
                continue
            raise LLMError(f"Groq HTTP {r.status_code}: {msg}")
        raise LLMError("Groq kept rate limiting this request")


class ClaudeLLM:
    """Optional. Needs `pip install anthropic` and ANTHROPIC_API_KEY."""

    def __init__(self, api_key: str, log=print):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.max_request_tokens = 150000
        self.log = log
        self.used: dict[str, int] = {}

    def chat(self, model, system_parts, messages, max_tokens=1500, json_mode=False, reasoning=None) -> str:
        system = [{"type": "text", "text": p} for p in system_parts if p]
        if system:
            system[0]["cache_control"] = {"type": "ephemeral"}
        try:
            msg = self.client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=list(messages))
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()


def make_llm(cfg: dict, env: dict, log=print):
    provider = (cfg.get("llm") or {}).get("provider", "groq")
    if provider == "groq" and env.get("GROQ_API_KEY"):
        return GroqLLM(env["GROQ_API_KEY"], plan=(cfg.get("llm") or {}).get("groq_plan", "free"), log=log)
    if provider == "anthropic" and env.get("ANTHROPIC_API_KEY"):
        return ClaudeLLM(env["ANTHROPIC_API_KEY"], log=log)
    return None
