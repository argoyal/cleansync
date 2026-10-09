import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Offload, SyncJob, SyncPair
from app.models.enums import OffloadState as S
from app.services.drive_api.client import DriveFileState
from app.services.offload import store
from app.services.offload.service import BatchItem, OffloadBatch, OffloadError, OffloadService
from app.services.queue import MISSING_DISK_MSG
from tests.conftest import StubQueue
from tests.fake_rclone import check_out, jl


class FakeDrive:
    """Google Drive's answers about single files, by ID.

    `files` holds each file's size and MD5 as Drive has it (fill it with `know`); `trashed`
    says whether it is in the trash (default True), None meaning gone for good.
    """

    def __init__(self):
        self.files: dict[str, tuple[int | None, str | None]] = {}
        self.trashed: dict[str, bool | None] = {}
        self.asked: list[str] = []
        self.deleted: list[str] = []

    def know(self, files):
        for _, size, file_id in files:
            if file_id:
                self.files[file_id] = (size, f"md5-{file_id}")

    def _state(self, file_id) -> DriveFileState | None:
        trashed = self.trashed.get(file_id, True)
        if trashed is None:
            return None
        size, md5 = self.files.get(file_id, (None, None))
        return DriveFileState(trashed, size, md5)

    async def file_state(self, remote, file_id):
        self.asked.append(file_id)
        return self._state(file_id)

    async def trashed_state(self, remote, file_id):
        state = await self.file_state(remote, file_id)
        return None if state is None else state.trashed

    async def delete_forever(self, remote, file_id, *, size, md5):
        state = self._state(file_id)
        if state is None:
            return True
        if not state.trashed or not state.matches(size, md5):
            return False
        self.deleted.append(file_id)
        self.trashed[file_id] = None
        return True


class FakeCatalog:
    """The cached Drive listing, without starting real listings in the background."""

    def __init__(self):
        self.listings: dict = {}
        self.refreshed: list[int] = []

    def load(self, pair_id):
        return self.listings.get(pair_id)

    def request_refresh(self, pair_id):
        self.refreshed.append(pair_id)


class StopsAfter(StubQueue):
    """The disk 'drops out' after `n` checks."""

    def __init__(self, n):
        super().__init__()
        self.n = n

    async def check(self):
        self.n -= 1
        if self.n < 0:
            self.disabled_reason = MISSING_DISK_MSG
        return self.disabled_reason is not None


def lsjson(*files):
    """rclone lsjson output: (path, size, id) per file; the MD5 is "md5-<id>"."""
    rows = [
        json.dumps(
            {
                "Path": p,
                "Name": p.rsplit("/", 1)[-1],
                "Size": n,
                "IsDir": False,
                "ID": i,
                "Hashes": {"md5": f"md5-{i}"},
            }
        )
        + ","
        for p, n, i in files
    ]
    return ["[", *rows, "]"]


TWO = (("Old/a.txt", 3, "F1"), ("Old/b.txt", 2, "F2"))


@pytest.fixture
def s(sync_engine, make_account, make_pair, layout):
    acc = make_account()
    pair = make_pair(
        acc, local_subpath="Drive", remote_path="Work", initialized=True, status="HEALTHY"
    )
    root = layout.account_dir(acc.email)
    old = root / "Drive" / "Old"
    old.mkdir(parents=True)
    (old / "a.txt").write_text("aaa")
    (old / "b.txt").write_text("bb")
    queue, drive, catalog = StubQueue(), FakeDrive(), FakeCatalog()
    service = OffloadService(
        engine=sync_engine,
        get_queue=lambda: queue,
        catalog=catalog,
        indexes=None,
        drive=drive,
    )
    lists: list[str] = []
    real_runner = sync_engine.runner

    async def runner(args, *a, **kw):
        # The delete list is removed after the run; keep what rclone was given.
        if "--files-from-raw" in args:
            lists.append(Path(args[args.index("--files-from-raw") + 1]).read_text())
        return await real_runner(args, *a, **kw)

    sync_engine.runner = runner
    return SimpleNamespace(
        acc=acc,
        pair=pair,
        root=root,
        queue=queue,
        drive=drive,
        catalog=catalog,
        service=service,
        lists=lists,
    )


def _drive_has(s, fake_rclone, files=TWO, check_lines=None):
    fake_rclone.on("lsjson", stdout=lsjson(*files))
    lines = check_lines if check_lines is not None else [f"= {p}" for p, _, _ in files]
    fake_rclone.on("check", **check_out(lines))
    s.drive.know(files)


