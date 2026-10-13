"""Rules for one mailbox (AI rules spec §5): checked on save, versioned, ordered, with starter
rules, YAML export/import and copy to another mailbox."""

from dataclasses import dataclass, field

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Rule, RuleDecision, RuleVersion
from app.models.enums import DecisionStatus, RuleCombine, RuleMode
from app.services.rules.apply import is_system_label
from app.services.rules.match import Conditions, RuleView

ACTION_KINDS = ("archive", "label", "mark_read", "star", "offload")
# Listed in the rule form, but it needs the full Gmail permission (round 3 of 0.5.0).
NOT_YET_KINDS = {
    "delete_forever": "Delete forever needs full Gmail access (coming in this release).",
}
# Names older files still use: a 0.4.0 export says "delete" for Offload.
OLD_ACTION_NAMES = {"delete": "offload"}
STANDS_ALONE = ("offload",)


def action_kind(kind: str) -> str:
    return OLD_ACTION_NAMES.get(kind, kind)


SNAPSHOT = ("name", "description", "conditions", "combine", "actions", "mode")
MAX_NAME, MAX_DESCRIPTION, MAX_LABEL = 80, 1000, 100
MAX_RULES_IN_FILE = 100


class RuleProblem(ValueError):
    pass


class RuleNotFound(LookupError):
    pass


@dataclass
class RuleIn:
    name: str
    description: str = ""
    conditions: dict = field(default_factory=dict)
    combine: str = RuleCombine.AI_ONLY
    actions: list[dict] = field(default_factory=list)
    mode: str = RuleMode.SUGGEST
    enabled: bool = True


def clean_actions(actions: list[dict]) -> list[dict]:
    if not isinstance(actions, list):
        raise RuleProblem("Pick at least one action.")
    out: list[dict] = []
    for a in actions:
        if not isinstance(a, dict):
            raise RuleProblem("Each action needs a kind, such as archive or label.")
        kind = str(a.get("kind") or "")
        if kind in NOT_YET_KINDS:
            raise RuleProblem(NOT_YET_KINDS[kind])
        if kind not in ACTION_KINDS:
            raise RuleProblem(f"{kind!r} isn't an action a rule can take.")
        if kind == "label":
            label = " ".join(str(a.get("label") or "").split())[:MAX_LABEL]
            if not label:
                raise RuleProblem("Give the label a name.")
            if is_system_label(label):
                raise RuleProblem(f"{label!r} is one of Gmail's own labels. Pick another name.")
            item = {"kind": "label", "label": label}
        else:
            item = {"kind": kind}
        if item not in out:
            out.append(item)
    if not out:
        raise RuleProblem("Pick at least one action.")
    if any(a["kind"] in STANDS_ALONE for a in out) and len(out) > 1:
        raise RuleProblem("Offload can't be combined with other actions.")
    return out


def _clean(data: RuleIn) -> dict:
    name = " ".join(str(data.name or "").split())[:MAX_NAME]
    if not name:
        raise RuleProblem("Give the rule a name.")
    if data.combine not in tuple(RuleCombine):
        raise RuleProblem("Unknown way to combine conditions.")
    if data.mode not in tuple(RuleMode):
        raise RuleProblem("Unknown mode.")
    conditions = Conditions.from_json(data.conditions).to_json()
    description = str(data.description or "").strip()[:MAX_DESCRIPTION]
    if data.combine != RuleCombine.AI_ONLY and not conditions:
        raise RuleProblem("Add at least one fixed condition, or let the AI decide alone.")
    if data.combine != RuleCombine.FIXED_ONLY and not description:
        raise RuleProblem(
            "Add a description: one sentence on which mail this rule is for; the AI reads it."
        )
    return {
        "name": name,
        "description": description,
        "conditions": conditions,
        "combine": str(data.combine),
        "actions": clean_actions(data.actions),
        "mode": str(data.mode),
    }


def _snapshot(rule: Rule) -> dict:
    return {k: getattr(rule, k) for k in SNAPSHOT}


def _save_version(db: Session, rule: Rule, saved_by: str) -> None:
    db.add(
        RuleVersion(
            rule_id=rule.id, version=rule.version, snapshot=_snapshot(rule), saved_by=saved_by
        )
    )


def get_rule(db: Session, account_id: int, rule_id: int) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.account_id != account_id:
        raise RuleNotFound("That rule no longer exists.")
    return rule


