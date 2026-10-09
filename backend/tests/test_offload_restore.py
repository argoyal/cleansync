import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Offload, SyncPair
from app.models.enums import OffloadState as S
from app.services.offload import store
from app.services.offload.service import (
    WAITING_NOTE,
    OffloadError,
    OffloadService,
    local_copy_problems,
)
from app.services.queue import MISSING_DISK_MSG
from app.services.sync.filters import refresh_pair_filters
from app.services.sync.initial import InitialSync
from tests.conftest import StubQueue
from tests.fake_rclone import check_out, jl
from tests.test_offload_run import FakeCatalog, FakeDrive


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


@pytest.fixture
def s(sync_engine, make_account, make_pair, layout, db):
    acc = make_account()
    pair = make_pair(acc, local_subpath="Drive", remote_path="Work", initialized=True)
    root = layout.account_dir(acc.email)
    (root / "Drive" / "Old").mkdir(parents=True)
    (root / "Drive" / "Old" / "a.txt").write_text("aaa")
    (root / "Drive" / "Old" / "b.txt").write_text("bb")
    off = Offload(
        pair_id=pair.id,
        path="Old",
        is_dir=True,
        file_count=2,
        total_bytes=5,
        state=S.ACTIVE,
        trashed_ids=["F1", "F2"],
        trash_verified=True,
    )
    db.add(off)
    db.commit()
    store.write_manifest(
        layout,
        off.id,
        [
            {"path": "Old/a.txt", "size": 3, "md5": _md5("aaa"), "id": "F1"},
            {"path": "Old/b.txt", "size": 2, "md5": _md5("bb"), "id": "F2"},
        ],
    )
    refresh_pair_filters(db, layout, pair)
    queue, drive, catalog = StubQueue(), FakeDrive(), FakeCatalog()
    # What Drive's trash holds: the files as they were offloaded.
    drive.files.update({"F1": (3, _md5("aaa")), "F2": (2, _md5("bb"))})
    service = OffloadService(
        engine=sync_engine,
        get_queue=lambda: queue,
        catalog=catalog,
        indexes=None,
        drive=drive,
    )
    return SimpleNamespace(
        acc=acc,
        pair=pair,
        root=root,
        off=off,
        queue=queue,
        drive=drive,
        catalog=catalog,
        service=service,
    )


# ---- Restore to Drive ---------------------------------------------------------------------


async def test_restore_puts_the_path_back_in_sync_and_asks_for_a_sync(s, db, layout):
    out = await s.service.restore(s.off.id)
    assert out.state == S.RESTORING and out.note is None
    assert "Old" not in layout.pair_filters(s.pair.id).read_text()
    db.expire_all()
    assert db.get(SyncPair, s.pair.id).needs_resync is True
    assert s.queue.requests == [(s.pair.id, "OFFLOAD", "sync")]
    assert s.queue.held == [s.pair.id]
    s.service.resync_done(s.pair.id)
    db.expire_all()
    assert db.get(Offload, s.off.id).state == S.RESTORED
    assert s.catalog.refreshed == [s.pair.id]


async def test_resync_done_leaves_offloads_not_going_back_alone(s, db):
    s.service.resync_done(s.pair.id)
    db.expire_all()
    assert db.get(Offload, s.off.id).state == S.ACTIVE
    assert s.catalog.refreshed == []


async def test_restore_waits_for_sync_on_when_the_folder_is_off(s, db):
    pair = db.get(SyncPair, s.pair.id)
    pair.paused = True
    db.commit()
    out = await s.service.restore(s.off.id)
    assert out.state == S.RESTORING and out.note == WAITING_NOTE
    assert "Syncing is off" in out.note


async def test_restore_of_an_offload_that_needs_a_look(s, db):
    off = db.get(Offload, s.off.id)
    off.state, off.note = S.ATTENTION, store.INTERRUPTED_NOTE
    db.commit()
    out = await s.service.restore(s.off.id)
    assert out.state == S.RESTORING and out.note is None


