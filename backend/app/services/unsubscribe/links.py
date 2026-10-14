"""Pull unsubscribe links out of a List-Unsubscribe header or a message body."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urlsplit

# One cap for every HTML scan here; keeps hostile pages cheap.
HTML_CAP = 64 * 1024
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


_A_TAG = re.compile(r"<a\s([^<>]*+)>", re.IGNORECASE)
_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^<>]*>")


def body_unsubscribe_link(html: str) -> str | None:
    # A plain regex scan, linear in the input: HTMLParser can be made very slow by hostile markup.
    html = html[:HTML_CAP]
    for tag in _A_TAG.finditer(html):
        m = _HREF.search(tag.group(1))
        if not m:
            continue
        href = unescape(next(g for g in m.groups() if g is not None)).strip()
        try:
            scheme = urlsplit(href).scheme.lower()
        except ValueError:
            continue
        if scheme not in ("http", "https"):
            continue
        end = html.find("</a", tag.end(), tag.end() + 1000)
        raw = html[tag.end() : end if end != -1 else tag.end() + 200]
        text = " ".join(unescape(_ANY_TAG.sub(" ", raw)).lower().split())
        lowered_href = href.lower()
        if any(h in text for h in _TEXT_HINTS) or any(h in lowered_href for h in _HREF_HINTS):
            return href
    return None


def host_of(url: str) -> str:
    """Only the host, lower-cased: safe for logs, since the rest of a link may hold a token."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
