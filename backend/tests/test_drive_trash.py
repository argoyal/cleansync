import httpx
import pytest
import respx

from app.services.drive_api.client import FILES_URL, DriveFileState
from app.services.drive_api.tokens import DriveApiError, DriveAuthError

ACCESS = "ya29.test-access"
MD5 = "0cc175b9c0f1b6a831c399e269772661"


@pytest.fixture
def api(app):
    a = app.state.drive_api
    a.tokens._cache["gd_alice"] = (ACCESS, 1e12)
    return a


def _file(fid, trashed, size="1", md5=MD5):
    body = {"id": fid, "trashed": trashed}
    if size is not None:
        body["size"] = size
    if md5 is not None:
        body["md5Checksum"] = md5
    return httpx.Response(200, json=body)


async def test_file_state_reads_trashed_size_and_md5(api):
    with respx.mock:
        route = respx.get(f"{FILES_URL}/F1").mock(return_value=_file("F1", True, "12"))
        state = await api.file_state("gd_alice", "F1")
    assert state == DriveFileState(trashed=True, size=12, md5=MD5)
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {ACCESS}"
    fields = req.url.params["fields"].split(",")
    assert {"id", "trashed", "size", "md5Checksum"} <= set(fields)
    assert req.url.params["supportsAllDrives"] == "true"


async def test_trashed_state_reads_the_trashed_flag(api):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=_file("F1", True))
        respx.get(f"{FILES_URL}/F2").mock(return_value=_file("F2", False))
        respx.get(f"{FILES_URL}/F3").mock(return_value=httpx.Response(404, json={}))
        assert await api.trashed_state("gd_alice", "F1") is True
        assert await api.trashed_state("gd_alice", "F2") is False
        assert await api.trashed_state("gd_alice", "F3") is None


def test_matches_needs_the_same_size_and_md5():
    s = DriveFileState(trashed=True, size=1, md5=MD5)
    assert s.matches(1, MD5)
    assert s.matches(1, MD5.upper())
    assert not s.matches(2, MD5)
    assert not s.matches(1, "f" * 32)
    assert not s.matches(1, None)  # nothing to compare with is never a match
    assert not s.matches(None, MD5)
    assert not DriveFileState(trashed=True, size=None, md5=MD5).matches(1, MD5)
    assert not DriveFileState(trashed=True, size=1, md5=None).matches(1, MD5)


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"id": "F1", "trashed": "yes"}),
        httpx.Response(200, json={"id": "F1", "trashed": True, "size": "-1"}),
        httpx.Response(200, json={"id": "F1", "trashed": True, "size": "x"}),
        httpx.Response(200, json={"id": "F1", "trashed": True, "md5Checksum": 5}),
        httpx.Response(200, json=[1]),
        httpx.Response(500),
        httpx.Response(403, json={}),
        httpx.Response(200, text="x"),
    ],
)
async def test_odd_answers_fail_closed(api, resp):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=resp)
        with pytest.raises(DriveApiError):
            await api.trashed_state("gd_alice", "F1")


async def test_an_unreachable_google_is_an_error(api):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DriveApiError):
            await api.file_state("gd_alice", "F1")


async def test_a_bad_id_is_never_sent(api):
    with respx.mock:  # any request at all would fail as unmocked
        for bad in ("../x", "", "a/b", "F1?x=1", "F1\n"):
            with pytest.raises(DriveApiError):
                await api.trashed_state("gd_alice", bad)
            with pytest.raises(DriveApiError):
                await api.delete_forever("gd_alice", bad, size=1, md5=MD5)


async def test_delete_forever_only_deletes_unchanged_files_in_the_trash(api):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=_file("F1", True))
        respx.get(f"{FILES_URL}/F2").mock(return_value=_file("F2", False))
        respx.get(f"{FILES_URL}/F3").mock(return_value=_file("F3", True, size="2"))
        respx.get(f"{FILES_URL}/F4").mock(return_value=_file("F4", True, md5="f" * 32))
        respx.get(f"{FILES_URL}/F5").mock(return_value=_file("F5", True, md5=None))
        dels = {
            f: respx.delete(f"{FILES_URL}/{f}").mock(return_value=httpx.Response(204))
            for f in ("F1", "F2", "F3", "F4", "F5")
        }
        assert await api.delete_forever("gd_alice", "F1", size=1, md5=MD5) is True
        for f in ("F2", "F3", "F4", "F5"):
            assert await api.delete_forever("gd_alice", f, size=1, md5=MD5) is False, f
    assert dels["F1"].called
    assert [f for f, r in dels.items() if r.called] == ["F1"]
    assert dels["F1"].calls.last.request.url.params["supportsAllDrives"] == "true"


async def test_delete_forever_of_a_file_already_gone_is_done(api):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=httpx.Response(404, json={}))
        gone = respx.delete(f"{FILES_URL}/F1").mock(return_value=httpx.Response(204))
        assert await api.delete_forever("gd_alice", "F1", size=1, md5=MD5) is True
    assert not gone.called


@pytest.mark.parametrize("status", [200, 204, 404])
async def test_delete_forever_accepts_googles_done_answers(api, status):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=_file("F1", True))
        respx.delete(f"{FILES_URL}/F1").mock(return_value=httpx.Response(status))
        assert await api.delete_forever("gd_alice", "F1", size=1, md5=MD5) is True


async def test_delete_forever_refused_by_google_is_an_error(api):
    with respx.mock:
        respx.get(f"{FILES_URL}/F1").mock(return_value=_file("F1", True))
        respx.delete(f"{FILES_URL}/F1").mock(return_value=httpx.Response(403, json={}))
        with pytest.raises(DriveApiError):
            await api.delete_forever("gd_alice", "F1", size=1, md5=MD5)


async def test_delete_forever_needs_the_saved_size_and_md5(api):
    with pytest.raises(TypeError):
        await api.delete_forever("gd_alice", "F1")  # type: ignore[call-arg]


async def test_an_expired_token_is_renewed_once_then_reported(api, monkeypatch):
    asked: list[str] = []

    async def fresh(remote):
        asked.append(remote)
        return "ya29.again"

    monkeypatch.setattr(api.tokens, "access_token", fresh)
    with respx.mock:
        route = respx.get(f"{FILES_URL}/F1").mock(return_value=httpx.Response(401))
        with pytest.raises(DriveAuthError):
            await api.trashed_state("gd_alice", "F1")
    assert asked == ["gd_alice", "gd_alice"]
    assert route.call_count == 2


async def test_an_expired_token_renewed_once_then_works(api, monkeypatch):
    tokens = iter(["ya29.old", "ya29.new"])

    async def next_token(remote):
        return next(tokens)

    monkeypatch.setattr(api.tokens, "access_token", next_token)
    with respx.mock:
        route = respx.get(f"{FILES_URL}/F1").mock(
            side_effect=[httpx.Response(401), _file("F1", True)]
        )
        assert await api.trashed_state("gd_alice", "F1") is True
    assert route.calls.last.request.headers["Authorization"] == "Bearer ya29.new"
