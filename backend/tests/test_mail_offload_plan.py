import shutil

from app.models import MailMessage
from app.models.enums import MailPhase, MailState, ServiceKind, ServiceState
from app.services.accounts.services import set_service_state
from app.services.app_settings import set_settings
from app.services.mail.index import sync_state
from app.services.queue import MISSING_DISK_MSG


def _preview(client, acc, **body):
    return client.post("/api/mail/offloads/preview", json={"account_id": acc.id, **body})


async def test_the_preview_counts_sizes_and_lists_largest_and_oldest(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    r = _preview(auth_client, box.acc, q="older_than:1y")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["filter_text"] == "older_than:1y" and p["count"] == 3
    assert p["bytes"] == sum(
        box.row(s).size for s in ("Old statement", "Old photos", "Ancient note")
    )
    assert [m["subject"] for m in p["oldest"]] == ["Ancient note", "Old photos", "Old statement"]
    assert len(p["largest"]) == 3 and p["excluded"] == []


async def test_the_preview_leaves_out_new_mail_unbacked_mail_and_mail_already_gone(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    db.get(MailMessage, box.row("Old photos").id).state = MailState.OFFLOADED
    db.commit()
    db.get(MailMessage, box.row("Ancient note").id).file_size = 0  # no file recorded (0.5.2)
    db.commit()
    p = _preview(auth_client, box.acc, q="newer_than:3y").json()  # every mail in Gmail
    assert p["count"] == 2  # Old statement, Last month
    assert p["excluded"] == [
        {
            "reason": "too_new",
            "count": 2,
            "text": "2 mails left out: newer than 30 days. Recent mail always stays in Gmail.",
        },
        {
            "reason": "not_backed_up",
            "count": 1,
            "text": "1 mail left out: not fully backed up here.",
        },
    ]  # mail already out of Gmail isn't in All mail, so a search never counts it


async def test_picked_mails_and_threads_use_the_same_preview(auth_client, offload_box):
    box = offload_box
    await box.backup()
    ids = [box.row("Old statement").id, box.row("Yesterday").id]
    p = _preview(auth_client, box.acc, message_ids=ids).json()
    assert p["filter_text"] == "2 picked mails" and p["count"] == 1
    assert p["excluded"][0]["reason"] == "too_new"
    t = _preview(auth_client, box.acc, thread_ids=[box.row("Ancient note").thread_id]).json()
    assert t["count"] == 1


async def test_refusals_change_nothing(auth_client, offload_box, db, app):
    box = offload_box
    r = _preview(auth_client, box.acc, q="older_than:1y")
    assert r.status_code == 409 and r.json()["detail"] == (
        "The first mail backup hasn't finished yet. Offload works once every mail is backed up."
    )
    await box.backup()
    assert sync_state(db, box.acc.id).phase == MailPhase.INCREMENTAL
    r = _preview(auth_client, box.acc, q="")
    assert (
        r.status_code == 422
        and r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    )
    r = _preview(auth_client, box.acc, q="larger:abc")
    assert r.status_code == 422
    set_service_state(db, box.acc.id, ServiceKind.MAIL, ServiceState.NEEDS_RECONNECT)
    r = _preview(auth_client, box.acc, q="older_than:1y")
    assert r.status_code == 409 and "Reconnect" in r.json()["detail"]
    set_service_state(db, box.acc.id, ServiceKind.MAIL, ServiceState.ACTIVE)
    app.state.queue.disabled_reason = MISSING_DISK_MSG
    r = _preview(auth_client, box.acc, q="older_than:1y")
    assert r.status_code == 503 and r.json()["detail"] == MISSING_DISK_MSG
    assert box.fake.calls == [
        c for c in box.fake.calls if c[0] == "GET"
    ]  # nothing changed in Gmail


async def test_a_preview_does_not_look_at_the_disk(auth_client, offload_box, app):
    """0.5.2 §9: the preview reads the index only. A missing file is caught right before the
    mail would leave Gmail (see test_a_missing_file_is_never_trashed)."""
    box = offload_box
    await box.backup()
    shutil.rmtree(app.state.layout.mail_root)
    p = _preview(auth_client, box.acc, q="older_than:1y").json()
    assert p["count"] == 3 and p["excluded"] == []


async def test_a_preview_waits_while_the_mailbox_is_being_listed_again(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    sync_state(db, box.acc.id).phase = MailPhase.RECHECK
    db.commit()
    r = _preview(auth_client, box.acc, q="older_than:1y")
    assert r.status_code == 409
    assert (
        r.json()["detail"]
        == "Wait until the mail backup has finished checking your mailbox, then try again."
    )


async def test_forty_thousand_picked_ids_do_not_break_the_preview(auth_client, offload_box):
    box = offload_box
    await box.backup()
    ids = [box.row("Old statement").id, *range(10_000_000, 10_040_000)]
    p = _preview(auth_client, box.acc, message_ids=ids)
    assert p.status_code == 200, p.text
    assert p.json()["count"] == 1
    threads = [box.row("Old photos").thread_id, *(f"none-{i}" for i in range(40_000))]
    assert _preview(auth_client, box.acc, thread_ids=threads).json()["count"] == 1


async def test_picking_mails_and_threads_together_is_refused(auth_client, offload_box):
    box = offload_box
    await box.backup()
    r = _preview(auth_client, box.acc, message_ids=[1], thread_ids=["x"])
    assert r.status_code == 422 and r.json()["detail"] == "Pick either mails or threads, not both."


async def test_the_minimum_age_setting_sets_the_cut_off(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    set_settings(db, {"mail_min_age_days": 365})
    p = _preview(auth_client, box.acc, q="newer_than:3y").json()
    assert p["count"] == 3  # 400, 500 and 900 days old; the 40-day-old mail is now too new
    assert p["excluded"] == [
        {
            "reason": "too_new",
            "count": 3,
            "text": "3 mails left out: newer than 365 days. Recent mail always stays in Gmail.",
        }
    ]


async def test_an_empty_search_in_quotes_is_still_no_filter(auth_client, offload_box):
    box = offload_box
    await box.backup()
    for q in ('""', '  ""  ', '"" ""'):
        r = _preview(auth_client, box.acc, q=q)
        assert r.status_code == 422, q
        assert r.json()["detail"] == "Choose at least one filter, or pick mails in the list."
    assert _preview(auth_client, box.acc, q="older_than:1y").status_code == 200


async def test_a_sender_search_counts_only_that_sender(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    senders = {
        "Old statement": "news@shop.com",
        "Old photos": "technews@shop.com",
        "Ancient note": "news@shop.com.au",
    }
    for subject, sender in senders.items():
        row = db.get(MailMessage, box.row(subject).id)
        row.sender = sender
    db.commit()
    p = _preview(auth_client, box.acc, q="sender:news@shop.com").json()
    assert p["count"] == 1 and p["bytes"] == box.row("Old statement").size
