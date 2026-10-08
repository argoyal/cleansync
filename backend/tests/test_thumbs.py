import asyncio
import os
import shutil
import sys
import time

import pytest
from PIL import Image

from app.services.files import thumbs as t
from app.services.files.thumbs import ThumbError, Thumbnails, prune_thumbs


def _png(path, size=(800, 400), color=(30, 120, 200)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


async def test_image_preview_is_small_webp_and_cached(tmp_path):
    src = _png(tmp_path / "files" / "sky.png")
    th = Thumbnails(tmp_path / "thumbs")
    out = await th.get(src)
    with Image.open(out) as im:
        assert im.format == "WEBP" and max(im.size) == 256
    first_mtime = out.stat().st_mtime_ns
    assert await th.get(src) == out
    assert out.stat().st_mtime_ns >= first_mtime


async def test_changed_file_gets_a_new_preview(tmp_path):
    src = _png(tmp_path / "a.png")
    th = Thumbnails(tmp_path / "thumbs")
    first = await th.get(src)
    _png(src, color=(200, 0, 0))
    os.utime(src, (time.time() + 5, time.time() + 5))
    assert await th.get(src) != first


async def test_huge_images_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "MAX_PIXELS", 10_000)
    src = _png(tmp_path / "big.png", size=(200, 200))
    with pytest.raises(ThumbError):
        await Thumbnails(tmp_path / "thumbs").get(src)


async def test_broken_and_unsupported_files(tmp_path):
    th = Thumbnails(tmp_path / "thumbs")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    with pytest.raises(ThumbError):
        await th.get(broken)
    doc = tmp_path / "a.docx"
    doc.write_bytes(b"x")
    with pytest.raises(ThumbError):
        await th.get(doc)


def _fake_pdftoppm(tmp_path, body):
    script = tmp_path / "fake-pdftoppm"
    script.write_text(f"#!{sys.executable}\nimport sys, time\nargs = sys.argv[1:]\n{body}\n")
    script.chmod(0o755)
    return str(script)


async def test_pdf_first_page_is_rendered_with_pdftoppm(tmp_path):
    # Stands in for pdftoppm: writes "<prefix>.png" like the real one does with -singlefile.
    fake = _fake_pdftoppm(
        tmp_path,
        "from PIL import Image\n"
        "assert args[:7] == ['-f', '1', '-l', '1', '-singlefile', '-scale-to', '256']\n"
        "assert args[7] == '-png'\n"
        "Image.new('RGB', (256, 330), 'white').save(args[-1] + '.png')",
    )
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = await Thumbnails(tmp_path / "thumbs", pdftoppm=fake).get(pdf)
    with Image.open(out) as im:
        assert im.format == "WEBP"


async def test_slow_pdf_is_given_up(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "PDF_TIMEOUT_S", 1.5)
    pidfile = tmp_path / "pid"
    fake = _fake_pdftoppm(
        tmp_path, f"import os\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)"
    )
    pdf = tmp_path / "slow.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ThumbError, match="too long"):
        await Thumbnails(tmp_path / "thumbs", pdftoppm=fake).get(pdf)
    assert _is_dead(int(pidfile.read_text()))


def _is_dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


async def test_cancelled_pdf_preview_kills_pdftoppm(tmp_path):
    pidfile = tmp_path / "pid"
    fake = _fake_pdftoppm(
        tmp_path, f"import os\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)"
    )
    pdf = tmp_path / "slow.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    task = asyncio.create_task(Thumbnails(tmp_path / "thumbs", pdftoppm=fake).get(pdf))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _is_dead(int(pidfile.read_text()))


async def test_failing_pdftoppm_is_an_error(tmp_path):
    fake = _fake_pdftoppm(tmp_path, "sys.exit(1)")
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ThumbError, match="could not preview"):
        await Thumbnails(tmp_path / "thumbs", pdftoppm=fake).get(pdf)


async def test_renamed_eps_is_never_decoded(tmp_path):
    eps = tmp_path / "x.jpg"
    eps.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n")
    with pytest.raises(ThumbError):
        await Thumbnails(tmp_path / "thumbs").get(eps)


async def test_failed_file_is_not_retried_for_a_while(tmp_path, monkeypatch):
    calls = []
    real = t.image_thumb
    monkeypatch.setattr(t, "image_thumb", lambda s, o: (calls.append(1), real(s, o))[1])
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    th = Thumbnails(tmp_path / "thumbs")
    for _ in range(2):
        with pytest.raises(ThumbError):
            await th.get(broken)
    assert len(calls) == 1


async def test_busy_server_says_try_again(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "QUEUE_WAIT_S", 0.1)
    th = Thumbnails(tmp_path / "thumbs", max_parallel=1)
    await th._semaphore().acquire()
    with pytest.raises(t.ThumbBusy):
        await th.get(_png(tmp_path / "a.png"))


