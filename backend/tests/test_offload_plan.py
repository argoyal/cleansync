from datetime import UTC, datetime

import pytest

from app.models.enums import Direction
from app.services.catalog.model import Catalog, CatalogEntry
from app.services.files.scope import Scope
from app.services.offload import plan as P

NOW = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


@pytest.fixture
def world(layout, make_account, make_pair):
    def _make(**pair_kw):
        acc = make_account()
        fields = {"local_subpath": "Drive", "initialized": True} | pair_kw
        pair = make_pair(acc, **fields)
        root = layout.account_dir(acc.email)
        (root / "Drive" / "Old").mkdir(parents=True)
        (root / "Drive" / "Old" / "a.txt").write_text("aaa")
        return Scope(account=acc, root=root, pairs=[pair]), pair, root

    return _make


def _plan(scope, rels, **kw):
    args = {"catalogs": {}, "offloads": {}, "conflicts": {}, "busy": set(), "connected": True}
    return P.plan_offload(scope, rels, **(args | kw))


def _cat(pair, *entries):
    return {pair.id: Catalog.build(pair.id, NOW, "root", list(entries))}


def test_a_synced_folder_is_ready_with_its_counts(world):
    scope, pair, _ = world()
    [item] = _plan(scope, ["Drive/Old"])
    assert (item.blocked, item.prel, item.is_dir) == (None, "Old", True)
    assert (item.file_count, item.total_bytes) == (1, 3)


def test_native_docs_are_skipped_not_blocking(world):
    scope, pair, _ = world()
    cats = _cat(
        pair,
        CatalogEntry("Old/Plan.docx", False, 0, None, id="D1", native="document"),
        CatalogEntry("Sheet.xlsx", False, 0, None, id="S1", native="spreadsheet"),
    )
    folder, native = _plan(scope, ["Drive/Old", "Drive/Sheet.xlsx"], catalogs=cats)
    assert folder.blocked is None and folder.stays_on_drive == ["Drive/Old/Plan"]
    assert native.blocked == P.NATIVE_MSG
    assert P.NATIVE_MSG == "Stays on Drive — not a file DriveSync can keep a copy of yet."


def test_a_folder_holding_only_native_docs_has_nothing_to_offload(world):
    scope, pair, root = world()
    (root / "Drive" / "Docs").mkdir()
    cats = _cat(pair, CatalogEntry("Docs/Plan.docx", False, 0, None, id="D1", native="document"))
    [item] = _plan(scope, ["Drive/Docs"], catalogs=cats)
    assert item.blocked == P.ONLY_NATIVE_MSG
    (root / "Drive" / "Empty").mkdir()
    assert _plan(scope, ["Drive/Empty"])[0].blocked == P.EMPTY_MSG


def test_each_refusal_says_why(world):
    scope, pair, root = world()
    (root / "Loose.txt").write_text("x")
    assert _plan(scope, ["Loose.txt"])[0].blocked == P.NOT_SYNCED_MSG
    assert _plan(scope, ["Drive"])[0].blocked == P.PAIR_ROOT_MSG
    assert _plan(scope, ["Drive/nope.txt"])[0].blocked == P.NOT_FOUND_MSG
    remote_only = _cat(pair, CatalogEntry("Old/far.txt", False, 3, NOW))
    assert _plan(scope, ["Drive/Old/far.txt"], catalogs=remote_only)[0].blocked == P.DRIVE_ONLY_MSG
    assert _plan(scope, ["Drive/Old"], connected=False)[0].blocked == P.RECONNECT_MSG
    assert _plan(scope, ["Drive/Old"], busy={pair.id})[0].blocked == P.BUSY_MSG
    done = {pair.id: (("Old", True),)}
    assert _plan(scope, ["Drive/Old"], offloads=done)[0].blocked == P.ALREADY_MSG
    assert _plan(scope, ["Drive/Old/a.txt"], offloads=done)[0].blocked == P.ALREADY_MSG
    part = {pair.id: (("Old/sub", True),)}
    assert _plan(scope, ["Drive/Old"], offloads=part)[0].blocked == P.PART_MSG
    clash = {pair.id: {"Old/a.conflict1.txt"}}
    assert _plan(scope, ["Drive/Old"], conflicts=clash)[0].blocked == P.CONFLICT_MSG
    differs = _cat(
        pair, CatalogEntry("Old", True, 0, NOW), CatalogEntry("Old/a.txt", False, 99, NOW)
    )
    assert _plan(scope, ["Drive/Old"], catalogs=differs)[0].blocked == P.PENDING_MSG


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"direction": Direction.REMOTE_TO_LOCAL}, P.ONE_WAY_MSG),
        ({"initialized": False}, P.NOT_STARTED_MSG),
    ],
)
def test_only_set_up_two_way_folders_can_offload(world, fields, message):
    scope, _, _ = world(**fields)
    assert _plan(scope, ["Drive/Old"])[0].blocked == message


def test_selection_inside_a_selected_folder_counts_once(world):
    scope, _, _ = world()
    items = _plan(scope, ["Drive/Old", "Drive/Old/a.txt", "Drive/Old"])
    assert [i.path for i in items] == ["Drive/Old"]
