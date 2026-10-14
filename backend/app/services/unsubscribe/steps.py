"""One sender through the unsubscribe steps (0.5.2 §4.3), and the Gmail filter calls (§5).

Logs name only a link's host: the rest of a link may hold the owner's token."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from urllib.parse import urlencode, urlsplit

from app.models.enums import AiTaskName, UnsubscribeMethod, UnsubscribeOutcome
from app.services.accounts.filters import (
    LOST_MSG,
    NOT_ALLOWED_MSG,
    allows_gmail_filters,
    permission_lost,
)
from app.services.mail.adapter import FiltersRefused
from app.services.unsubscribe.forms import has_form, page_text, simple_form
from app.services.unsubscribe.judge import judge_page
from app.services.unsubscribe.links import (
    HTML_CAP,
    body_unsubscribe_link,
    host_of,
    parse_list_unsubscribe,
)
from app.services.unsubscribe.senders import SenderRow
from app.services.unsubscribe.web import RefusedAddress, SafeWeb, WebAnswer, WebTrouble

log = logging.getLogger(__name__)

NO_LINK_MSG = "This sender's mail has no unsubscribe link CleanSync can use."
NO_MODEL_MSG = "No model to check the page. Open it to finish."
MODEL_PAUSED_MSG = "The model is paused, so the page wasn't checked. Open it to finish."
NOT_CONFIRMED_MSG = "The page didn't say you were unsubscribed. Open it to finish."
FORM_NOT_SIMPLE_MSG = "The page needs you to finish it. Open it to finish."
MAILTO_ONLY_MSG = "Write to this address to unsubscribe, or turn on Allow Gmail filters."
FILTER_CAP_MSG = "Gmail allows 1,000 filters and this mailbox has {n}. Remove some in Gmail first."
KEEP_NEEDS_FILTERS_MSG = "Turn on Allow Gmail filters to remove CleanSync's filter for this sender."
# The notes never start with the outcome word: the job window shows "Unsubscribed (note)".
DONE_UNSUBSCRIBED = "Done by {how}."
HOW_ONE_CLICK = "the one-click request"
HOW_LINK = "opening the link"
HOW_FORM = "filling in the form"
DONE_FILTER = "Their new mail skips the Inbox."
DONE_KEPT = "CleanSync won't suggest this sender again."
ERROR_ANSWER_MSG = "{host} answered with an error ({status})."

FILTER_CAP = 990

UNSUBSCRIBED = UnsubscribeOutcome.UNSUBSCRIBED.value
AUTO_ARCHIVED = UnsubscribeOutcome.AUTO_ARCHIVED.value
KEPT = UnsubscribeOutcome.KEPT.value
NOT_CONFIRMED = UnsubscribeOutcome.NOT_CONFIRMED.value
NEEDS_YOU = UnsubscribeOutcome.NEEDS_YOU.value


@dataclass
class StepResult:
    outcome: str  # an UnsubscribeOutcome value
    method: str | None
    note: str
    link: str | None
    filter_id: str | None = None


class _LinkFailed(Exception):
    """This link didn't work; the message (host only) says why. Try the next link."""


def _ok(ans: WebAnswer) -> bool:
    return 200 <= ans.status < 300


def _check(ans: WebAnswer, link: str) -> None:
    if not _ok(ans):
        raise _LinkFailed(ERROR_ANSWER_MSG.format(host=host_of(link), status=ans.status))


def first_html(raw: bytes) -> str | None:
    """The first text/html part of a saved mail, or None."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                content = part.get_content()
                return content[: HTML_CAP * 4] if isinstance(content, str) else None
    except Exception:  # a broken mail has no link CleanSync can use
        return None
    return None


class FilterGate:
    """Gmail filters for one job: checks the permission, lists the filters once, and keeps
    the mailbox under Gmail's 1,000 filter limit."""

    def __init__(self, *, adapter, sf, bus, account_id: int) -> None:
        self.adapter = adapter
        self.sf = sf
        self.bus = bus
        self.account_id = account_id
        self._filters: list | None = None

    def _allowed(self) -> bool:
        with self.sf() as db:
            return allows_gmail_filters(db, self.account_id)

    def _lost(self) -> StepResult:
        with self.sf() as db:
            permission_lost(db, self.bus, self.account_id)
        return StepResult(NEEDS_YOU, None, LOST_MSG, None)

    async def auto_archive(self, address: str) -> StepResult:
        if not self._allowed():
            return StepResult(NEEDS_YOU, None, NOT_ALLOWED_MSG, None)
        try:
            if self._filters is None:
                self._filters = list(await self.adapter.list_filters())
            if len(self._filters) >= FILTER_CAP:
                return StepResult(
                    NEEDS_YOU, None, FILTER_CAP_MSG.format(n=f"{len(self._filters):,}"), None
                )
            fid = await self.adapter.create_filter(address)
        except FiltersRefused:
            return self._lost()
        self._filters.append(fid)
        return StepResult(AUTO_ARCHIVED, UnsubscribeMethod.FILTER.value, DONE_FILTER, None, fid)

    async def remove(self, filter_id: str) -> StepResult:
        if not self._allowed():
            return StepResult(NEEDS_YOU, None, KEEP_NEEDS_FILTERS_MSG, None)
        try:
            await self.adapter.delete_filter(filter_id)
        except FiltersRefused:
            return self._lost()
        if self._filters is not None:
            self._filters = [f for f in self._filters if getattr(f, "id", f) != filter_id]
        return StepResult(KEPT, None, DONE_KEPT, None)