async def _run(s, paths):
    batch = await s.service.start(s.acc.id, paths)
    await s.service.wait_idle()
    return batch


def _subs(fake_rclone):
    return [a[4] for a in fake_rclone.calls()]


async def test_offload_checks_records_deletes_to_trash_and_verifies(s, fake_rclone, db, layout):
    _drive_has(s, fake_rclone)
    batch = await _run(s, ["Drive/Old"])
    assert batch.done and [(i.state, i.message) for i in batch.items] == [
        ("done", "Removed from Google Drive (it is in Drive's trash). Kept on this server.")
    ]
    off = db.scalars(select(Offload)).one()
    assert (off.path, off.is_dir, off.state, off.file_count, off.total_bytes) == (
        "Old",
        True,
        S.ACTIVE,
        2,
        5,
    )
    assert off.trash_verified and off.trashed_ids == ["F1", "F2"]
    assert sorted(s.drive.asked) == ["F1", "F2"]
    assert _subs(fake_rclone) == ["lsjson", "check", "delete", "rmdirs"]
    listing, check = fake_rclone.calls()[:2]
    assert listing[5] == "gd_alice:Work"
    assert check[5:7] == [str(s.root / "Drive"), "gd_alice:Work"]
    for args in (listing, check):
        include = args.index("--filter=+ /Old/**")
        assert args.index("--filter=- **") > include
    delete = fake_rclone.calls()[2]
    assert delete[5] == "gd_alice:Work/Old"
    assert "--drive-use-trash=true" in delete and "--drive-skip-gdocs" in delete
    # Only the checked files, named inside the folder being deleted.
    assert s.lists == ["a.txt\nb.txt\n"]
    assert not Path(delete[delete.index("--files-from-raw") + 1]).exists()
    assert fake_rclone.calls()[3][5] == "gd_alice:Work/Old"
    assert "- /Old/**" in layout.pair_filters(s.pair.id).read_text().splitlines()
    db.expire_all()
    assert db.get(SyncPair, s.pair.id).needs_resync is True
    # One follow-up sync; it becomes a safe resync because the filter changed.
    assert s.queue.requests == [(s.pair.id, "OFFLOAD", "sync")]
    assert s.queue.held == [s.pair.id]
    assert s.catalog.refreshed == [s.pair.id]  # list Drive again: the files left it
    assert (s.root / "Drive" / "Old" / "a.txt").read_text() == "aaa"
    job = db.scalars(select(SyncJob)).one()
    assert (job.kind, job.trigger, job.state) == ("OFFLOAD", "OFFLOAD", "SUCCESS")
    assert store.read_manifest(layout, off.id)[0] == {
        "path": "Old/a.txt",
        "size": 3,
        "md5": "md5-F1",
        "id": "F1",
    }


async def test_a_single_file_uses_deletefile(s, fake_rclone, db):
    _drive_has(s, fake_rclone, files=(("a.txt", 3, "F1"),))
    (s.root / "Drive" / "a.txt").write_text("aaa")
    [item] = (await _run(s, ["Drive/a.txt"])).items
    assert item.state == "done"
    assert _subs(fake_rclone) == ["lsjson", "check", "deletefile"]
    assert fake_rclone.calls()[2][5] == "gd_alice:Work/a.txt"
    assert db.scalars(select(Offload)).one().is_dir is False


@pytest.mark.parametrize("line", ["* Old/a.txt", "+ Old/new.txt", "- Old/b.txt", "! Old/a.txt"])
async def test_any_difference_stops_before_anything_is_removed(s, fake_rclone, db, layout, line):
    _drive_has(s, fake_rclone, check_lines=["= Old/b.txt", line])
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "doesn't match Google Drive" in item.message
    assert _subs(fake_rclone) == ["lsjson", "check"]
    assert db.scalars(select(Offload)).all() == []
    filters = layout.pair_filters(s.pair.id)
    assert not filters.exists() or "Old" not in filters.read_text()
    assert s.queue.requests == []
    job = db.scalars(select(SyncJob)).one()
    assert job.state == "FAILED" and "Nothing was removed" in job.error_message


async def test_a_check_that_compares_nothing_never_deletes(s, fake_rclone, db):
    _drive_has(s, fake_rclone, check_lines=[])
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed"
    assert "compared 0 files but Google Drive lists 2" in item.message
    assert "delete" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


