"""Find a plain unsubscribe form on a page that can be sent without a browser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlsplit

from app.services.mail.store import html_to_text
from app.services.unsubscribe.links import HTML_CAP

_CAPTCHA = ("g-recaptcha", "h-captcha", "cf-turnstile", "captcha")
_PAGE_TEXT_CAP = 4000
_TICK_WORDS = {"unsubscribe", "unsub", "all"}
_NO_TICK_WORDS = {"allow", "subscribe", "marketing", "offers", "partners"}
_BUTTONS = {"submit", "button", "reset", "image"}

# Linear scans (no HTMLParser, which can be made very slow by unclosed quotes).
_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^<>]*)>")
_ATTR = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")


@dataclass
class FormToSend:
    method: str
    action: str
    fields: list[tuple[str, str]]


def _attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR.finditer(raw):
        value = next((g for g in m.groups()[1:] if g is not None), "")
        out.setdefault(m.group(1).lower(), unescape(value))
    return out


def _ticks(name: str, value: str) -> bool:
    tokens = set(re.split(r"[^a-z]+", f"{name} {value}".lower())) - {""}
    return bool(tokens & _TICK_WORDS) and not tokens & _NO_TICK_WORDS


class _Parsed:
    def __init__(self, html: str) -> None:
        self.forms = 0
        self.action = ""
        self.method = "POST"
        self.blocked = False
        self.fields: list[tuple[str, str]] = []
        self._scan(html[:HTML_CAP])

    def _scan(self, html: str) -> None:
        tags = list(_TAG.finditer(html))
        in_form = False
        submit_taken = False
        select: str | None = None
        select_value: str | None = None
        select_chosen = False
        for i, m in enumerate(tags):
            closing, tag = m.group(1), m.group(2).lower()
            if tag == "form":
                if closing:
                    in_form = in_form and self.forms != 1
                    continue
                self.forms += 1
                if self.forms == 1:
                    in_form = True
                    a = _attrs(m.group(3))
                    self.action = a.get("action", "").strip()
                    self.method = "GET" if a.get("method", "").strip().lower() == "get" else "POST"
                continue
            if not in_form or self.forms != 1:
                continue
            if tag == "select":
                if closing:
                    if select is not None and select_value is not None:
                        self.fields.append((select, select_value))
                    select = None
                else:
                    select = _attrs(m.group(3)).get("name") or None
                    select_value, select_chosen = None, False
                continue
            if closing:
                continue
            a = _attrs(m.group(3))
            if tag == "option" and select is not None:
                value = a.get("value", "")
                if select_value is None:
                    select_value = value
                if "selected" in a and not select_chosen:
                    select_value, select_chosen = value, True
            elif tag == "textarea":
                name = a.get("name")
                if name:
                    stop = tags[i + 1].start() if i + 1 < len(tags) else len(html)
                    self.fields.append((name, unescape(html[m.end() : stop])))
            elif tag == "input":
                kind = a.get("type", "text").lower()
                name, value = a.get("name", ""), a.get("value", "")
                if kind in ("password", "file"):
                    self.blocked = True
                elif kind in ("checkbox", "radio"):
                    if name and ("checked" in a or _ticks(name, value)):
                        self.fields.append((name, value or "on"))
                elif kind == "submit":
                    submit_taken = self._submit(submit_taken, name, value)
                elif kind not in _BUTTONS:
                    if name:
                        self.fields.append((name, value))
            elif tag == "button" and a.get("type", "submit").lower() == "submit":
                submit_taken = self._submit(submit_taken, a.get("name", ""), a.get("value", ""))

    def _submit(self, taken: bool, name: str, value: str) -> bool:
        if not taken and name:
            self.fields.append((name, value))
        return True


def has_form(html: str) -> bool:
    return _Parsed(html).forms > 0


def simple_form(html: str, base_url: str) -> FormToSend | None:
    if any(word in html[:HTML_CAP].lower() for word in _CAPTCHA):
        return None
    parsed = _Parsed(html)
    if parsed.forms != 1 or parsed.blocked:
        return None
    try:
        action = urljoin(base_url, parsed.action) if parsed.action else base_url
        if urlsplit(action).scheme.lower() not in ("http", "https"):
            return None
    except ValueError:
        return None
    return FormToSend(method=parsed.method, action=action, fields=parsed.fields)


def page_text(html: str) -> str:
    return html_to_text(html[:HTML_CAP])[:_PAGE_TEXT_CAP]
