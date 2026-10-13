"""Stop on an offload (bulk offload spec §4.4, plan decision 9)."""

from datetime import timedelta

from app.models import MailOffload, MailOffloadItem
from app.models.enums import MailItemResult, MailOffloadState, MailState
from app.services.mail import offload as offload_mod
from app.services.mail.offload import OFFLOAD_NO_ANSWER_MSG, STOPPED_MSG
from tests.test_mail_offload_run import OLD, _start, _trash_calls, _wrap


async def test_stop_before_it_runs_ends_it_and_nothing_leaves_gmail(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    r = auth_client.post(f"/api/mail/offloads/{off_id}/stop")
    assert r.status_code == 204
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_MSG
    assert off.finished_at is not None
    await box.run_queued()  # the queued run finds it stopped
    assert _trash_calls(box) == [] and {box.row(s).state for s in OLD} == {MailState.IN_MAIL}
    detail = auth_client.get(f"/api/mail/offloads/{off_id}").json()
    assert detail["state"] == "FAILED" and detail["not_done"] == 3


async def test_stop_during_the_run_stops_before_the_next_batch(
    auth_client, offload_box, db, monkeypatch
):
    monkeypatch.setattr(offload_mod, "GMAIL_BATCH", 1)
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    _wrap(box, lambda ids, n: box.offloads.stop(off_id) if n == 1 else None)
    await box.run_queued()
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.FAILED and off.reason == STOPPED_MSG
    assert off.done == 1 and len(_trash_calls(box)) == 1
    pending = db.query(MailOffloadItem).filter_by(result=MailItemResult.PENDING).count()
    assert pending == 2


async def test_stop_on_a_finished_offload_changes_nothing(auth_client, offload_box, db):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    await box.run_queued()
    assert auth_client.post(f"/api/mail/offloads/{off_id}/stop").status_code == 204
    assert db.get(MailOffload, off_id).state == MailOffloadState.DONE
    assert auth_client.post("/api/mail/offloads/999999/stop").status_code == 404


async def test_no_answer_from_gmail_is_retried_after_1_5_15_then_60_minutes(
    auth_client, offload_box, db
):
    box = offload_box
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    now = {"t": box.offloads.clock()}
    box.offloads.clock = lambda: now["t"]
    box.queue.requests.clear()

    def queued() -> int:
        n = len([r for r in box.queue.requests if r[1] == "offload"])
        box.queue.requests.clear()
        return n

    for tries, minutes in enumerate((1, 5, 15, 60, 60)):  # then every 60
        box.offloads._no_answer_pause(off_id)  # what a run that got no answer does
        asked, not_before = box.offloads._no_answer[off_id]
        assert asked == tries + 1 and not_before - now["t"] == timedelta(minutes=minutes)
        box.offloads.resume_waiting()
        assert queued() == 0  # the next minute's tick leaves it alone
        now["t"] += timedelta(minutes=minutes - 1)
        box.offloads.resume_waiting()
        assert queued() == 0
        now["t"] += timedelta(minutes=1)
        box.offloads.resume_waiting()
        assert queued() == 1  # after the back-off it goes on


async def test_the_back_off_starts_over_once_gmail_answers(auth_client, offload_box, db):
    box = offload_box
    box.offloads.sleep = _no_nap
    await box.backup()
    off_id = _start(auth_client, box.acc, 3, q="older_than:1y").json()["offload_id"]
    now = {"t": box.offloads.clock()}
    box.offloads.clock = lambda: now["t"]
    box.fake.fail("POST", r"/batchModify$", 503, times=6)  # two runs of no answer (3 tries each)
    await box.run_queued()
    assert box.offloads._no_answer[off_id][0] == 1
    now["t"] += timedelta(minutes=1)
    await box.mail.tick()
    await box.run_queued()
    assert box.offloads._no_answer[off_id][0] == 2  # the wait is longer now (5 minutes)
    assert db.get(MailOffload, off_id).reason == OFFLOAD_NO_ANSWER_MSG
    now["t"] += timedelta(minutes=5)
    await box.mail.tick()
    await box.run_queued()  # Gmail answers
    assert off_id not in box.offloads._no_answer  # the next silence starts at 1 minute again
    off = db.get(MailOffload, off_id)
    db.refresh(off)
    assert off.state == MailOffloadState.DONE and off.done == 3
    box.offloads._no_answer_pause(off_id)
    _, not_before = box.offloads._no_answer[off_id]
    assert not_before - now["t"] == timedelta(minutes=1)


async def _no_nap(_s):
    return None