@pytest.mark.parametrize("reason", ["BLOCKED", "ACCESS_CHECK", "NEEDS_RESYNC"])
async def test_restore_is_refused_while_the_folder_waits_for_the_owner(s, db, reason):
    pair = db.get(SyncPair, s.pair.id)
    pair.attention_reason = reason
    db.commit()
    with pytest.raises(OffloadError, match="waiting for you"):
        await s.service.restore(s.off.id)
    db.expire_all()
    assert db.get(Offload, s.off.id).state == S.ACTIVE
    assert s.queue.requests == []


async def test_restore_refusals(s, db):
    s.queue.disabled_reason = MISSING_DISK_MSG
    with pytest.raises(OffloadError) as e:
        await s.service.restore(s.off.id)
    assert e.value.status == 503
    s.queue.disabled_reason = None
    s.queue.running.add(s.pair.id)
    with pytest.raises(OffloadError, match="syncing right now"):
        await s.service.restore(s.off.id)
    s.queue.running.clear()
    s.service.emptying.add(s.off.id)
    with pytest.raises(OffloadError, match="emptying these from the trash right now"):
        await s.service.restore(s.off.id)
    s.service.emptying.clear()
    await s.service.restore(s.off.id)
    with pytest.raises(OffloadError, match="already going back"):
        await s.service.restore(s.off.id)
    with pytest.raises(OffloadError) as e:
        await s.service.restore(999)
    assert e.value.status == 404
    # A refused restore changed nothing.
    db.expire_all()
    assert len(s.queue.requests) == 1


async def test_a_finished_resync_tells_its_listeners(
    sync_engine, make_account, make_pair, fake_rclone
):
    initial = InitialSync(sync_engine)
    seen: list[int] = []

    def broken(pair_id):
        raise RuntimeError("boom")

    initial.on_resync_done.append(broken)  # one failing listener doesn't stop the others
    initial.on_resync_done.append(seen.append)
    pair = make_pair(make_account(), initialized=True)
    fake_rclone.on("check", **check_out([]))
    await initial.safe_resync(pair.id, "RESYNC")
    assert seen == [pair.id]


async def test_a_failed_resync_tells_no_one(sync_engine, make_account, make_pair, fake_rclone):
    initial = InitialSync(sync_engine)
    seen: list[int] = []
    initial.on_resync_done.append(seen.append)
    pair = make_pair(make_account(), initialized=True)
    fake_rclone.on("check", **check_out([]))
    fake_rclone.on("bisync", exit=7, stderr=[jl("Fatal error: boom", "error")])
    await initial.safe_resync(pair.id, "RESYNC")
    assert seen == []


# ---- the local copy check -----------------------------------------------------------------


def test_local_copy_problems(tmp_path):
    (tmp_path / "Old").mkdir()
    (tmp_path / "Old" / "a.txt").write_text("aaa")
    (tmp_path / "Old" / "b.txt").write_text("bb")
    (tmp_path / "elsewhere.txt").write_text("aaa")
    (tmp_path / "Old" / "link.txt").symlink_to(tmp_path / "elsewhere.txt")
    manifest = [
        {"path": "Old/a.txt", "size": 3, "md5": _md5("aaa")},
        {"path": "Old/b.txt", "size": 2, "md5": _md5("xx")},  # same size, other content
        {"path": "Old/gone.txt", "size": 1, "md5": None},
        {"path": "Old/link.txt", "size": 3, "md5": _md5("aaa")},
        {"path": "../escape.txt", "size": 1, "md5": None},
        {"path": "Old/a.txt", "size": 4, "md5": None},
    ]
    assert local_copy_problems(tmp_path, manifest) == [
        "Old/b.txt",
        "Old/gone.txt",
        "Old/link.txt",
        "../escape.txt",
        "Old/a.txt",
    ]


# ---- Empty from trash now -----------------------------------------------------------------


async def _empty(s):
    await s.service.empty_trash(s.off.id)
    await s.service.wait_idle()
    assert s.off.id not in s.service.emptying