def _unique_name(db: Session, account_id: int, name: str) -> str:
    taken = set(db.scalars(select(Rule.name).where(Rule.account_id == account_id)))
    if name not in taken:
        return name
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[: MAX_NAME - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


def create_rule(db: Session, account_id: int, data: RuleIn, saved_by: str) -> Rule:
    fields = _clean(data)
    last = db.scalar(select(func.max(Rule.position)).where(Rule.account_id == account_id))
    rule = Rule(
        account_id=account_id,
        position=(last + 1) if last is not None else 0,
        enabled=bool(data.enabled),
        version=1,
        **fields,
    )
    db.add(rule)
    db.flush()
    _save_version(db, rule, saved_by)
    db.commit()
    return rule


def update_rule(db: Session, account_id: int, rule_id: int, data: RuleIn, saved_by: str) -> Rule:
    rule = get_rule(db, account_id, rule_id)
    fields = _clean(data)
    rule.enabled = bool(data.enabled)
    if fields != _snapshot(rule):
        for k, v in fields.items():
            setattr(rule, k, v)
        rule.version += 1
        _save_version(db, rule, saved_by)
    db.commit()
    return rule


def delete_rule(db: Session, account_id: int, rule_id: int) -> None:
    rule = get_rule(db, account_id, rule_id)
    for d in db.scalars(
        select(RuleDecision).where(
            RuleDecision.rule_id == rule.id,
            RuleDecision.status.in_([DecisionStatus.SUGGESTED, DecisionStatus.PLANNED]),
        )
    ):
        d.status, d.reason = DecisionStatus.WITHDRAWN, "The rule was deleted."
    db.delete(rule)
    db.commit()


def reorder(db: Session, account_id: int, ids: list[int]) -> None:
    rules = {r.id: r for r in db.scalars(select(Rule).where(Rule.account_id == account_id))}
    if sorted(ids) != sorted(rules):
        raise RuleProblem("The list of rules changed meanwhile. Reload and try again.")
    for pos, rid in enumerate(ids):
        rules[rid].position = pos
    db.commit()


def view_of(rule: Rule) -> RuleView:
    return RuleView(
        id=rule.id,
        version=rule.version,
        name=rule.name,
        description=rule.description,
        conditions=Conditions.from_json(rule.conditions),
        combine=rule.combine,
        actions=tuple(rule.actions or ()),
        mode=rule.mode,
    )


def rule_views(
    db: Session, account_id: int, enabled_only: bool = True, rule_ids: list[int] | None = None
) -> list[RuleView]:
    """The mailbox's rules in their order. `rule_ids` keeps only those (a run's own rules)."""
    q = select(Rule).where(Rule.account_id == account_id)
    if enabled_only:
        q = q.where(Rule.enabled.is_(True))
    if rule_ids is not None:
        q = q.where(Rule.id.in_(rule_ids))
    return [view_of(r) for r in db.scalars(q.order_by(Rule.position, Rule.id))]


def _label_archive(label: str) -> list[dict]:
    return [{"kind": "label", "label": label}, {"kind": "archive"}]


STARTERS: dict[str, RuleIn] = {
    "Newsletters": RuleIn(
        "Newsletters",
        "Newsletters, digests and blog updates I signed up for.",
        {"list_unsubscribe": True},
        RuleCombine.FIXED_THEN_AI,
        _label_archive("Newsletters"),
    ),
    "Marketing": RuleIn(
        "Marketing",
        "Promotions, sales, offers and product announcements from companies.",
        {"list_unsubscribe": True},
        RuleCombine.FIXED_THEN_AI,
        _label_archive("Marketing"),
    ),
    "Receipts": RuleIn(
        "Receipts",
        "Receipts, invoices, order confirmations and payment notices.",
        {},
        RuleCombine.AI_ONLY,
        [{"kind": "label", "label": "Receipts"}],
    ),
    "Notifications": RuleIn(
        "Notifications",
        "Automatic notices from apps and services: alerts, sign-in notices, status updates, "
        "social media notifications.",
        {},
        RuleCombine.AI_ONLY,
        _label_archive("Notifications"),
    ),
    "Cold outreach": RuleIn(
        "Cold outreach",
        "Sales pitches and cold emails from people I have never written to.",
        {},
        RuleCombine.AI_ONLY,
        _label_archive("Cold outreach"),
    ),
    "Calendar": RuleIn(
        "Calendar",
        "Calendar invitations and updates.",
        {"calendar_invite": True},
        RuleCombine.FIXED_ONLY,
        [{"kind": "label", "label": "Calendar"}],
    ),
}


def add_starters(db: Session, account_id: int, names: list[str], saved_by: str) -> list[Rule]:
    have = set(db.scalars(select(Rule.name).where(Rule.account_id == account_id)))
    created: list[Rule] = []
    for n in names:
        if n in STARTERS and n not in have:
            created.append(create_rule(db, account_id, STARTERS[n], saved_by))
            have.add(n)
    return created


def export_yaml(db: Session, account_id: int) -> str:
    rules = db.scalars(select(Rule).where(Rule.account_id == account_id).order_by(Rule.position))
    items = [{**_snapshot(r), "enabled": r.enabled} for r in rules]
    return yaml.safe_dump(
        {"cleansync_rules": 1, "rules": items}, sort_keys=False, allow_unicode=True
    )


def _from_item(item: dict) -> RuleIn:
    fields = {k: item[k] for k in (*SNAPSHOT, "enabled") if k in item}
    fields.setdefault("name", "")
    if isinstance(fields.get("actions"), list):  # an older file's action names
        fields["actions"] = [
            {**a, "kind": action_kind(str(a.get("kind") or ""))} if isinstance(a, dict) else a
            for a in fields["actions"]
        ]
    data = RuleIn(**fields)
    data.name = " ".join(str(data.name or "").split())
    return data


def _create_from(db: Session, account_id: int, item: dict, saved_by: str) -> Rule:
    data = _from_item(item)
    data.name = _unique_name(db, account_id, data.name)
    return create_rule(db, account_id, data, saved_by)


def import_yaml(db: Session, account_id: int, text: str, saved_by: str) -> list[Rule]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        raise RuleProblem("That file isn't readable YAML.") from None
    items = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise RuleProblem("That file has no list of rules.")
    if len(items) > MAX_RULES_IN_FILE:
        raise RuleProblem(
            f"That file has more than {MAX_RULES_IN_FILE} rules. Split it and import the parts."
        )
    for i in items:  # check all before saving any
        _clean(_from_item(i))
    return [_create_from(db, account_id, i, saved_by) for i in items]


def copy_rules(db: Session, from_account: int, to_account: int, saved_by: str) -> int:
    if from_account == to_account:
        raise RuleProblem("Pick another mailbox to copy the rules to.")
    rules = list(
        db.scalars(select(Rule).where(Rule.account_id == from_account).order_by(Rule.position))
    )
    for r in rules:
        _create_from(db, to_account, {**_snapshot(r), "enabled": r.enabled}, saved_by)
    return len(rules)
