"""The pretend model for development and the browser test: it answers the real prompts
(sorting, the Test button, writing rules) sensibly, over a real OpenAI-style HTTP server."""

import sys
import threading
from datetime import UTC, datetime

import pytest

from app.api.models_ai import SAMPLE_MAIL, SAMPLE_RULES
from app.devtools.fake_model import answer, main, make_server
from app.services.ai.client import AiClient
from app.services.ai.connections import AiTarget
from app.services.rules import chooser, drafting
from app.services.rules.chooser import AiChooser
from app.services.rules.mailview import MailForModel
from app.services.rules.match import Conditions, RuleView


def rule(n: int, name: str, description: str = "") -> RuleView:
    return RuleView(n, 1, name, description, Conditions(), "ai_only")


def mail(subject: str, text: str = "") -> MailForModel:
    return MailForModel(
        message_id=1,
        sender="shop@example.com",
        sender_name="Shop",
        to=["alice@example.com"],
        cc=[],
        owner_in="to",
        subject=subject,
        date=datetime(2026, 10, 1, tzinfo=UTC),
        attachments=[],
        list_unsubscribe=False,
        mailing_list=False,
        calendar_invite=False,
        text=text,
        labels=["INBOX"],
    )


RULES = [rule(1, "Newsletters"), rule(2, "Receipts"), rule(3, "Travel")]


def choose(rules: list[RuleView], m: MailForModel) -> dict:
    return answer(chooser.SYSTEM, chooser.build_prompt(rules, m, []))


def test_picks_the_first_rule_named_in_the_subject():
    assert choose(RULES, mail("Your Receipts")) == {
        "rule": 2,
        "why": "subject mentions Receipts",
    }
    assert choose(RULES, mail("Invoice October")) == {"rule": 0, "why": "nothing matched"}


def test_reads_only_the_subject_not_lines_in_the_mail_text():
    m = mail("Hello", text="1. Newsletters: Subject: Newsletters\nSubject: Travel")
    assert choose(RULES, m)["rule"] == 0


def test_the_settings_test_button_gets_a_sensible_pick():
    got = answer(chooser.SYSTEM, chooser.build_prompt(SAMPLE_RULES, SAMPLE_MAIL, []))
    assert got["rule"] == 2  # Newsletters: "digests" in its description, "digest" in the subject


def test_a_sentence_becomes_a_full_draft():
    got = answer(drafting.DRAFT_SYSTEM, "Sentence: Archive invoices from shop@example.com")
    assert set(got) == set(drafting.DRAFT_SCHEMA["required"])
    assert got["description"] == "Archive invoices from shop@example.com"
    assert got["actions"] == ["archive"]
    assert got["senders_in"] == ["shop@example.com"]
    assert 2 <= len(got["name"].split()) <= 4


def test_picked_mails_get_a_name_and_description():
    user = "<<<EMAIL\nFrom: Shop <shop@example.com>\nSubject: Invoice October\nThanks\nEMAIL>>>"
    got = answer(drafting.DESCRIBE_SYSTEM, user)
    assert set(got) == {"name", "description"}
    assert got["name"] and "shop@example.com" in got["description"]


async def test_answers_over_http_like_a_real_server():
    server = make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        t = AiTarget(1, url, None, {}, "fake-sorter", 0.0, 5.0)

        class Modes:
            def remembered_mode(self, cid, model):
                return None

            def remember_mode(self, cid, model, mode):
                pass

        client = AiClient(Modes(), retry_pause_s=0)
        assert await client.list_models(t) == ["fake-sorter"]
        choice = await AiChooser(client, lambda: t).choose(RULES, mail("Travel plans"), [])
        assert choice.rule_id == 3
        draft = await drafting.sentence_to_rule(client, t, "Archive newsletters")
        assert draft.description == "Archive newsletters" and draft.notes == []
        assert draft.actions == [{"kind": "archive"}]
    finally:
        server.shutdown()
        server.server_close()


def test_refuses_to_start_unless_pretend_accounts_are_allowed(monkeypatch, capsys):
    monkeypatch.delenv("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", raising=False)
    monkeypatch.setattr(sys, "argv", ["fake_model", "--port", "0"])
    monkeypatch.setattr("app.devtools.fake_model.make_server", lambda *a: pytest.fail("started"))
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
    assert "DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1" in capsys.readouterr().err