async def test_empty_trash_deletes_only_the_recorded_ids(s, db, layout):
    store.write_manifest(
        layout,
        s.off.id,
        [
            *store.read_manifest(layout, s.off.id),
            {"path": "Old/c.txt", "size": 1, "md5": _md5("c"), "id": "NOT_OURS"},  # not recorded
        ],
    )
    s.drive.files["NOT_OURS"] = (1, _md5("c"))  # in the trash and matching
    (s.root / "Drive" / "Old" / "c.txt").write_text("c")
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert sorted(s.drive.deleted) == ["F1", "F2"]
    assert off.trash_emptied is True and off.note is None


async def test_empty_trash_refuses_when_the_server_copy_changed(s, db):
    (s.root / "Drive" / "Old" / "a.txt").write_text("changed!")
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == [] and off.trash_emptied is False
    assert "not the same as when it was offloaded" in off.note and "Drive/Old/a.txt" in off.note


async def test_empty_trash_refuses_when_the_same_size_server_copy_differs(s, db):
    (s.root / "Drive" / "Old" / "a.txt").write_text("xyz")  # same size, other MD5
    await _empty(s)
    assert s.drive.deleted == []


async def test_empty_trash_refuses_when_a_server_file_is_gone(s, db):
    (s.root / "Drive" / "Old" / "b.txt").unlink()
    await _empty(s)
    assert s.drive.deleted == []


async def test_empty_trash_refuses_without_the_saved_list(s, db, layout):
    store.manifest_file(layout, s.off.id).unlink()
    await _empty(s)
    db.expire_all()
    assert s.drive.deleted == [] and "lost its list" in db.get(Offload, s.off.id).note


async def test_empty_trash_never_deletes_a_file_taken_out_of_the_trash(s, db):
    s.drive.trashed["F2"] = False
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == ["F1"] and off.trash_emptied is False
    assert "no longer in Google Drive's trash" in off.note and "Drive/Old/b.txt" in off.note


async def test_empty_trash_never_deletes_a_file_changed_on_drive(s, db):
    s.drive.files["F1"] = (3, _md5("zzz"))  # someone changed it in Drive's trash
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == ["F2"] and off.trash_emptied is False
    assert "changed there" in off.note and "Drive/Old/a.txt" in off.note


async def test_empty_trash_stops_when_the_disk_drops_out(s, db):
    from tests.test_offload_run import StopsAfter

    queue = StopsAfter(2)  # empty_trash's check, then the first delete's
    s.service._get_queue = lambda: queue
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == ["F1"] and off.trash_emptied is False
    assert "stopped part-way" in off.note


async def test_empty_trash_refusals(s, db):
    s.queue.disabled_reason = MISSING_DISK_MSG
    with pytest.raises(OffloadError) as e:
        await s.service.empty_trash(s.off.id)
    assert e.value.status == 503
    s.queue.disabled_reason = None
    with pytest.raises(OffloadError) as e:
        await s.service.empty_trash(999)
    assert e.value.status == 404
    s.service.emptying.add(s.off.id)
    with pytest.raises(OffloadError, match="already emptying"):
        await s.service.empty_trash(s.off.id)
    s.service.emptying.clear()
    off = db.get(Offload, s.off.id)
    off.trash_emptied = True
    db.commit()
    with pytest.raises(OffloadError, match="already gone"):
        await s.service.empty_trash(s.off.id)
    off.trash_emptied = False
    off.trash_verified = False
    db.commit()
    with pytest.raises(OffloadError, match="checked in the trash"):
        await s.service.empty_trash(s.off.id)
    assert s.drive.deleted == []


# ---- Restore and Empty racing (fix round 1) -----------------------------------------------


class YieldingQueue(StubQueue):
    """Like the real queue: checking the disk gives the event loop to others."""

    async def check(self):
        await asyncio.sleep(0)
        return self.disabled_reason is not None


async def test_restore_then_empty_started_together_only_restore_runs(s, db):
    queue = YieldingQueue()
    s.service._get_queue = lambda: queue
    restored, emptied = await asyncio.gather(
        s.service.restore(s.off.id), s.service.empty_trash(s.off.id), return_exceptions=True
    )
    await s.service.wait_idle()
    assert restored.state == S.RESTORING
    assert isinstance(emptied, OffloadError) and "going back to Google Drive" in str(emptied)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == [] and off.trash_emptied is False and off.state == S.RESTORING


