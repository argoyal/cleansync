"""Deciding which rule a mail falls under (AI rules spec §5): remembered senders, then fixed
conditions in order, then one numbered question to the model. One rule per mail."""

from dataclasses import dataclass, field
from typing import Protocol

from app.models.enums import DecisionHow, RuleCombine
from app.services.rules.mailview import MailForModel, domain_of

ADDRESSED = ("direct", "cc", "list")


def _strs(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out = [str(v).strip().lower() for v in value if str(v).strip()]
    return tuple(dict.fromkeys(out))


def _flag(value) -> bool | None:
    return value if isinstance(value, bool) else None


@dataclass(frozen=True)
class Conditions:
    senders_in: tuple[str, ...] = ()
    senders_not_in: tuple[str, ...] = ()
    subject_contains: tuple[str, ...] = ()
    list_unsubscribe: bool | None = None
    calendar_invite: bool | None = None
    has_attachments: bool | None = None
    addressed: str | None = None

    @classmethod
    def from_json(cls, data: dict | None) -> "Conditions":
        d = data if isinstance(data, dict) else {}
        addressed = d.get("addressed")
        return cls(
            senders_in=_strs(d.get("senders_in")),
            senders_not_in=_strs(d.get("senders_not_in")),
            subject_contains=_strs(d.get("subject_contains")),
            list_unsubscribe=_flag(d.get("list_unsubscribe")),
            calendar_invite=_flag(d.get("calendar_invite")),
            has_attachments=_flag(d.get("has_attachments")),
            addressed=addressed if addressed in ADDRESSED else None,
        )

    def to_json(self) -> dict:
        out: dict = {}
        for name in ("senders_in", "senders_not_in", "subject_contains"):
            if getattr(self, name):
                out[name] = list(getattr(self, name))
        for name in ("list_unsubscribe", "calendar_invite", "has_attachments", "addressed"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        return out

    def is_empty(self) -> bool:
        return not self.to_json()


def _sender_in(sender: str, entries: tuple[str, ...]) -> bool:
    dom = domain_of(sender)
    for e in entries:
        if "@" in e:
            if sender == e:
                return True
        elif dom and (dom == e or dom.endswith("." + e)):
            return True
    return False


def fixed_match(c: Conditions, m: MailForModel) -> bool:
    if c.senders_in and not _sender_in(m.sender, c.senders_in):
        return False
    if c.senders_not_in and _sender_in(m.sender, c.senders_not_in):
        return False
    subject = m.subject.lower()
    if c.subject_contains and not any(s.lower() in subject for s in c.subject_contains):
        return False
    if c.list_unsubscribe is not None and m.list_unsubscribe != c.list_unsubscribe:
        return False
    if c.calendar_invite is not None and m.calendar_invite != c.calendar_invite:
        return False
    if c.has_attachments is not None and bool(m.attachments) != c.has_attachments:
        return False
    if c.addressed == "direct" and m.owner_in != "to":
        return False
    if c.addressed == "cc" and m.owner_in != "cc":
        return False
    if c.addressed == "list" and not (m.mailing_list or m.owner_in == "other"):
        return False
    return True


@dataclass(frozen=True)
class RuleView:
    id: int
    version: int
    name: str
    description: str
    conditions: Conditions
    combine: str
    actions: tuple[dict, ...] = field(default_factory=tuple)
    mode: str = "suggest"


@dataclass
class Choice:
    rule_id: int | None
    why: str
    model: str = ""
    took_ms: int = 0
    raw: str | None = None


class Chooser(Protocol):
    async def choose(
        self, rules: list[RuleView], mail: MailForModel, hints: list[str]
    ) -> Choice: ...


@dataclass
class Verdict:
    rule: RuleView | None
    how: str
    why: str = ""
    model: str = ""
    took_ms: int = 0
    raw: str | None = None


NO_MODEL_WHY = "No model is set up, so only fixed conditions and remembered senders were checked."


async def decide(
    rules: list[RuleView],
    mail: MailForModel,
    *,
    remembered: dict[str, int],
    chooser: Chooser | None,
    hints: list[str],
) -> Verdict:
    by_id = {r.id: r for r in rules}
    rid = remembered.get(mail.sender)
    if rid in by_id:
        return Verdict(
            by_id[rid],
            DecisionHow.REMEMBERED,
            f"You approved mail from {mail.sender} into this rule before.",
        )
    candidates: list[RuleView] = []
    for r in rules:
        hit = not r.conditions.is_empty() and fixed_match(r.conditions, mail)
        if hit and r.combine in (RuleCombine.FIXED_ONLY, RuleCombine.FIXED_DECIDES):
            return Verdict(r, DecisionHow.FIXED, "Its fixed conditions match.")
        if (
            r.combine == RuleCombine.AI_ONLY
            or (r.combine == RuleCombine.FIXED_DECIDES and not hit)
            or (r.combine == RuleCombine.FIXED_THEN_AI and hit)
        ):
            candidates.append(r)
    if not candidates:
        return Verdict(None, DecisionHow.NONE, "No rule fits.")
    if chooser is None:
        return Verdict(None, DecisionHow.NONE, NO_MODEL_WHY)
    choice = await chooser.choose(candidates, mail, hints)
    picked = by_id.get(choice.rule_id) if choice.rule_id is not None else None
    if picked is not None and picked in candidates:
        return Verdict(picked, DecisionHow.AI, choice.why, choice.model, choice.took_ms)
    return Verdict(
        None,
        DecisionHow.AI,
        choice.why or "No rule fits.",
        choice.model,
        choice.took_ms,
        choice.raw,
    )