async def test_a_check_of_other_files_never_deletes(s, fake_rclone, db):
    # As many "same" lines as listed files, but not the listed files.
    _drive_has(s, fake_rclone, check_lines=["= Old/a.txt", "= Old/c.txt"])
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "nothing was removed" in item.message
    assert "delete" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


async def test_a_failed_check_removes_nothing(s, fake_rclone, db):
    fake_rclone.on("lsjson", stdout=lsjson(*TWO))
    fake_rclone.on("check", exit=2, stderr=[jl("Failed to create file system", level="critical")])
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "couldn't compare it" in item.message
    assert _subs(fake_rclone) == ["lsjson", "check"]
    assert db.scalars(select(Offload)).all() == []


async def test_a_failed_listing_removes_nothing(s, fake_rclone):
    fake_rclone.on("lsjson", exit=3, stderr=[jl("directory not found", level="error")])
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "couldn't list it on Google Drive" in item.message
    assert _subs(fake_rclone) == ["lsjson"]


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ((("Old/a.txt", 3, None), ("Old/b.txt", 2, "F2")), "didn't give an ID"),
        ((("Old/a.txt", 3, "F1"), ("Other/b.txt", 2, "F2")), "listed files outside"),
        ((("Old/a\nb.txt", 3, "F1"),), "can't be removed safely"),
        ((("Old/a.txt", 3, "F1"), ("Old/a.txt", 3, "F9")), "same name"),
    ],
)
async def test_a_listing_that_cannot_be_trusted_stops_before_the_delete(
    s, fake_rclone, db, files, message
):
    _drive_has(s, fake_rclone, files=files)
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and message in item.message
    assert "delete" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


async def test_a_file_without_md5_stops_before_the_delete(s, fake_rclone, db):
    rows = [
        json.dumps({"Path": "Old/a.txt", "Size": 3, "IsDir": False, "ID": "F1"}) + ",",
        json.dumps(
            {"Path": "Old/b.txt", "Size": 2, "IsDir": False, "ID": "F2", "Hashes": {"md5": "x"}}
        ),
    ]
    fake_rclone.on("lsjson", stdout=["[", *rows, "]"])
    fake_rclone.on("check", **check_out(["= Old/a.txt", "= Old/b.txt"]))
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "didn't give a checksum" in item.message
    assert "delete" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


async def test_a_file_not_in_the_trash_puts_the_offload_in_attention(s, fake_rclone, db, layout):
    _drive_has(s, fake_rclone)
    s.drive.trashed["F2"] = False
    [item] = (await _run(s, ["Drive/Old"])).items
    off = db.scalars(select(Offload)).one()
    assert item.state == "attention" and off.state == S.ATTENTION
    assert "Drive/Old/b.txt" in off.note and "Drive/Old/a.txt" not in off.note
    assert "Use Restore to Drive" in off.note and "newer version" not in off.note
    assert not off.trash_verified
    # Still out of sync: the next sync neither uploads nor deletes the local copy.
    assert "- /Old/**" in layout.pair_filters(s.pair.id).read_text().splitlines()
    assert db.scalars(select(SyncJob)).one().state == "FAILED"
    assert s.queue.requests == [(s.pair.id, "OFFLOAD", "sync")]


@pytest.mark.parametrize(
    "drive_now",
    [(2, "md5-other"), (9, "md5-F2"), (None, None)],
    ids=["md5-changed", "size-changed", "no-details"],
)
async def test_a_trashed_file_that_changed_on_drive_is_attention(s, fake_rclone, db, drive_now):
    _drive_has(s, fake_rclone)
    s.drive.files["F2"] = drive_now  # someone changed it on Drive after the check
    [item] = (await _run(s, ["Drive/Old"])).items
    off = db.scalars(select(Offload)).one()
    assert item.state == "attention" and off.state == S.ATTENTION
    assert "Drive/Old/b.txt" in off.note and not off.trash_verified
    # The newer version is only in Drive's trash: recover it from there, not from here.
    assert "newer version" in off.note and "Google Drive's trash" in off.note
    assert "30 days" in off.note and "Use Restore to Drive to put" not in off.note


