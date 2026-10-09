"""Pings. Email through Gmail by default; Telegram and Discord also work if you add their secrets."""
from __future__ import annotations

import html
import json
import mimetypes
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .models import Job
from .scoring import Score
from .text import clip, posted_label


class Email:
    def __init__(self, address: str, app_password: str, to: str | None = None,
                 host: str = "smtp.gmail.com", port: int = 465):
        self.address, self.password, self.to = address, app_password, to or address
        self.host, self.port = host, port

    def send(self, subject: str, html_body: str, text_body: str, attachments=(), reply_to: str | None = None) -> str:
        msg = EmailMessage()
        msg["From"] = f"Job Radar <{self.address}>"
        msg["To"] = self.to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        mid = make_msgid(domain="job-radar")
        msg["Message-ID"] = mid
        if reply_to:  # keeps the resume in the same Gmail thread as its job
            msg["In-Reply-To"] = reply_to
            msg["References"] = reply_to
        msg.set_content(text_body or " ")
        msg.add_alternative(html_body, subtype="html")
        for path in attachments:
            path = Path(path)
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        with smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=40) as smtp:
            smtp.login(self.address, self.password)
            smtp.send_message(msg)
        return mid


class Telegram:
    def __init__(self, token: str, chat_id: str, http):
        self.api, self.chat_id, self.http = f"https://api.telegram.org/bot{token}/", chat_id, http

    def send_text(self, text: str, parse_html: bool = True) -> str | None:
        data = {"chat_id": self.chat_id, "text": clip(text, 4096), "disable_web_page_preview": "true"}
        if parse_html:
            data["parse_mode"] = "HTML"
        r = self.http.post(self.api + "sendMessage", data=data)
        if not r.ok:
            raise RuntimeError(f"Telegram {r.status_code}: {r.text[:200]}")
        return str(r.json().get("result", {}).get("message_id"))

    def send_file(self, path: Path, caption: str = "") -> None:
        with open(path, "rb") as fh:
            r = self.http.post(self.api + "sendDocument", data={"chat_id": self.chat_id, "caption": clip(caption, 1024)},
                               files={"document": (Path(path).name, fh)}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"Telegram file {r.status_code}: {r.text[:200]}")


class Discord:
    def __init__(self, webhook_url: str, http):
        self.url, self.http = webhook_url, http

    def _payload(self, text: str) -> dict:
        return {"content": clip(text, 2000), "allowed_mentions": {"parse": []}, "flags": 4}

    def send_text(self, text: str) -> None:
        r = self.http.post(self.url, json=self._payload(text))
        if not r.ok:
            raise RuntimeError(f"Discord {r.status_code}: {r.text[:200]}")

    def send_file(self, path: Path, caption: str = "") -> None:
        with open(path, "rb") as fh:
            r = self.http.post(self.url, data={"payload_json": json.dumps(self._payload(caption))},
                               files={"files[0]": (Path(path).name, fh)}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"Discord file {r.status_code}: {r.text[:200]}")


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _meta(job: Job, score: Score):
    where = " · ".join(x for x in [job.location, posted_label(job.posted_at)] if x)
    level = f" · {score.level_fit} level fit" if score.level_fit else ""
    by = " (keyword estimate)" if score.by == "keywords" else ""
    return where, level, by


BOX = "background:#f3f4f6;border-radius:8px;padding:10px 12px;margin:6px 0 14px;white-space:pre-wrap"


def email_job(job: Job, score: Score, links, notes, labels, resume_coming: bool, paste: str):
    where, level, by = _meta(job, score)
    subject = f"{score.score}/10 · {job.title} · {job.company}"
    link_html = " &nbsp;|&nbsp; ".join(f'<a href="{_e(u)}">{_e(l)}</a>' for l, u in links)
    parts = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#111;max-width:680px">',
        f'<div style="font-size:18px;font-weight:bold">{_e(job.title)}</div>',
        f"<div><b>{_e(job.company)}</b> · {_e(where)}</div>",
        f"<div>Fit <b>{score.score}/10</b>{_e(level)}{_e(by)}</div>",
    ]
    if score.why:
        parts.append(f"<p style='margin:8px 0'>{_e(score.why)}</p>")
    if labels:
        parts.append(f"<div>Lead with: {_e(', '.join(labels))}</div>")
    if score.red_flags:
        parts.append(f"<div>⚠️ {_e('; '.join(score.red_flags))}</div>")
    parts.append(f'<p style="margin:14px 0"><a href="{_e(job.url)}" style="background:#111;color:#fff;padding:8px 14px;'
                 f'border-radius:6px;text-decoration:none">Apply now</a> &nbsp; {link_html}</p>')
    for title, note in zip(["Referral ask", "Advice ask"], notes):
        parts.append(f"<div><b>{title}</b> (swap in their name)</div><div style='{BOX}'>{_e(note)}</div>")
    if resume_coming:
        parts.append("<p>📄 Your tailored resume will land in this thread in a few minutes.</p>")
    else:
        parts.append("<div><b>Paste into your Resume Maker project</b></div>"
                     f"<div style='{BOX};font-family:Menlo,Consolas,monospace;font-size:12px'>{_e(paste)}</div>")
    parts.append("</div>")
    text = [job.title, f"{job.company} · {where}", f"Fit {score.score}/10{level}{by}", score.why,
            ("Lead with: " + ", ".join(labels)) if labels else "", ("Red flags: " + "; ".join(score.red_flags)) if score.red_flags else "",
            f"Apply: {job.url}"] + [f"{l}: {u}" for l, u in links]
    for title, note in zip(["Referral ask", "Advice ask"], notes):
        text += ["", f"{title}:", note]
    text += ["", "Resume coming in this thread shortly." if resume_coming else "Paste into your Resume Maker project:\n\n" + paste]
    return subject, "\n".join(parts), "\n".join(t for t in text if t is not None)