async def test_empty_then_restore_started_together_only_empty_runs(s, db):
    queue = YieldingQueue()
    s.service._get_queue = lambda: queue
    emptied, restored = await asyncio.gather(
        s.service.empty_trash(s.off.id), s.service.restore(s.off.id), return_exceptions=True
    )
    await s.service.wait_idle()
    assert isinstance(restored, OffloadError) and "emptying" in str(restored)
    assert not isinstance(emptied, Exception)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert sorted(s.drive.deleted) == ["F1", "F2"] and off.state == S.ACTIVE
    assert queue.requests == []


async def test_two_empty_calls_together_run_once(s, db):
    queue = YieldingQueue()
    s.service._get_queue = lambda: queue
    first, second = await asyncio.gather(
        s.service.empty_trash(s.off.id), s.service.empty_trash(s.off.id), return_exceptions=True
    )
    await s.service.wait_idle()
    assert not isinstance(first, Exception)
    assert isinstance(second, OffloadError) and "already emptying" in str(second)
    assert sorted(s.drive.deleted) == ["F1", "F2"]


async def test_empty_stops_when_the_offload_is_no_longer_done(s, db):
    # Belt and braces: whatever moved the row on, no permanent delete follows.
    real = s.drive.delete_forever

    async def then_restore(remote, file_id, *, size, md5):
        ok = await real(remote, file_id, size=size, md5=md5)
        with s.service.sf() as other:
            other.get(Offload, s.off.id).state = S.RESTORING
            other.commit()
        return ok

    s.drive.delete_forever = then_restore
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == ["F1"] and off.trash_emptied is False
    assert "1 of 2" in off.note and "no longer offloaded" in off.note


# ---- honest notes (fix round 1) -----------------------------------------------------------


async def test_a_partial_empty_says_how_many_were_deleted_and_kept(s, db):
    from app.models import Event

    s.drive.trashed["F2"] = False
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert off.note.startswith("Deleted 1 of 2 files from Google Drive's trash for good.")
    events = db.scalars(select(Event).where(Event.kind == "offload_trash_emptied")).all()
    assert [e.level for e in events] == ["warning"]
    assert "1 of 2" in events[0].message and "1 left in the trash" in events[0].message


async def test_a_full_empty_says_so(s, db):
    from app.models import Event

    await _empty(s)
    db.expire_all()
    [event] = db.scalars(select(Event).where(Event.kind == "offload_trash_emptied")).all()
    assert event.level == "info" and "deleted all 2 files" in event.message


async def test_a_recorded_id_missing_from_the_saved_list_is_not_counted_as_emptied(s, db, layout):
    off = db.get(Offload, s.off.id)
    off.trashed_ids = ["F1", "F2", "F3"]
    db.commit()
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert sorted(s.drive.deleted) == ["F1", "F2"] and off.trash_emptied is False
    assert "2 of 3" in off.note and "1 file isn't in DriveSync's saved list" in off.note


async def test_a_server_copy_changed_during_the_empty_keeps_that_file(s, db):
    real = s.drive.delete_forever

    async def then_edit(remote, file_id, *, size, md5):
        ok = await real(remote, file_id, size=size, md5=md5)
        (s.root / "Drive" / "Old" / "b.txt").write_text("edited meanwhile")
        return ok

    s.drive.delete_forever = then_edit
    await _empty(s)
    db.expire_all()
    off = db.get(Offload, s.off.id)
    assert s.drive.deleted == ["F1"] and off.trash_emptied is False
    assert "changed on this server" in off.note and "Drive/Old/b.txt" in off.note


async def test_restore_refuses_a_pair_that_is_not_two_way(s, db):
    pair = db.get(SyncPair, s.pair.id)
    pair.direction = "LOCAL_TO_REMOTE"
    db.commit()
    with pytest.raises(OffloadError, match="sync both ways"):
        await s.service.restore(s.off.id)
    db.expire_all()
    assert db.get(Offload, s.off.id).state == S.ACTIVE and s.queue.requests == []
