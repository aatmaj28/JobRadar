"""Tailored resume + cover letter, using Aatmaj's own prompts and files unchanged.

Flow per document:
  1. Ask Claude with his prompt (system) + his 3 FILES + the job (user message).
  2. Pull the LaTeX out of the reply and make a few unicode characters safe.
  3. Compile with pdflatex. If it fails, or the resume runs past one page,
     send the problem back to Claude and try again (a couple of rounds max).
  4. If pdflatex isn't available (e.g. your laptop), keep the .tex so you can
     paste it into Overleaf like before.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import Job
from .profile import Profile
from .text import clip

AUTOMATION_NOTE = """

---

## AUTOMATION NOTE (added by the job radar pipeline)
This request comes from an automated pipeline, so nobody is available to answer follow up questions. Never ask clarifying questions. If the job description is thin, make the best selection you can from what is there.
Reply with the short rationale and then the complete LaTeX document inside ONE ```latex code block. The code must compile with pdflatex.
"""

SAFE_CHARS = {
    "→": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}", "×": r"\ensuremath{\times}",
    "≈": r"\ensuremath{\approx}", "≥": r"\ensuremath{\geq}", "≤": r"\ensuremath{\leq}",
    "±": r"\ensuremath{\pm}", "τ": r"\ensuremath{\tau}", "μ": r"\ensuremath{\mu}",
    "²": r"\ensuremath{^{2}}", "³": r"\ensuremath{^{3}}", "•": r"\ensuremath{\bullet}",
    "–": "--", "—": "---", "“": "``", "”": "''", "‘": "`", "’": "'", "…": r"\ldots{}",
    "\u00a0": "~", "\u200b": "",
}

_FENCE = re.compile(r"```(?:latex|tex)?[ \t]*\n(.*?)```", re.S | re.I)
_DOC = re.compile(r"\\documentclass.*?\\end\{document\}", re.S)


def extract_latex(reply: str) -> str:
    for block in _FENCE.findall(reply or ""):
        if "\\documentclass" in block:
            return block.strip()
    m = _DOC.search(reply or "")
    if m:
        return m.group(0).strip()
    raise ValueError("no LaTeX document in the reply")


def make_safe(tex: str) -> str:
    for ch, rep in SAFE_CHARS.items():
        tex = tex.replace(ch, rep)
    return tex