def chat_job(job: Job, score: Score, links, notes, labels, resume_coming: bool, markdown: bool = False) -> str:
    where, level, by = _meta(job, score)
    if markdown:
        lines = [f"🎯 **{job.title}**", f"**{job.company}** · {where}", f"Fit **{score.score}/10**{level}{by}", score.why,
                 " | ".join([f"[Apply]({job.url})"] + [f"[{l}]({u})" for l, u in links])]
        lines += [f"`{n}`" for n in notes]
    else:
        lines = [f"🎯 <b>{_e(job.title)}</b>", f"<b>{_e(job.company)}</b> · {_e(where)}",
                 f"Fit <b>{score.score}/10</b>{_e(level)}{_e(by)}", _e(score.why),
                 " | ".join([f'<a href="{_e(job.url)}">Apply</a>'] + [f'<a href="{_e(u)}">{_e(l)}</a>' for l, u in links])]
        lines += [f"<code>{_e(n)}</code>" for n in notes]
    if resume_coming:
        lines.append("📄 Resume on the way.")
    return "\n".join(x for x in lines if x)


class Notifier:
    def __init__(self, email: Email | None = None, telegram: Telegram | None = None, discord: Discord | None = None,
                 dry: bool = False, log=print):
        self.email, self.telegram, self.discord, self.dry, self.log = email, telegram, discord, dry, log
        self.sent = []  # (kind, subject) for tests and logs

    @property
    def enabled(self) -> bool:
        return bool(self.email or self.telegram or self.discord)

    def _try(self, label: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # one failed channel never stops the run
            self.log(f"  {label} failed: {exc}")
            return None

    def job(self, job, score, links, notes, labels, resume_coming: bool, paste: str) -> dict:
        subject, html_body, text_body = email_job(job, score, links, notes, labels, resume_coming, paste)
        self.sent.append(("job", subject))
        if self.dry or not self.enabled:
            self.log("\n[ping] " + subject + "\n" + clip(text_body, 1500))
            return {"subject": subject}
        refs = {"subject": subject}
        if self.email:
            refs["email"] = self._try("Email", self.email.send, subject, html_body, text_body)
        if self.telegram:
            self._try("Telegram", self.telegram.send_text, chat_job(job, score, links, notes, labels, resume_coming))
            time.sleep(0.4)
        if self.discord:
            self._try("Discord", self.discord.send_text, chat_job(job, score, links, notes, labels, resume_coming, True))
        return refs

    def text(self, subject: str, html_body: str, text_body: str, attachments=()) -> None:
        self.sent.append(("text", subject))
        if self.dry or not self.enabled:
            self.log(f"\n[message] {subject}\n{clip(text_body, 1500)}" + (f"\n[attached] {list(map(str, attachments))}" if attachments else ""))
            return
        if self.email:
            self._try("Email", self.email.send, subject, html_body, text_body, attachments)
        for chan in (self.telegram, self.discord):
            if chan:
                self._try("Chat", chan.send_text, f"{subject}\n\n{clip(text_body, 1800)}", **({"parse_html": False} if chan is self.telegram else {}))
                for path in attachments:
                    self._try("Chat file", chan.send_file, Path(path), subject)

    def files(self, paths, subject: str, html_body: str, text_body: str, refs: dict | None = None) -> None:
        refs = refs or {}
        self.sent.append(("files", subject))
        if self.dry or not self.enabled:
            self.log(f"[files] {subject}: " + ", ".join(str(p) for p in paths))
            return
        if self.email:
            thread_subject = f"Re: {refs['subject']}" if refs.get("subject") else subject
            self._try("Email", self.email.send, thread_subject, html_body, text_body, paths, refs.get("email"))
        for chan in (self.telegram, self.discord):
            if chan:
                for path in paths:
                    self._try("Chat file", chan.send_file, Path(path), subject)