class Ladder:
    def __init__(
        self,
        web: SafeWeb,
        ai,
        connections,
        read_eml: Callable[[int], bytes | None],
    ) -> None:
        self.web = web
        self.ai = ai
        self.connections = connections
        self.read_eml = read_eml

    def _body_link(self, mail_id: int) -> str | None:
        raw = self.read_eml(mail_id)
        if not raw:
            return None
        html = first_html(raw)
        return body_unsubscribe_link(html) if html else None

    async def unsubscribe(self, sender: SenderRow, *, filters: FilterGate) -> StepResult:
        links = parse_list_unsubscribe(sender.newest_unsubscribe)
        # The one-click request only when the sender says it takes one (RFC 8058), and only
        # for the header's links.
        one_click = "one-click" in (sender.newest_unsubscribe_post or "").lower()
        header_links = set(links.web)
        if not links.web:
            found = await asyncio.to_thread(self._body_link, sender.newest_mail_id)
            if found:
                links.web.append(found)
        if links.web:
            last_error = NO_LINK_MSG
            for link in links.web:
                try:
                    return await self._try_link(link, one_click=one_click and link in header_links)
                except (RefusedAddress, WebTrouble, _LinkFailed) as e:
                    last_error = str(e)
                    log.info("unsubscribe link on %s didn't work: %s", host_of(link), last_error)
            return StepResult(NOT_CONFIRMED, None, last_error, links.web[0])
        if links.mailto:
            res = await filters.auto_archive(sender.address)
            if res.outcome == NEEDS_YOU and res.note == NOT_ALLOWED_MSG:
                res.note = MAILTO_ONLY_MSG
            res.link = links.mailto[0]
            return res
        return StepResult(NOT_CONFIRMED, None, NO_LINK_MSG, None)

    async def _try_link(self, link: str, *, one_click: bool) -> StepResult:
        # (a) the one-click request. A POST answered after a redirect was turned into a GET
        # of another page, so it doesn't count.
        if one_click:
            ans = await self.web.request("POST", link, form={"List-Unsubscribe": "One-Click"})
            if _ok(ans) and ans.url == link:
                return StepResult(
                    UNSUBSCRIBED,
                    UnsubscribeMethod.ONE_CLICK.value,
                    DONE_UNSUBSCRIBED.format(how=HOW_ONE_CLICK),
                    link,
                )
        # (b) visit the link
        ans = await self.web.request("GET", link)
        _check(ans, link)
        if "html" not in ans.content_type.lower() or not has_form(ans.text):
            return StepResult(
                UNSUBSCRIBED,
                UnsubscribeMethod.LINK.value,
                DONE_UNSUBSCRIBED.format(how=HOW_LINK),
                link,
            )
        # (c) a simple form, checked by the model
        form = simple_form(ans.text, ans.url)
        if form is None:
            return StepResult(NOT_CONFIRMED, None, FORM_NOT_SIMPLE_MSG, link)
        if form.method.upper() == "POST":
            reply = await self.web.request("POST", form.action, form=dict(form.fields))
        else:
            target = urlsplit(form.action)._replace(query=urlencode(form.fields)).geturl()
            reply = await self.web.request("GET", target)
        _check(reply, form.action)
        verdict = await judge_page(
            self.ai, self.connections.target(AiTaskName.UNSUBSCRIBE), page_text(reply.text)
        )
        if verdict == "confirmed":
            return StepResult(
                UNSUBSCRIBED,
                UnsubscribeMethod.FORM.value,
                DONE_UNSUBSCRIBED.format(how=HOW_FORM),
                link,
            )
        note = {"no_model": NO_MODEL_MSG, "paused": MODEL_PAUSED_MSG}.get(
            verdict, NOT_CONFIRMED_MSG
        )
        return StepResult(NOT_CONFIRMED, None, note, link)
