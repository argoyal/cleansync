import httpx
import pytest

from app.services.unsubscribe.web import RefusedAddress, SafeWeb, WebTrouble, is_public_ip


def resolver(table):
    async def resolve(host):
        return table[host]

    return resolve


def test_is_public_ip():
    for ip in (
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.1.1",
        "100.100.1.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "0.0.0.0",
        "::ffff:10.0.0.1",
        "224.0.0.1",
    ):
        assert not is_public_ip(ip), ip
    assert is_public_ip("93.184.216.34") and is_public_ip("2606:2800:220:1::1")


async def test_post_goes_to_checked_ip_with_host_header():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["host"], seen["body"] = str(req.url), req.headers["host"], req.content
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"]}), transport=httpx.MockTransport(handler)
    )
    ans = await web.request("POST", "http://x.com/u?t=1", form={"List-Unsubscribe": "One-Click"})
    assert ans.status == 200
    assert seen["url"].startswith("http://93.184.216.34/u?t=1")
    assert seen["host"] == "x.com"
    assert seen["body"] == b"List-Unsubscribe=One-Click"


async def test_https_passes_sni_hostname_and_brackets_ipv6():
    seen = {}

    def handler(req: httpx.Request):
        seen["sni"] = req.extensions.get("sni_hostname")
        seen["url"] = str(req.url)
        return httpx.Response(200, text="ok")

    web = SafeWeb(
        resolve=resolver({"x.com": ["2606:2800:220:1::1"]}),
        transport=httpx.MockTransport(handler),
    )
    await web.request("GET", "https://x.com/u")
    assert seen["sni"] == "x.com"
    assert seen["url"].startswith("https://[2606:2800:220:1::1]/u")


async def test_mixed_dns_is_refused():
    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34", "10.0.0.5"]}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    with pytest.raises(RefusedAddress):
        await web.request("GET", "https://x.com/u")


async def test_redirect_to_private_is_refused():
    def handler(req):
        return httpx.Response(302, headers={"location": "http://inside.lan/x"})

    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"], "inside.lan": ["192.168.0.2"]}),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RefusedAddress):
        await web.request("GET", "http://x.com/u")


async def test_too_many_redirects_and_ports_and_schemes():
    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"]}),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"location": "/again"})
        ),
    )
    with pytest.raises(WebTrouble, match="round in circles"):
        await web.request("GET", "http://x.com/u")
    with pytest.raises(RefusedAddress):
        await web.request("GET", "http://x.com:8080/u")
    with pytest.raises(RefusedAddress):
        await web.request("GET", "file:///etc/passwd")


async def test_body_is_capped_and_303_turns_post_into_get():
    calls = []

    def handler(req):
        calls.append(req.method)
        if len(calls) == 1:
            return httpx.Response(303, headers={"location": "/done"})
        return httpx.Response(200, text="a" * 600_000, headers={"content-type": "text/html"})

    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"]}), transport=httpx.MockTransport(handler)
    )
    ans = await web.request("POST", "http://x.com/u", form={"a": "b"})
    assert calls == ["POST", "GET"] and len(ans.text) <= 256 * 1024
    assert ans.url == "http://x.com/done"


async def test_messages_never_hold_the_url():
    def handler(req):
        raise httpx.ConnectError("boom")

    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"]}), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(WebTrouble) as e:
        await web.request("GET", "https://x.com/u?token=SECRET")
    assert "SECRET" not in str(e.value) and "x.com" in str(e.value)


async def test_allow_private_for_tests_only():
    web = SafeWeb(
        resolve=resolver({"localhost": ["127.0.0.1"]}),
        allow_private=True,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert (await web.request("GET", "http://localhost/u")).status == 200


async def test_allow_private_also_allows_other_ports_but_the_default_does_not():
    # The browser test's pretend unsubscribe site listens on 8766: only the test switch allows it.
    answers = {"localhost": ["127.0.0.1"], "127.0.0.1": ["127.0.0.1"]}
    ok = httpx.MockTransport(lambda r: httpx.Response(200))
    web = SafeWeb(resolve=resolver(answers), allow_private=True, transport=ok)
    assert (await web.request("POST", "http://127.0.0.1:8766/one-click")).status == 200
    strict = SafeWeb(resolve=resolver(answers), transport=ok)
    with pytest.raises(RefusedAddress):
        await strict.request("POST", "http://localhost:8766/one-click")


async def test_no_addresses_and_timeout():
    web = SafeWeb(resolve=resolver({"x.com": []}))
    with pytest.raises(WebTrouble, match="couldn't be found"):
        await web.request("GET", "http://x.com/u")

    import asyncio

    async def slow(host):
        await asyncio.sleep(5)

    web = SafeWeb(resolve=slow, timeout_s=0.05)
    with pytest.raises(WebTrouble, match="didn't answer in time"):
        await web.request("GET", "http://x.com/u")


async def test_idn_host_is_punycoded_for_header_and_sni():
    seen = {}

    def handler(req):
        seen["host"] = req.headers["host"]
        seen["sni"] = req.extensions.get("sni_hostname")
        seen["enc"] = req.headers["accept-encoding"]
        return httpx.Response(200, text="ok")

    web = SafeWeb(
        resolve=resolver({"xn--bcher-kva.example": ["93.184.216.34"]}),
        transport=httpx.MockTransport(handler),
    )
    await web.request("GET", "https://bücher.example/u")
    assert seen["host"] == "xn--bcher-kva.example"
    assert seen["sni"] == "xn--bcher-kva.example"
    assert seen["enc"] == "identity"


async def test_307_keeps_the_post_body():
    seen = []

    def handler(req):
        seen.append((req.method, req.content))
        if len(seen) == 1:
            return httpx.Response(307, headers={"location": "/next"})
        return httpx.Response(200, text="ok")

    web = SafeWeb(
        resolve=resolver({"x.com": ["93.184.216.34"]}), transport=httpx.MockTransport(handler)
    )
    await web.request("POST", "http://x.com/u", form={"a": "b"})
    assert seen == [("POST", b"a=b"), ("POST", b"a=b")]