def test_prune_removes_stale_temp_files(tmp_path):
    old = time.time() - 7200
    stale_file = tmp_path / "ab" / ".x.webp.1234.tmp"
    stale_file.parent.mkdir()
    stale_file.write_bytes(b"x")
    stale_dir = tmp_path / "tmpabc"
    stale_dir.mkdir()
    (stale_dir / "page.png").write_bytes(b"x")
    fresh = tmp_path / "tmpfresh"
    fresh.mkdir()
    for p in (stale_file, stale_dir):
        os.utime(p, (old, old))
    prune_thumbs(tmp_path)
    assert not stale_file.exists() and not stale_dir.exists() and fresh.exists()


async def test_missing_pdftoppm_is_explained(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ThumbError, match="poppler"):
        await Thumbnails(tmp_path / "thumbs", pdftoppm=str(tmp_path / "missing")).get(pdf)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler-utils not installed")
async def test_real_pdftoppm(tmp_path):
    pdf = tmp_path / "real.pdf"
    Image.new("RGB", (600, 800), "white").save(pdf)  # Pillow writes a one-page PDF
    out = await Thumbnails(tmp_path / "thumbs").get(pdf)
    with Image.open(out) as im:
        assert max(im.size) <= 256


def test_prune_keeps_the_most_recently_used(tmp_path):
    for i in range(10):
        p = tmp_path / f"{i:02d}.webp"
        p.write_bytes(b"x" * 100)
        os.utime(p, (1000 + i, 1000 + i))
    assert prune_thumbs(tmp_path, max_bytes=2000) == 0
    assert prune_thumbs(tmp_path, max_bytes=500, keep_bytes=300) == 7
    assert sorted(p.name for p in tmp_path.iterdir()) == ["07.webp", "08.webp", "09.webp"]


def test_thumb_api(auth_client, app, make_account):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    _png(root / "Photos" / "sky.png")
    (root / "notes.txt").write_text("x")
    r = auth_client.get(f"/api/thumbs/{acc.id}/Photos/sky.png", params={"v": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "max-age" in r.headers["cache-control"]
    assert auth_client.get(f"/api/thumbs/{acc.id}/notes.txt").status_code == 404
    assert auth_client.get(f"/api/thumbs/{acc.id}/missing.png").status_code == 404
    assert auth_client.get(f"/api/thumbs/{acc.id}/..%2F..%2Fx.png").status_code == 422


def test_thumb_refuses_symlink_into_hidden_folder(auth_client, app, make_account):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    _png(root / ".google-exports" / "secret.png")
    (root / "Photos").mkdir(parents=True, exist_ok=True)
    (root / "Photos" / "link.png").symlink_to(root / ".google-exports" / "secret.png")
    assert auth_client.get(f"/api/thumbs/{acc.id}/Photos/link.png").status_code == 404
    assert auth_client.get(f"/api/thumbs/{acc.id}/.GOOGLE-EXPORTS/secret.png").status_code == 404


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def disk_calls_on_loop(monkeypatch, tmp_path):
    """Records disk checks on files under tmp_path made on the event loop thread."""
    import pathlib

    seen: list[str] = []
    real_stat, real_utime = pathlib.Path.stat, os.utime

    def stat(self, *a, **kw):
        if _on_loop() and str(self).startswith(str(tmp_path)):
            seen.append(f"stat {self.name}")
        return real_stat(self, *a, **kw)

    def utime(path, *a, **kw):
        if _on_loop() and str(path).startswith(str(tmp_path)):
            seen.append(f"utime {os.path.basename(path)}")
        return real_utime(path, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "stat", stat)
    monkeypatch.setattr(t.os, "utime", utime)
    return seen


async def test_cached_preview_lookups_stay_off_the_event_loop(tmp_path, disk_calls_on_loop):
    src = _png(tmp_path / "a.png")
    th = Thumbnails(tmp_path / "thumbs")
    disk_calls_on_loop.clear()  # the set-up above ran on the loop too
    await th.get(src)
    await th.get(src)  # the cached path: stat, exists and utime
    assert disk_calls_on_loop == []


def test_thumbnail_route_checks_the_disk_off_the_event_loop(
    auth_client, app, make_account, disk_calls_on_loop, settings
):
    acc = make_account()
    root = app.state.layout.account_dir(acc.email)
    _png(root / "a.png")
    assert str(root).startswith(str(settings.drivesync_root))
    r = auth_client.get(f"/api/thumbs/{acc.id}/a.png")
    assert r.status_code == 200, r.text
    assert disk_calls_on_loop == []
