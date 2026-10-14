from app.services.unsubscribe.links import body_unsubscribe_link, host_of, parse_list_unsubscribe


def test_header_order_and_kinds():
    links = parse_list_unsubscribe(
        "<mailto:u@x.com?subject=unsub>, <http://x.com/u>, <https://x.com/u?t=1>"
    )
    assert links.web == ["https://x.com/u?t=1", "http://x.com/u"]
    assert links.mailto == ["mailto:u@x.com?subject=unsub"]


def test_folded_and_bare_and_junk():
    assert parse_list_unsubscribe("<https://x.com/a\r\n b>").web == ["https://x.com/ab"]
    assert parse_list_unsubscribe("https://x.com/u").web == ["https://x.com/u"]
    assert parse_list_unsubscribe("<javascript:alert(1)>, <ftp://x>, junk").web == []
    assert parse_list_unsubscribe(None).web == [] and parse_list_unsubscribe("").mailto == []


def test_body_link():
    html = '<p><a href="https://x.com/home">Home</a> <a href="https://x.com/p?id=1">Unsubscribe here</a></p>'
    assert body_unsubscribe_link(html) == "https://x.com/p?id=1"
    assert (
        body_unsubscribe_link('<a href="https://list-manage.com/z">click</a>')
        == "https://list-manage.com/z"
    )
    assert body_unsubscribe_link('<a href="mailto:x@y">unsubscribe</a>') is None
    assert body_unsubscribe_link("no links") is None


def test_host_only():
    assert host_of("https://Shop.Example.com/u?token=SECRET") == "shop.example.com"


def test_hostile_input_is_fast_and_bad_urls_are_skipped():
    import time

    hostile = "<a b='" * 200_000
    start = time.monotonic()
    assert body_unsubscribe_link(hostile) is None
    assert parse_list_unsubscribe("<" * 1_000_000) is not None
    assert host_of("http://[x/unsub") == ""
    assert time.monotonic() - start < 1
    assert body_unsubscribe_link('<a href="http://[x/unsub">unsubscribe</a>') is None
    assert (
        body_unsubscribe_link(
            '<a href="http://[x/unsub">unsubscribe</a><a href="https://ok.com/u">Unsubscribe</a>'
        )
        == "https://ok.com/u"
    )
    assert parse_list_unsubscribe("<http://[x/unsub>").web == ["http://[x/unsub"]


def test_long_unclosed_tag_name_is_fast():
    import time

    start = time.monotonic()
    body_unsubscribe_link("<" + "a" * 65_000)
    body_unsubscribe_link("<a " + "a" * 65_000)
    assert time.monotonic() - start < 1
