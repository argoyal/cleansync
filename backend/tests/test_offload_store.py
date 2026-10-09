from app.models import Offload
from app.models.enums import OffloadState as S
from app.services.offload import store
from app.services.sync.filters import refresh_pair_filters


def _off(db, pair, path, state, is_dir=True):
    off = Offload(pair_id=pair.id, path=path, is_dir=is_dir, state=state)
    db.add(off)
    db.commit()
    return off


def test_offloads_in_force_are_kept_out_of_sync(db, layout, make_account, make_pair):
    pair = make_pair(make_account())
    for path, state in [
        ("Active", S.ACTIVE),
        ("Pending", S.PENDING),
        ("Deleting", S.DELETING),
        ("Verifying", S.VERIFYING),
        ("Attention", S.ATTENTION),  # its Drive copy may be gone: never let bisync see it
        ("Restoring", S.RESTORING),
        ("Restored", S.RESTORED),
    ]:
        _off(db, pair, path, state)
    _off(db, pair, "notes [v2].txt", S.ACTIVE, is_dir=False)
    assert refresh_pair_filters(db, layout, pair) is True
    lines = layout.pair_filters(pair.id).read_text().splitlines()
    for name in ("Active", "Pending", "Deleting", "Verifying", "Attention"):
        assert f"- /{name}/**" in lines
    assert "- /notes \\[v2\\].txt" in lines
    assert not any("Restor" in line for line in lines)
    assert refresh_pair_filters(db, layout, pair) is False  # same content: no change


def test_covers_and_has_inside():
    paths = (("Old", True), ("a.txt", False))
    assert store.covers(paths, "Old") and store.covers(paths, "Old/x/y.txt")
    assert store.covers(paths, "a.txt")
    assert not store.covers(paths, "Older") and not store.covers(paths, "a.txt/b")
    assert store.has_inside(paths, "") and not store.has_inside(paths, "Old")
    assert store.has_inside((("Work/Old", True),), "Work")
    assert not store.has_inside((("Work/Old", True),), "Wor")


def test_startup_turns_unfinished_offloads_into_attention(db, make_account, make_pair):
    pair = make_pair(make_account())
    rows = {s: _off(db, pair, s.value, s) for s in (S.PENDING, S.DELETING, S.VERIFYING, S.ACTIVE)}
    assert store.recover_offloads(db) == 3
    for s in (S.PENDING, S.DELETING, S.VERIFYING):
        db.refresh(rows[s])
        assert rows[s].state == S.ATTENTION
        assert rows[s].note == store.INTERRUPTED_NOTE
    db.refresh(rows[S.ACTIVE])
    assert rows[S.ACTIVE].state == S.ACTIVE and rows[S.ACTIVE].note is None
    # Still out of sync after the restart.
    assert ("PENDING", True) in store.excluded_paths(db, pair.id)


def test_manifest_round_trip(layout):
    files = [{"path": "Old/a.txt", "size": 3, "md5": "abc", "id": "F1"}]
    store.write_manifest(layout, 7, files)
    assert store.read_manifest(layout, 7) == files
    assert store.manifest_file(layout, 7).parent == layout.offloads_dir
    assert store.read_manifest(layout, 8) == []


def test_leftover_delete_lists_are_swept_at_startup(layout):
    store.write_manifest(layout, 7, [])
    left = store.delete_list_file(layout, 12)
    left.write_text("a.txt\n")
    (layout.offloads_dir / "notes.txt").write_text("keep")
    assert left.parent == layout.offloads_dir and left.name == "delete-12.txt"
    assert store.sweep_delete_lists(layout) == 1
    assert not left.exists()
    assert store.manifest_file(layout, 7).exists()
    assert (layout.offloads_dir / "notes.txt").exists()
