import shutil

from app.models import MailMessage
from app.models.enums import MailPhase, MailState, ServiceKind, ServiceState
from app.services.accounts.services import set_service_state
from app.services.mail.index import sync_state
from app.services.mail.store import eml_path
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
    eml_path(box.app.state.layout, box.acc.email, box.row("Ancient note").file_path).write_bytes(
        b"short"
    )
    p = _preview(auth_client, box.acc, q="newer_than:3y").json()  # all six mails
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
        {
            "reason": "already",
            "count": 1,
            "text": "1 mail left out: already offloaded or deleted in Gmail.",
        },
    ]


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


async def test_a_preview_without_the_mail_folder_offers_nothing(auth_client, offload_box, app):
    box = offload_box
    await box.backup()
    shutil.rmtree(app.state.layout.mail_root)  # the disk answers, but the saved mail is gone
    p = _preview(auth_client, box.acc, q="older_than:1y").json()
    assert p["count"] == 0 and p["bytes"] == 0 and p["largest"] == []
    assert [(e["reason"], e["count"]) for e in p["excluded"]] == [("not_backed_up", 3)]


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
