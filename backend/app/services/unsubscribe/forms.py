"""Find a plain unsubscribe form on a page that can be sent without a browser."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.services.mail.store import html_to_text

_CAPTCHA = ("g-recaptcha", "h-captcha", "cf-turnstile", "captcha")
_PAGE_TEXT_CAP = 4000
_HTML_CAP = 1024 * 1024


@dataclass
class FormToSend:
    method: str
    action: str
    fields: list[tuple[str, str]]


class _Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms = 0
        self.action = ""
        self.method = "POST"
        self.blocked = False
        self.fields: list[tuple[str, str]] = []
        self._in_form = False
        self._submit_taken = False
        self._select: str | None = None
        self._select_value: str | None = None
        self._select_chosen = False
        self._textarea: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self.forms += 1
            if self.forms == 1:
                self._in_form = True
                self.action = a.get("action", "").strip()
                self.method = "GET" if a.get("method", "").strip().lower() == "get" else "POST"
            return
        if not self._in_form or self.forms != 1:
            return
        if tag == "input":
            self._input(a)
        elif tag == "select":
            self._select = a.get("name") or None
            self._select_value = None
            self._select_chosen = False
        elif tag == "option" and self._select is not None:
            value = a.get("value", "")
            if self._select_value is None:
                self._select_value = value
            if "selected" in a and not self._select_chosen:
                self._select_value = value
                self._select_chosen = True
        elif tag == "textarea":
            self._textarea = a.get("name") or None
            self._text = []
        elif tag == "button":
            self._button(a)

    def _input(self, a: dict[str, str]) -> None:
        kind = a.get("type", "text").lower()
        name = a.get("name", "")
        value = a.get("value", "")
        if kind in ("password", "file"):
            self.blocked = True
        elif kind in ("hidden", "text", "email"):
            if name:
                self.fields.append((name, value))
        elif kind in ("checkbox", "radio"):
            if name and (
                "checked" in a
                or "unsubscribe" in value.lower()
                or "all" in value.lower()
                or "unsubscribe" in name.lower()
                or "all" in name.lower()
            ):
                self.fields.append((name, value or "on"))
        elif kind == "submit":
            self._take_submit(name, value)

    def _button(self, a: dict[str, str]) -> None:
        if a.get("type", "submit").lower() == "submit":
            self._take_submit(a.get("name", ""), a.get("value", ""))

    def _take_submit(self, name: str, value: str) -> None:
        if self._submit_taken:
            return
        self._submit_taken = True
        if name:
            self.fields.append((name, value))

    def handle_data(self, data):
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "form" and self.forms == 1:
            self._in_form = False
        elif tag == "select" and self._select is not None:
            if self._select_value is not None:
                self.fields.append((self._select, self._select_value))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            self.fields.append((self._textarea, "".join(self._text)))
            self._textarea = None


def _parse(html: str) -> _Forms:
    parser = _Forms()
    parser.feed(html[:_HTML_CAP])
    parser.close()
    return parser


def has_form(html: str) -> bool:
    return _parse(html).forms > 0


def simple_form(html: str, base_url: str) -> FormToSend | None:
    lowered = html[:_HTML_CAP].lower()
    if any(word in lowered for word in _CAPTCHA):
        return None
    parsed = _parse(html)
    if parsed.forms != 1 or parsed.blocked:
        return None
    action = urljoin(base_url, parsed.action) if parsed.action else base_url
    if urlsplit(action).scheme.lower() not in ("http", "https"):
        return None
    return FormToSend(method=parsed.method, action=action, fields=parsed.fields)


def page_text(html: str) -> str:
    return html_to_text(html)[:_PAGE_TEXT_CAP]
