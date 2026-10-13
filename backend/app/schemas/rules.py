# backend/app/schemas/rules.py
from pydantic import BaseModel, Field

from app.schemas.ai import PauseOut
from app.schemas.common import UtcDatetime


class AccountBody(BaseModel):
    account_id: int


class RuleFields(BaseModel):
    name: str = Field(max_length=200)
    description: str = Field("", max_length=2000)
    conditions: dict = Field(default_factory=dict)
    combine: str = "ai_only"
    actions: list[dict] = Field(default_factory=list, max_length=10)
    mode: str = "suggest"
    enabled: bool = True


class RuleBody(RuleFields):
    account_id: int


class SenderOut(BaseModel):
    id: int
    sender: str
    approvals: int
    remembered: bool


class RuleOut(RuleFields):
    id: int
    position: int
    version: int
    decided_7d: int
    senders: list[SenderOut]
    blocked: str | None = None


class IdsBody(AccountBody):
    ids: list[int] = Field(max_length=5000)


class ApproveBody(IdsBody):
    confirm: int | None = None  # the count of Delete forever suggestions, typed


class NamesBody(AccountBody):
    names: list[str] = Field(max_length=20)


class YamlBody(AccountBody):
    yaml: str = Field(max_length=500_000)


class CopyBody(AccountBody):
    to_account_id: int


class SentenceBody(AccountBody):
    sentence: str = Field(min_length=1, max_length=1000)


class MailIdsBody(AccountBody):
    message_ids: list[int] = Field(min_length=1, max_length=50)


class DraftOut(BaseModel):
    name: str
    description: str
    conditions: dict
    combine: str
    actions: list[dict]
    notes: list[str]


class TryBody(AccountBody):
    rule: RuleFields
    rule_id: int | None = None
    message_ids: list[int] | None = Field(None, max_length=50)
    q: str = Field("", max_length=1000)


class TryOut(BaseModel):
    message_id: int
    subject: str
    sender: str
    rule_name: str | None
    this_rule: bool
    how: str
    why: str


class SortingBody(AccountBody):
    enabled: bool


class SortingOut(BaseModel):
    enabled: bool
    waiting: int
    sorted_today: int
    suggested: int
    failing_since: UtcDatetime | None
    last_error: str | None
    warning: bool
    model_set: bool
    paused: PauseOut | None = None


class DecisionOut(BaseModel):
    id: int
    message_id: int
    thread_id: str
    subject: str
    from_addr: str
    date: UtcDatetime
    rule_id: int | None
    rule_name: str
    how: str
    why: str
    model: str
    status: str
    reason: str | None
    actions: list[dict]
    moved_back: bool
    created_at: UtcDatetime


class CorrectBody(AccountBody):
    rule_id: int | None


class JobStartBody(AccountBody):
    """Running rules spec §8: the rules, and one of the Inbox (`inbox`, one rule), a search
    (`view`, `label`, a non-empty `q`), a pick-all (the same with `pick_all`), or a pick by
    hand (`message_ids`, or `thread_ids` with `view` and `label`)."""

    rule_ids: list[int] = Field(default_factory=list, max_length=100)
    inbox: bool = False
    view: str = Field("all", max_length=32)
    label: str | None = Field(None, max_length=128)
    q: str = Field("", max_length=1000)
    pick_all: bool = False
    message_ids: list[int] | None = Field(None, max_length=20000)
    thread_ids: list[str] | None = Field(None, max_length=5000)


class JobRunBody(AccountBody):
    """Approving the match list (running rules spec §8). `left_out`: the unticked matches.
    `only`: after Pick all was unticked, the matches ticked by hand; every other one is left
    out. `confirm` is needed only when picked mail would be offloaded or deleted forever."""

    left_out: list[int] = Field(default_factory=list, max_length=20000)
    only: list[int] | None = Field(None, max_length=20000)
    confirm: int | None = None


class MatchOut(BaseModel):
    id: int
    message_id: int
    thread_id: str
    subject: str
    from_addr: str
    date: UtcDatetime
    rule_id: int | None
    rule_name: str
    actions: list[dict]
    why: str


class JobPreviewOut(BaseModel):
    job_id: int
    state: str
    total: int
    decided: int
    planned: int
    per_rule: list[dict]
    no_rule: int
    applied: int
    skipped: int
    failed: int
    reason: str | None
    rule_ids: list[int] = []
    view: str = ""
    q: str = ""
    filter_text: str = ""
    left_out: int = 0
    skip_reasons: list[dict] = []
    paused: PauseOut | None = None
