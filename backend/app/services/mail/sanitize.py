"""Allow-list cleaning of HTML mail on the server (spec §6.2), with nh3 (Rust's ammonia).

Whatever is not on these lists is dropped: scripts (with their content), on… handlers,
frames, forms, plugins, external style sheets. Links keep only http, https and mailto.
Remote images are kept but counted: the mail frame's own policy blocks them until the
person chooses "Show images". Inline cid: images become data: addresses from the mail."""

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


@dataclass
class Cleaned:
    html: str
    remote_images: int


def sanitize_html(html: str, cid_images: dict[str, str]) -> Cleaned:
    remote = 0

    def keep(tag: str, attr: str, value: str) -> str | None:
        nonlocal remote
        v = value.strip()
        low = v.lower()
        if tag == "img" and attr == "src":
            if low.startswith("cid:"):
                return cid_images.get(v[4:].strip().strip("<>"))  # None drops it
            if low.startswith(("http://", "https://")):
                remote += 1
                return v
            if low.startswith("data:image/"):
                return v
            return None
        if attr == "href":
            return v if low.startswith(LINK_SCHEMES) else None
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
    return Cleaned(clean, remote)
