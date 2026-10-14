from app.services.mail.store import parse_eml, sender_of

RAW = (
    b"From: Shop News <News@Shop.Example.com>\r\n"
    b"To: me@example.com\r\nSubject: Sale\r\nMessage-ID: <a@b>\r\n"
    b"List-Unsubscribe: <mailto:u@shop.example.com?subject=unsub>,\r\n"
    b" <https://shop.example.com/u?t=abc>\r\n"
    b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nHello\r\n"
)


def test_parse_keeps_unsubscribe_headers_and_sender():
    p = parse_eml(RAW)
    assert p.list_unsubscribe == (
        "<mailto:u@shop.example.com?subject=unsub>, <https://shop.example.com/u?t=abc>"
    )
    assert p.list_unsubscribe_post == "List-Unsubscribe=One-Click"
    assert p.sender == "news@shop.example.com"


def test_no_header_and_odd_from():
    p = parse_eml(b"From: not an address\r\nSubject: x\r\n\r\nbody")
    assert p.list_unsubscribe is None and p.list_unsubscribe_post is None
    assert sender_of("not an address") is None
    assert sender_of("") is None
    assert sender_of('"A, B" <A@B.COM>') == "a@b.com"


def test_header_is_capped():
    raw = b"From: a@b.com\r\nList-Unsubscribe: <https://x.example/" + b"a" * 5000 + b">\r\n\r\n"
    assert len(parse_eml(raw).list_unsubscribe) == 2000
