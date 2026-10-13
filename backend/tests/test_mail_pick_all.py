"""Mail panel spec §6, §9: a pick-all travels as {view, label, q}, and the server works out
the mails when the action starts."""

from datetime import UTC, datetime

from app.devtools.fake_gmail import make_mail
from app.models.enums import MailState
from app.services.mail.offload import restore_targets
from app.services.mail.offload_plan import Pick
from tests.rules_helpers import keep
from tests.test_mail_restore import _offloaded, _restore


def _preview(client, box, **pick):
    return client.post("/api/mail/offloads/preview", json={"account_id": box.acc.id, **pick})


async def test_an_offload_from_a_pick_all_in_a_label_view(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pick = {"view": "all", "label": "Label_2", "q": ""}  # Receipts
    p = _preview(auth_client, box, **pick)
    assert p.status_code == 200 and (p.json()["count"], p.json()["filter_text"]) == (1, "Receipts")
    r = auth_client.post(
        "/api/mail/offloads", json={"account_id": box.acc.id, **pick, "confirm": 1}
    )
    assert r.status_code == 202
    await box.run_queued()
    assert box.row("Old statement").state == MailState.OFFLOADED
    assert box.row("Old photos").state == MailState.IN_MAIL


async def test_a_pick_all_in_the_inbox_with_a_search(auth_client, offload_box):
    box = offload_box
    await box.backup()
    p = _preview(auth_client, box, view="inbox", q="older_than:1y").json()
    assert (p["count"], p["filter_text"]) == (3, "Inbox · older_than:1y")


async def test_a_pick_all_in_all_mail_needs_a_search_or_a_label(auth_client, offload_box):
    box = offload_box
    await box.backup()
    r = _preview(auth_client, box, view="all", q="")
    assert r.status_code == 422
    assert r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    assert _preview(auth_client, box, view="archived", q="").status_code == 200
    assert _preview(auth_client, box, view="nonsense", q="x").status_code == 422


async def test_a_pick_all_is_worked_out_again_when_the_offload_starts(auth_client, offload_box):
    box = offload_box
    await box.backup()
    pick = {"view": "all", "label": "Label_2", "q": ""}
    assert _preview(auth_client, box, **pick).json()["count"] == 1
    box.fake.set_labels(box.provider_id("Old photos"), ("INBOX", "Label_2"))  # labelled in Gmail
    await box.backup()
    r = auth_client.post(
        "/api/mail/offloads", json={"account_id": box.acc.id, **pick, "confirm": 1}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "The number of mails changed since the preview (now 2). Check the preview again."
    )
    assert box.row("Old photos").state == MailState.IN_MAIL


async def test_restore_takes_a_view_and_a_search(auth_client, offload_box):
    box = offload_box
    await _offloaded(auth_client, box)  # Old statement, Old photos, Ancient note
    assert _restore(auth_client, box, view="offloaded", q="photos").json()["count"] == 1
    assert _restore(auth_client, box, view="offloaded", q="").json()["count"] == 3
    assert _restore(auth_client, box, view="offloaded", label="Label_2", q="").json()["count"] == 1
    for view in ("deleted", "inbox"):
        r = _restore(auth_client, box, view=view, q="")
        assert r.status_code == 409
        assert r.json()["detail"] == "Nothing here needs restoring: these mails are in Gmail."


def test_a_picked_thread_restores_only_the_mails_of_its_view(db, layout, make_account):
    acc = make_account()
    a = keep(db, layout, acc, make_mail("Offloaded one"))
    b = keep(db, layout, acc, make_mail("Deleted one"))
    a.state, b.state, b.thread_id = MailState.OFFLOADED, MailState.DELETED, a.thread_id
    db.commit()
    now = datetime(2026, 10, 13, tzinfo=UTC)

    def ids(view):
        return sorted(
            restore_targets(db, acc.id, Pick(thread_ids=[a.thread_id], view=view), None, now)
        )

    assert ids("offloaded") == [a.id]
    assert ids("deleted") == [b.id]
    assert ids("kept") == sorted([a.id, b.id])


def test_a_picked_thread_never_restores_its_mail_that_is_in_gmail(db, layout, make_account):
    acc = make_account()
    a = keep(db, layout, acc, make_mail("Still in Gmail"))
    b = keep(db, layout, acc, make_mail("Offloaded"))
    b.state, b.thread_id = MailState.OFFLOADED, a.thread_id
    db.commit()
    now = datetime(2026, 10, 13, tzinfo=UTC)
    for view in ("offloaded", "kept"):
        got = restore_targets(db, acc.id, Pick(thread_ids=[a.thread_id], view=view), None, now)
        assert got == [b.id]