async def test_a_file_gone_for_good_is_attention(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    s.drive.trashed["F1"] = None
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "attention" and "Drive/Old/a.txt" in item.message


async def test_a_refused_delete_is_attention_and_still_checks_the_trash(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    fake_rclone.on("delete", exit=1, stderr=[jl("couldn't delete: rate limit", level="error")])
    [item] = (await _run(s, ["Drive/Old"])).items
    off = db.scalars(select(Offload)).one()
    assert off.state == S.ATTENTION and "refused to remove some files" in off.note
    assert sorted(s.drive.asked) == ["F1", "F2"]
    assert "rmdirs" not in _subs(fake_rclone)


async def test_disk_drop_before_the_delete_removes_nothing_from_drive(s, fake_rclone, db, layout):
    _drive_has(s, fake_rclone)
    s.queue = StopsAfter(3)  # start, the pair, the item: then gone before the delete
    s.service._get_queue = lambda: s.queue
    [item] = (await _run(s, ["Drive/Old"])).items
    off = db.scalars(select(Offload)).one()
    assert off.state == S.ATTENTION and "Syncing stopped before anything was removed" in off.note
    assert item.state == "attention"
    assert "delete" not in _subs(fake_rclone)
    assert "- /Old/**" in layout.pair_filters(s.pair.id).read_text().splitlines()


async def test_disk_drop_after_the_delete_skips_removing_empty_folders(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    s.queue = StopsAfter(4)  # ... and the delete: then gone before the empty folders
    s.service._get_queue = lambda: s.queue
    [item] = (await _run(s, ["Drive/Old"])).items
    assert _subs(fake_rclone) == ["lsjson", "check", "delete"]
    off = db.scalars(select(Offload)).one()
    assert off.state == S.ACTIVE and off.trash_verified
    assert item.state == "done" and "empty folders" in off.note


async def test_nothing_starts_while_syncing_is_stopped(s, fake_rclone):
    s.queue.disabled_reason = MISSING_DISK_MSG
    with pytest.raises(OffloadError) as e:
        await s.service.start(s.acc.id, ["Drive/Old"])
    assert e.value.status == 503 and fake_rclone.calls() == []


async def test_blocked_items_are_listed_and_the_rest_run(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    (s.root / "Loose.txt").write_text("x")
    batch = await _run(s, ["Drive/Old", "Drive/nope.txt", "Loose.txt"])
    assert [(i.path, i.state) for i in batch.items] == [
        ("Drive/Old", "done"),
        ("Drive/nope.txt", "failed"),
        ("Loose.txt", "failed"),
    ]
    assert batch.items[1].message == "This is no longer here."
    assert "isn't in a synced folder" in batch.items[2].message
    assert s.service.batch(batch.id) is batch


async def test_a_busy_pair_is_left_alone(s, fake_rclone):
    _drive_has(s, fake_rclone)
    s.queue.running.add(s.pair.id)
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "syncing right now" in item.message
    assert fake_rclone.calls() == []


async def test_a_pretend_account_has_no_trash_to_check(
    sync_engine, make_account, make_pair, layout, fake_rclone, db
):
    acc = make_account(remote_name="local_abc123")
    make_pair(acc, local_subpath="", remote_path="", initialized=True)
    root = layout.account_dir(acc.email)
    (root / "Old").mkdir()
    (root / "Old" / "a.txt").write_text("aaa")
    fake_rclone.on("lsjson", stdout=lsjson(("Old/a.txt", 3, None)))
    fake_rclone.on("check", **check_out(["= Old/a.txt"]))
    drive = FakeDrive()
    service = OffloadService(
        engine=sync_engine,
        get_queue=StubQueue,
        catalog=FakeCatalog(),
        indexes=None,
        drive=drive,
        allow_pretend=True,
    )
    await service.start(acc.id, ["Old"])
    await service.wait_idle()
    off = db.scalars(select(Offload)).one()
    assert off.state == S.ACTIVE and "Pretend account" in off.note and drive.asked == []
    assert fake_rclone.calls()[2][5] == "local_abc123:Old"


async def test_a_pretend_account_is_real_without_the_setting(
    sync_engine, make_account, make_pair, layout, fake_rclone, db
):
    acc = make_account(remote_name="local_abc123")
    make_pair(acc, local_subpath="", remote_path="", initialized=True)
    root = layout.account_dir(acc.email)
    (root / "Old").mkdir()
    (root / "Old" / "a.txt").write_text("aaa")
    fake_rclone.on("lsjson", stdout=lsjson(("Old/a.txt", 3, None)))
    fake_rclone.on("check", **check_out(["= Old/a.txt"]))
    service = OffloadService(
        engine=sync_engine,
        get_queue=StubQueue,
        catalog=FakeCatalog(),
        indexes=None,
        drive=FakeDrive(),
    )
    batch = await service.start(acc.id, ["Old"])
    await service.wait_idle()
    assert "didn't give an ID" in batch.items[0].message
    assert "delete" not in _subs(fake_rclone)


async def test_an_unexpected_error_leaves_the_offload_in_attention(s, fake_rclone, db, layout):
    _drive_has(s, fake_rclone)

    async def broken(remote, file_id):
        raise RuntimeError("boom")

    s.drive.file_state = broken
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "attention" and "boom" in item.message
    assert store.INTERRUPTED_NOTE in item.message
    off = db.scalars(select(Offload)).one()
    assert off.state == S.ATTENTION and off.note == store.INTERRUPTED_NOTE
    assert "- /Old/**" in layout.pair_filters(s.pair.id).read_text().splitlines()
    assert db.scalars(select(SyncJob)).one().state == "FAILED"
    assert s.queue.requests == [(s.pair.id, "OFFLOAD", "sync")]


async def test_an_item_offloaded_meanwhile_is_not_offloaded_twice(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    planned = await s.service.preview(s.acc.id, ["Drive/Old"])  # planned before the first ran
    await _run(s, ["Drive/Old"])
    late = OffloadBatch("late", s.acc.id, [BatchItem("Drive/Old")])
    await s.service._run_batch(late, planned)
    assert [(i.state, i.message) for i in late.items] == [("failed", "This is already offloaded.")]
    assert _subs(fake_rclone).count("delete") == 1
    assert len(db.scalars(select(Offload)).all()) == 1


async def test_a_pair_made_one_way_after_planning_is_not_offloaded(s, fake_rclone, db):
    _drive_has(s, fake_rclone)
    planned = await s.service.preview(s.acc.id, ["Drive/Old"])
    pair = db.get(SyncPair, s.pair.id)
    pair.direction = "LOCAL_TO_REMOTE"
    db.commit()
    late = OffloadBatch("late", s.acc.id, [BatchItem("Drive/Old")])
    await s.service._run_batch(late, planned)
    assert [(i.state, i.message) for i in late.items] == [
        ("failed", "Offload works only in folders that sync both ways.")
    ]
    assert _subs(fake_rclone) == []
    assert db.scalars(select(Offload)).all() == []


async def test_a_single_file_with_a_twin_on_drive_says_so(s, fake_rclone, db):
    _drive_has(s, fake_rclone, files=(("a.txt", 3, "F1"), ("a.txt", 3, "F9")))
    (s.root / "Drive" / "a.txt").write_text("aaa")
    [item] = (await _run(s, ["Drive/a.txt"])).items
    assert item.state == "failed" and "same name" in item.message
    assert "deletefile" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


async def test_names_differing_only_in_case_stop_before_the_delete(s, fake_rclone, db):
    # Even if the check called both "same" (a disk that ignores case holds only one of them).
    _drive_has(s, fake_rclone, files=(("Old/A.txt", 3, "F1"), ("Old/a.txt", 3, "F2")))
    [item] = (await _run(s, ["Drive/Old"])).items
    assert item.state == "failed" and "same name" in item.message
    assert "delete" not in _subs(fake_rclone)
    assert db.scalars(select(Offload)).all() == []


class HoldBreaks(StubQueue):
    @contextlib.contextmanager
    def hold(self, pair_id):
        raise OSError("hold broke")
        yield


async def test_a_hold_that_breaks_fails_its_items_with_the_reason(s, fake_rclone):
    _drive_has(s, fake_rclone)
    s.queue = HoldBreaks()
    s.service._get_queue = lambda: s.queue
    batch = await _run(s, ["Drive/Old"])
    assert batch.done
    [item] = batch.items
    assert item.state == "failed" and "hold broke" in item.message
    assert fake_rclone.calls() == []


async def test_a_batch_cut_short_never_ends_with_waiting_items(s, fake_rclone, monkeypatch):
    _drive_has(s, fake_rclone)

    async def cut(*a, **kw):
        raise RuntimeError("cut short")

    monkeypatch.setattr(s.service, "_run_pair", cut)
    batch = await _run(s, ["Drive/Old"])
    assert batch.done and [i.state for i in batch.items] == ["failed"]
    assert "nothing was removed" in batch.items[0].message
