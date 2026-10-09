"""Aatmaj's source files: the same files his Claude Projects use."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILES = {
    "sample_resume": "AATMAJ_AMOL_SALUNKE_SAMPLE_RESUME.txt",
    "work": "AATMAJ_AMOL_SALUNKE_MASTER_WORK_EXPERIENCES.txt",
    "projects": "AATMAJ_AMOL_SALUNKE_MASTER_PROJECTS.txt",
    "resume_prompt": "RESUME_MAKER_PROMPT.txt",
    "cover_prompt": "COVER_LETTER_MAKER_PROMPT.txt",
    "linkedin": "LINKEDIN_PROFILE.txt",
}

_ENTRY = re.compile(r"^##\s+((?:EXP|PROJ)-\d+):\s*(.+?)\s*$", re.M)
_ORG = re.compile(r"^\*\*Organization:\*\*\s*(.+?)\s*$", re.M)


@dataclass
class Entry:
    id: str
    title: str
    org: str
    text: str

    @property
    def label(self) -> str:
        if self.id.startswith("EXP"):
            org = re.sub(r"\(.*?\)", "", self.org)
            org = re.sub(r",?\s*\b(Inc|LLC|Ltd)\b\.?", "", org).strip(" ,")
            return f"{org} ({self.id})" if org else self.id
        name = re.split(r"\s[–—-]\s|:\s", self.title, maxsplit=1)[0].strip()
        return f"{name} ({self.id})"


@dataclass
class Profile:
    root: Path
    names: dict
    sample_resume: str = ""
    work: str = ""
    projects: str = ""
    resume_prompt: str = ""
    cover_prompt: str = ""
    linkedin: str = ""
    entries: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path, names: dict | None = None) -> "Profile":
        names = {**DEFAULT_FILES, **(names or {})}
        p = cls(root=Path(root), names=names)
        for attr, filename in names.items():
            path = p.root / filename
            setattr(p, attr, path.read_text(encoding="utf-8") if path.exists() else "")
        for text in (p.work, p.projects):
            p.entries.update(parse_entries(text))
        return p

    def missing(self) -> list[str]:
        required = ["sample_resume", "work", "projects", "resume_prompt", "cover_prompt"]
        return [self.names[a] for a in required if not getattr(self, a)]

    def files_block(self) -> str:
        """The three FILES his resume and cover letter prompts refer to."""
        n = self.names
        return (
            "These are the FILES referenced in the instructions.\n\n"
            f"=== File 1: {n['sample_resume']} ===\n{self.sample_resume}\n\n"
            f"=== File 2: {n['work']} ===\n{self.work}\n\n"
            f"=== File 3: {n['projects']} ===\n{self.projects}\n"
        )

    def digest(self) -> str:
        """About 2K tokens: entry IDs with their tags, skills and education. Small enough for Groq's free plan."""
        lines = [l.strip() for l in self.resume_prompt.splitlines() if re.match(r"^- \*\*(EXP|PROJ)-\d+:\*\*", l.strip())]
        if not lines:
            lines = [f"- {e.id}: {e.title}, {e.org}" for e in self.entries.values()]
        skills = [l.strip() for l in self.sample_resume.splitlines()
                  if re.match(r"^(Languages|Libraries/Frameworks|Data Science|Backend/API|Deployment & Infrastructure):", l.strip())]
        edu = re.search(r"## EDUCATION.*?\n(.*?)\n---", self.sample_resume, re.S)
        text = ("CANDIDATE SUMMARY (his resume files use these exact IDs)\n\n"
                "Experience and projects:\n" + "\n".join(lines) + "\n\nSkills:\n" + "\n".join(skills))
        if edu:
            text += "\n\nEducation:\n" + re.sub(r"[ \t]{2,}", " | ", edu.group(1).strip())
        return text

    def label(self, entry_id: str) -> str:
        e = self.entries.get(entry_id.upper().strip())
        return e.label if e else entry_id


def parse_entries(text: str) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    matches = list(_ENTRY.finditer(text or ""))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        org = _ORG.search(body)
        out[m.group(1)] = Entry(id=m.group(1), title=m.group(2), org=org.group(1) if org else "", text=body)
    return out