def safe_name(company: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", company or "Company").strip("_")[:40] or "Company"


@dataclass
class CompileResult:
    ok: bool
    pages: int | None
    pdf: Path | None
    log_tail: str = ""


def tex_available(wait_seconds: int = 0) -> bool:
    """In GitHub Actions LaTeX installs in the background; wait for it if asked."""
    marker = os.environ.get("RADAR_TEX_READY_FILE")
    deadline = time.time() + wait_seconds
    if marker:
        while not os.path.exists(marker) and time.time() < deadline:
            time.sleep(3)
    return shutil.which("pdflatex") is not None


def compile_tex(tex_path: Path, timeout: int = 120) -> CompileResult:
    env = {**os.environ, "max_print_line": "10000"}
    cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", tex_path.name]
    try:
        proc = subprocess.run(cmd, cwd=tex_path.parent, capture_output=True, text=True, timeout=timeout, env=env)
        output = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return CompileResult(False, None, None, "pdflatex timed out")
    log_path = tex_path.with_suffix(".log")
    log = log_path.read_text(errors="ignore") if log_path.exists() else output
    flat = log.replace("\n", "")
    m = re.search(r"Output written on .*?\((\d+) pages?", flat)
    pages = int(m.group(1)) if m else None
    pdf = tex_path.with_suffix(".pdf")
    ok = code == 0 and pdf.exists() and pages is not None
    tail = ""
    if not ok:
        lines = log.splitlines()
        errors = [i for i, line in enumerate(lines) if line.startswith("!") or re.search(r"\.tex:\d+:", line)]
        if errors:
            i = errors[0]
            tail = "\n".join(lines[max(0, i - 2): i + 8])
        else:
            tail = "\n".join(lines[-25:])
    return CompileResult(ok, pages, pdf if pdf.exists() else None, clip(tail, 3000))


@dataclass
class Doc:
    kind: str  # resume | cover
    name: str
    tex_path: Path
    pdf_path: Path | None = None
    pages: int | None = None
    ok: bool = False
    rationale: str = ""
    note: str = ""
    messages: list = field(default_factory=list)


class Preparer:
    def __init__(self, llm, model: str, profile: Profile, out_dir: Path, file_prefix: str,
                 max_fix_rounds: int = 2, log=print):
        self.llm = llm
        self.model = model
        self.profile = profile
        self.out_dir = Path(out_dir)
        self.file_prefix = file_prefix
        self.max_fix_rounds = max_fix_rounds
        self.log = log
        files = profile.files_block()
        self.systems = {
            "resume": [files, profile.resume_prompt + AUTOMATION_NOTE],
            "cover": [files, profile.cover_prompt + AUTOMATION_NOTE],
        }

    def fits(self, kind: str = "resume") -> bool:
        from .llm import estimate_tokens

        return estimate_tokens(*self.systems[kind], "x" * 9000) + 9000 <= self.llm.max_request_tokens

    def _ask(self, kind: str, messages: list, max_tokens: int) -> str:
        return self.llm.chat(self.model, self.systems[kind], messages, max_tokens=max_tokens, reasoning="medium")

    def _user_message(self, job: Job, kind: str) -> str:
        return paste_block(job, kind)

    def draft(self, job: Job, kind: str) -> Doc:
        label = "Resume" if kind == "resume" else "Cover_Letter"
        name = f"{self.file_prefix}_{label}_{safe_name(job.company)}"
        folder = self.out_dir / safe_name(job.company + "_" + job.job_id)[:60]
        folder.mkdir(parents=True, exist_ok=True)
        doc = Doc(kind=kind, name=name, tex_path=folder / f"{name}.tex")
        doc.messages = [{"role": "user", "content": self._user_message(job, kind)}]
        self._take(doc, self._ask(kind, doc.messages, 9000 if kind == "resume" else 5000))
        return doc

    def _take(self, doc: Doc, reply: str) -> None:
        doc.messages.append({"role": "assistant", "content": reply})
        tex = make_safe(extract_latex(reply))
        doc.rationale = clip(reply.split("```", 1)[0].strip(), 1500)
        doc.tex_path.write_text(tex, encoding="utf-8")

    def finish(self, doc: Doc, can_compile: bool) -> Doc:
        """Compile, and loop back to the model on errors or a second page."""
        if not can_compile:
            doc.note = "LaTeX isn't installed here, so this is the .tex for Overleaf"
            return doc
        for round_no in range(self.max_fix_rounds + 1):
            result = compile_tex(doc.tex_path)
            doc.ok, doc.pages, doc.pdf_path = result.ok, result.pages, result.pdf
            if result.ok and result.pages == 1:
                return doc
            if round_no == self.max_fix_rounds:
                break
            if not result.ok:
                fix = ("The LaTeX failed to compile with pdflatex. Here is the end of the log:\n\n"
                       f"{result.log_tail}\n\nFix it and return the complete corrected LaTeX in one ```latex block.")
            else:
                fix = (f"It compiled to {result.pages} pages. It must be exactly one page. Follow your one page rules: "
                       "trim bullet wording first, keep every metric, tool name, section and the spacing rules. "
                       "Return the complete LaTeX in one ```latex block.")
            doc.messages.append({"role": "user", "content": fix})
            try:
                self._take(doc, self._ask(doc.kind, doc.messages, 9000 if doc.kind == "resume" else 5000))
            except Exception as exc:
                doc.note = f"fix round failed: {exc}"
                break
        if doc.ok and doc.pages and doc.pages > 1:
            doc.note = f"compiled to {doc.pages} pages, worth a quick trim in Overleaf"
        elif not doc.ok:
            doc.note = "didn't compile, so this is the .tex to fix in Overleaf"
        return doc


def paste_block(job: Job, kind: str = "resume") -> str:
    """Exactly the INPUT FORMAT his Resume Maker and Cover Letter Maker projects expect."""
    lines = [f"1. Company Name: {job.company}", f"2. Job Role / Title: {job.title}",
             f"3. Job Description / Requirements:\n{clip(job.description or '(not available, open the posting and paste it here)', 15000)}"]
    if kind == "cover":
        lines.append(f"4. Company Location / Address: {job.location or 'USA'}")
    return "\n\n".join(lines)
