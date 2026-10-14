from datetime import timedelta
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.core.clock import utcnow
from app.models import Account
from app.services.accounts.oauth import ABOUT_URL, fetch_about
from app.services.accounts.photos import PHOTO_MAX, allowed_photo_url, photo_path, save_photo
from app.services.google.quota import cost
from app.services.rclone.runner import RunResult

URL = "https://lh3.googleusercontent.com/a/photo=s96"


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, "PNG")
    return buf.getvalue()


def _factory(body: bytes = b"", status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, content=body)

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_about_asks_for_photo():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["fields"] = request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "user": {"emailAddress": "a@example.com", "photoLink": URL},
                "storageQuota": {"limit": "10", "usage": "1"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        about = await fetch_about(http, "tok")
    assert str(httpx.URL(ABOUT_URL)).startswith("https://")
    assert seen["fields"] == "user(emailAddress,photoLink),storageQuota"
    assert about["photo_link"] == URL


async def test_save_photo_ok(db, make_account, layout, session_factory):
    acc = make_account()
    ok = await save_photo(layout, session_factory, acc.id, acc.email, URL, _factory(_png()), utcnow)
    assert ok is True
    assert photo_path(layout, acc.email).read_bytes() == _png()
    db.expire_all()
    assert db.get(Account, acc.id).photo_type == "image/png"
    assert db.get(Account, acc.id).photo_updated_at is not None


@pytest.mark.parametrize(
    "url,body",
    [
        ("https://evil.example/x.png", _png()),
        ("http://lh3.googleusercontent.com/x", _png()),
        ("https://googleusercontent.com/x", _png()),
        (URL, b"x" * (300 * 1024)),
        (URL, b"this is not an image"),
        (None, b""),
    ],
)
async def test_save_photo_refuses_other_hosts_and_big_or_bad_images(
    db, make_account, layout, session_factory, url, body
):
    acc = make_account()
    seen: list = []
    ok = await save_photo(
        layout, session_factory, acc.id, acc.email, url, _factory(body, seen=seen), utcnow
    )
    assert ok is False
    assert not photo_path(layout, acc.email).exists()
    db.expire_all()
    assert db.get(Account, acc.id).photo_type is None
    if url is None or not allowed_photo_url(url):
        assert seen == []  # never even asked


async def test_failed_download_keeps_old_photo(db, make_account, layout, session_factory):
    acc = make_account()
    assert await save_photo(
        layout, session_factory, acc.id, acc.email, URL, _factory(_png()), utcnow
    )
    for factory in (_factory(b"", status=500), _factory(b"junk")):
        assert not await save_photo(
            layout, session_factory, acc.id, acc.email, URL, factory, utcnow
        )
    # a redirect is not followed
    assert not await save_photo(
        layout, session_factory, acc.id, acc.email, URL, _factory(b"", status=302), utcnow
    )
    assert photo_path(layout, acc.email).read_bytes() == _png()
    db.expire_all()
    assert db.get(Account, acc.id).photo_type == "image/png"


async def test_photo_download_stops_at_the_limit():
    chunks = 0

    async def body():
        nonlocal chunks
        for _ in range(100):
            chunks += 1
            yield b"x" * 65536

    def handler(request):
        return httpx.Response(200, content=body())

    from app.services.accounts.photos import _download

    got = await _download(URL, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert got is None
    assert chunks <= PHOTO_MAX // 65536 + 3


def test_photo_endpoint(auth_client, app, make_account, layout, client):
    with_photo = make_account("p@example.com")
    without = make_account("n@example.com")
    photo_path(layout, with_photo.email).write_bytes(_png())
    with app.state.session_factory() as db:
        db.get(Account, with_photo.id).photo_type = "image/png"
        db.commit()
    r = auth_client.get(f"/api/accounts/{with_photo.id}/photo")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.content == _png()
    assert auth_client.get(f"/api/accounts/{without.id}/photo").status_code == 404
    assert auth_client.get("/api/accounts/9999/photo").status_code == 404
    flags = {a["email"]: a["has_photo"] for a in auth_client.get("/api/accounts").json()}
    assert flags == {"p@example.com": True, "n@example.com": False}
    # photo_type set but the file is gone: no photo
    photo_path(layout, with_photo.email).unlink()
    assert auth_client.get(f"/api/accounts/{with_photo.id}/photo").status_code == 404
    client.headers.pop("X-CSRF-Token", None)
    client.cookies.clear()
    assert client.get(f"/api/accounts/{with_photo.id}/photo").status_code == 401


async def test_refresh_quota_fetches_photo_once_a_day(
    app, make_account, layout, session_factory, settings
):
    from app.services.accounts.service import AccountService

    acc = make_account()
    now = [utcnow()]
    calls = []

    class FakeDrive:
        async def photo_link(self, remote):
            calls.append(remote)
            return URL

    async def runner(args, env, **kw):
        kw["on_stdout"]('{"total": 10, "used": 1, "trashed": 0}')
        return RunResult(0, 0.0, [])

    svc = AccountService(
        settings=settings,
        layout=layout,
        session_factory=session_factory,
        bus=app.state.bus,
        rclone_env=app.state.rclone_env,
        runner=runner,
        clock=lambda: now[0],
    )
    svc.photo_http_factory = _factory(_png())
    await svc.refresh_quota(acc.id)  # no drive handle: skipped
    assert calls == []
    svc.drive_api = FakeDrive()
    await svc.refresh_quota(acc.id)
    await svc.refresh_quota(acc.id)
    assert len(calls) == 1
    assert photo_path(layout, acc.email).exists()
    now[0] += timedelta(days=1, minutes=1)
    await svc.refresh_quota(acc.id)
    assert len(calls) == 2


async def test_drive_api_photo_link():
    from app.services.drive_api.client import DriveApi

    class Tokens:
        async def access_token(self, remote):
            return "tok"

        def forget(self, remote):
            pass

    answers = [httpx.Response(200, json={"user": {"photoLink": URL}}), httpx.Response(500)]
    seen = []

    def handler(request):
        seen.append((request.url.params["fields"], request.headers["authorization"]))
        return answers.pop(0)

    api = DriveApi(Tokens(), lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ops = []
    real_send = api._send

    async def counted(remote, op, *a, **kw):
        ops.append(op)
        return await real_send(remote, op, *a, **kw)

    api._send = counted
    assert await api.photo_link("gd_a") == URL
    assert seen == [("user(photoLink)", "Bearer tok")]
    assert ops == ["drive.about.get"]  # counted under its own name, at 1 unit
    assert cost("drive.about.get") == 1
    assert await api.photo_link("gd_a") is None
