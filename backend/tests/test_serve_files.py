import io
import zipfile

import pytest

from app.services.files.mimes import serve_policy
from app.services.files.zipstream import CHUNK, zip_folder


@pytest.fixture
def acc_root(auth_client, app, make_account):
    acc = make_account()
    return acc, app.state.layout.account_dir(acc.email)


def _put(root, rel, data=b"hello"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_image_opens_inline_in_a_sandbox(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "Photos/sky.png", b"\x89PNG")
    r = auth_client.get(f"/files/{acc.id}/Photos/sky.png")
    assert r.status_code == 200 and r.content == b"\x89PNG"
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["content-security-policy"].startswith("sandbox;")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_forces_attachment_with_unicode_name(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "Résumé 2026.pdf", b"%PDF")
    r = auth_client.get(f"/files/{acc.id}/R%C3%A9sum%C3%A9%202026.pdf", params={"download": 1})
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%202026.pdf" in cd


def test_pdf_is_not_sandboxed_so_chrome_can_show_it(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "a.pdf", b"%PDF-1.4")
    r = auth_client.get(f"/files/{acc.id}/a.pdf")
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert r.headers["content-disposition"].startswith("inline;")


@pytest.mark.parametrize(
    "name", ["page.html", "x.svg.html", "data.xml", "app.js", "notes.md", "t.csv"]
)
def test_script_capable_and_text_files_are_shown_as_plain_text(auth_client, acc_root, name):
    acc, root = acc_root
    _put(root, name, b"<script>alert(1)</script>")
    r = auth_client.get(f"/files/{acc.id}/{name}")
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.headers["content-security-policy"].startswith("sandbox;")


def test_office_and_unknown_files_download(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "Budget.xlsx")
    _put(root, "blob.weird-ext")
    for name in ("Budget.xlsx", "blob.weird-ext"):
        r = auth_client.get(f"/files/{acc.id}/{name}")
        assert r.headers["content-disposition"].startswith("attachment;")


def test_range_requests_allow_video_seeking(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "clip.mp4", bytes(range(256)) * 4)
    r = auth_client.get(f"/files/{acc.id}/clip.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == bytes(range(10, 20))
    assert r.headers["content-range"] == "bytes 10-19/1024"
    assert r.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize(
    "path,status",
    [
        ("..%2F..%2Fetc%2Fpasswd", 422),
        ("RCLONE_TEST", 404),
        (".google-exports/a.docx", 404),
        ("nope.txt", 404),
        ("Photos", 404),
    ],
)
def test_bad_or_hidden_paths(auth_client, acc_root, path, status):
    acc, root = acc_root
    _put(root, "RCLONE_TEST")
    _put(root, ".google-exports/a.docx")
    (root / "Photos").mkdir()
    assert auth_client.get(f"/files/{acc.id}/{path}").status_code == status


def test_another_accounts_files_stay_out_of_reach(auth_client, app, make_account):
    alice = make_account()
    bob = make_account("bob@example.com")
    _put(app.state.layout.account_dir(bob.email), "secret.txt")
    r = auth_client.get(f"/files/{alice.id}/..%2Fbob@example.com%2Fsecret.txt")
    assert r.status_code in (404, 422)


def test_files_need_login(client):
    assert client.get("/files/1/a.txt").status_code == 401


def test_policy_table():
    assert serve_policy("image/jpeg") == ("image/jpeg", True)
    assert serve_policy("video/mp4") == ("video/mp4", True)
    assert serve_policy("text/html") == ("text/plain; charset=utf-8", True)
    assert serve_policy("application/zip") == ("application/zip", False)
    assert serve_policy(None) == ("application/octet-stream", False)


def test_zip_download_streams_the_folder(auth_client, acc_root, tmp_path):
    acc, root = acc_root
    _put(root, "Work/a.txt", b"A")
    _put(root, "Work/Sub/b.txt", b"B")
    _put(root, "Work/RCLONE_TEST")
    (root / "Work" / "Empty").mkdir()
    (root / "Work" / "link").symlink_to(tmp_path)
    r = auth_client.get("/api/files/zip", params={"account_id": acc.id, "path": "Work"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="Work.zip"' in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["Work/Empty/", "Work/Sub/b.txt", "Work/a.txt"]
    assert zf.read("Work/Sub/b.txt") == b"B"
    assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())


def test_zip_of_the_account_root_is_named_after_the_account(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "a.txt")
    r = auth_client.get("/api/files/zip", params={"account_id": acc.id})
    assert "alice@example.com.zip" in r.headers["content-disposition"]
    assert (
        auth_client.get(
            "/api/files/zip", params={"account_id": acc.id, "path": "a.txt"}
        ).status_code
        == 404
    )


def test_zip_is_produced_in_small_pieces(tmp_path):
    folder = tmp_path / "Big"
    folder.mkdir()
    (folder / "big.bin").write_bytes(b"x" * (3 * CHUNK + 5))
    pieces = list(zip_folder(folder, "Big"))
    assert max(len(p) for p in pieces) <= CHUNK + 1024
    data = b"".join(pieces)
    assert zipfile.ZipFile(io.BytesIO(data)).read("Big/big.bin") == b"x" * (3 * CHUNK + 5)


def test_a_route_set_csp_survives_the_security_middleware(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "a.png", b"\x89PNG")
    r = auth_client.get(f"/files/{acc.id}/a.png")
    assert "sandbox" in r.headers["content-security-policy"]
    assert "default-src 'self'" not in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_hidden_names_are_matched_case_insensitively(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "rclone_test")
    _put(root, ".Google-Exports/a.docx")
    assert auth_client.get(f"/files/{acc.id}/rclone_test").status_code == 404
    assert auth_client.get(f"/files/{acc.id}/.Google-Exports/a.docx").status_code == 404
    assert (
        auth_client.get(
            "/api/files/zip", params={"account_id": acc.id, "path": ".google-exports"}
        ).status_code
        == 404
    )


def test_zip_skips_hidden_names_in_any_case(tmp_path):
    folder = tmp_path / "W"
    (folder / ".GOOGLE-EXPORTS").mkdir(parents=True)
    (folder / ".GOOGLE-EXPORTS" / "x").write_bytes(b"x")
    (folder / "rclone_test").write_bytes(b"x")
    (folder / "ok.txt").write_bytes(b"ok")
    names = zipfile.ZipFile(io.BytesIO(b"".join(zip_folder(folder, "W")))).namelist()
    assert names == ["W/ok.txt"]


def test_media_is_inline_without_the_sandbox_so_seeking_keeps_the_cookie(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "clip.mp4", b"x" * 100)
    _put(root, "song.mp3", b"x" * 100)
    for name in ("clip.mp4", "song.mp3"):
        r = auth_client.get(f"/files/{acc.id}/{name}")
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert r.headers["content-disposition"].startswith("inline;")
        assert r.headers["x-content-type-options"] == "nosniff"


def test_policy_for_media_svg_and_html():
    assert serve_policy("video/mp4") == ("video/mp4", True)
    assert serve_policy("audio/mpeg") == ("audio/mpeg", True)
    assert serve_policy("image/svg+xml") == ("image/svg+xml", True)
    assert serve_policy("text/html") == ("text/plain; charset=utf-8", True)


def test_svg_is_sandboxed(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "i.svg", b"<svg onload='alert(1)'/>")
    r = auth_client.get(f"/files/{acc.id}/i.svg")
    assert r.headers["content-security-policy"].startswith("sandbox;")


def test_symlink_into_a_hidden_folder_is_refused(auth_client, acc_root):
    acc, root = acc_root
    _put(root, ".google-exports/x", b"secret")
    (root / "innocent.txt").symlink_to(root / ".google-exports" / "x")
    (root / "dir").symlink_to(root / ".google-exports")
    assert auth_client.get(f"/files/{acc.id}/innocent.txt").status_code == 404
    assert auth_client.get(f"/files/{acc.id}/dir/x").status_code == 404
    assert (
        auth_client.get("/api/files/zip", params={"account_id": acc.id, "path": "dir"}).status_code
        == 404
    )


def test_content_disposition_cannot_be_used_to_inject_headers():
    from app.api.serve import content_disposition

    cd = content_disposition("attachment", 'a"b\nc\r.txt')
    assert "\n" not in cd and "\r" not in cd
    assert 'filename="a_b_c_.txt"' in cd
    assert "a%22b%0Ac%0D.txt" in cd


def test_unsatisfiable_range_is_416(auth_client, acc_root):
    acc, root = acc_root
    _put(root, "clip.mp4", b"x" * 100)
    r = auth_client.get(f"/files/{acc.id}/clip.mp4", headers={"Range": "bytes=500-600"})
    assert r.status_code == 416


def test_zip_marks_non_ascii_names_as_utf8(tmp_path):
    folder = tmp_path / "Résumé"
    folder.mkdir()
    (folder / "é.txt").write_bytes(b"x")
    zf = zipfile.ZipFile(io.BytesIO(b"".join(zip_folder(folder, "Résumé"))))
    assert all(i.flag_bits & 0x800 for i in zf.infolist())


def test_zip_stops_cleanly_when_a_read_fails(tmp_path, monkeypatch):
    folder = tmp_path / "W"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"A")
    (folder / "b.txt").write_bytes(b"B")
    import builtins

    real_open = builtins.open

    class Bad:
        def __init__(self, f):
            self.f = f

        def read(self, n):
            raise OSError("disk went away")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    def fake_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        return Bad(f) if str(path).endswith("b.txt") else f

    monkeypatch.setattr("app.services.files.zipstream.open", fake_open, raising=False)
    data = b"".join(zip_folder(folder, "W"))  # must not raise
    assert zipfile.ZipFile(io.BytesIO(data)).read("W/a.txt") == b"A"
