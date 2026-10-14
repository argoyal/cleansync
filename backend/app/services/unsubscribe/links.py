"""Pull unsubscribe links out of a List-Unsubscribe header or a message body."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

_BODY_CAP = 512 * 1024
_TEXT_HINTS = ("unsubscribe", "email preferences", "opt out", "opt-out", "manage subscriptions")
_HREF_HINTS = ("unsub", "opt-out", "optout", "list-manage")


@dataclass
class Links:
    web: list[str] = field(default_factory=list)  # https first, then http
    mailto: list[str] = field(default_factory=list)  # raw mailto: URLs


def parse_list_unsubscribe(value: str | None) -> Links:
    if not value:
        return Links()
    parts = re.findall(r"<([^<>]+)>", value)
    if not parts:
        parts = value.split(",")
    https: list[str] = []
    http: list[str] = []
    mailto: list[str] = []
    for part in parts:
        part = re.sub(r"\s+", "", part)
        scheme = part.split(":", 1)[0].lower() if ":" in part else ""
        if scheme == "https":
            https.append(part)
        elif scheme == "http":
            http.append(part)
        elif scheme == "mailto":
            mailto.append(part)
    return Links(web=https + http, mailto=mailto)


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._close()
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self._close()

    def _close(self) -> None:
        if self._href is not None:
            self.found.append((self._href.strip(), "".join(self._text)))
        self._href = None
        self._text = []

    def close(self) -> None:
        super().close()
        self._close()


def body_unsubscribe_link(html: str) -> str | None:
    parser = _Anchors()
    parser.feed(html[:_BODY_CAP])
    parser.close()
    for href, text in parser.found:
        if urlsplit(href).scheme.lower() not in ("http", "https"):
            continue
        lowered_text = " ".join(text.lower().split())
        lowered_href = href.lower()
        if any(h in lowered_text for h in _TEXT_HINTS) or any(
            h in lowered_href for h in _HREF_HINTS
        ):
            return href
    return None


def host_of(url: str) -> str:
    """Only the host, lower-cased: safe for logs, since the rest of a link may hold a token."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
