"""Allow-list cleaning of HTML mail on the server (spec §6.2), with nh3 (Rust's ammonia).

Whatever is not on these lists is dropped: scripts (with their content), on… handlers,
frames, forms, plugins, external style sheets. Links keep only http, https and mailto.
Remote images are kept but counted: the mail frame's own policy blocks them until the
person chooses "Show images" (images in CSS count too). Inline cid: images become data:
addresses from the mail, up to INLINE_LIMIT in all, however often the mail repeats them."""

import re
from dataclasses import dataclass

import nh3

ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "center",
    "cite",
    "code",
    "col",
    "colgroup",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "small",
    "span",
    "strike",
    "strong",
    "style",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "tt",
    "u",
    "ul",
}
GENERIC_ATTRS = {
    "align",
    "bgcolor",
    "border",
    "cellpadding",
    "cellspacing",
    "class",
    "color",
    "colspan",
    "dir",
    "height",
    "rowspan",
    "style",
    "title",
    "valign",
    "width",
}
ALLOWED_ATTRS = {
    "*": GENERIC_ATTRS,
    "a": {"href", "name"},
    "img": {"src", "alt"},
    "font": {"face", "size"},
    "ol": {"start", "type"},
    "td": {"nowrap"},
    "th": {"nowrap"},
}
URL_SCHEMES = {"http", "https", "mailto", "cid", "data"}
LINK_SCHEMES = ("http://", "https://", "mailto:")
INLINE_LIMIT = 10 * 1024 * 1024  # inline images pasted into one page, in characters
_CID_REF = re.compile(r"""cid:\s*<?([^\s"'<>)]+)""", re.IGNORECASE)
_CSS_REMOTE = re.compile(r"""url\(\s*["']?\s*https?:""", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)  # a style sheet, not an image
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)


@dataclass
class Cleaned:
    html: str
    remote_images: int


def cid_refs(html: str) -> set[str]:
    """The Content-IDs a mail's HTML points at, so only those images are read out."""
    return set(_CID_REF.findall(html))


def sanitize_html(html: str, cid_images: dict[str, str]) -> Cleaned:
    remote = 0
    inlined = 0

    def keep(tag: str, attr: str, value: str) -> str | None:
        nonlocal remote, inlined
        v = value.strip()
        low = v.lower()
        if tag == "img" and attr == "src":
            if low.startswith("cid:"):
                uri = cid_images.get(v[4:].strip().strip("<>"))
                # Every reference pastes the whole image in again: drop any that would pass
                # the limit (a smaller one may still fit), so a mail that repeats one image
                # thousands of times can't fill the memory.
                if uri is None or inlined + len(uri) > INLINE_LIMIT:
                    return None  # dropped, shown as a missing image
                inlined += len(uri)
                return uri
            if low.startswith(("http://", "https://")):
                remote += 1
                return v
            if low.startswith("data:image/"):
                return v
            return None
        if attr == "href":
            return v if low.startswith(LINK_SCHEMES) else None
        if attr == "style":
            remote += len(_CSS_REMOTE.findall(value))
        return value

    clean = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer",
        attribute_filter=keep,
        clean_content_tags={"script"},
        strip_comments=True,
    )
    for css in _STYLE_BLOCK.findall(clean):
        remote += len(_CSS_REMOTE.findall(_CSS_IMPORT.sub("", css)))
    return Cleaned(clean, remote)
