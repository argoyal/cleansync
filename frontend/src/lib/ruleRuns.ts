import { describeAction, type RuleAction, type RunScope } from "../api/rules";
import { viewKind } from "./mail";

/** "1 mail", "1,284 mails". */
export const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Actions that take mail out of Gmail: approving them needs the typed count (running rules spec §5). */
export const HEAVY_KINDS: readonly string[] = ["offload", "delete_forever"];

/** One rule of a run, with how many of its matches are picked. */
export type RulePick = { actions: RuleAction[]; picked: number };

/** One action done to `n` mails, as the approve button says it. */
export function actionText(a: RuleAction, n: number): string {
  switch (a.kind) {
    case "archive":
      return `Archive ${mails(n)}`;
    case "star":
      return `Star ${mails(n)}`;
    case "mark_read":
      return `Mark ${mails(n)} read`;
    case "label":
      return `Add the label ${a.label} to ${mails(n)}`;
    case "offload":
      return `Offload ${mails(n)}`;
    case "delete_forever":
      return `Delete ${mails(n)} forever`;
  }
}

/** The approve button (spec §5): the action and the count when the picked mails all belong to
 * one rule with one action, else "Apply N changes" (one change per mail). */
export function approveText(rules: RulePick[]): string {
  const live = rules.filter((r) => r.picked > 0);
  const one = live.length === 1 ? live[0] : live.length === 0 && rules.length === 1 ? rules[0] : null;
  if (one && one.actions.length === 1) return actionText(one.actions[0], one.picked);
  const n = live.reduce((sum, r) => sum + r.picked, 0);
  return `Apply ${n.toLocaleString()} ${n === 1 ? "change" : "changes"}`;
}

/** The match list's header for a run of one rule. */
export function wouldText(rule: string, actions: RuleAction[], n: number): string {
  if (actions.length === 1) return `${rule} would ${lowerFirst(actionText(actions[0], n))}.`;
  return `${rule} would change ${mails(n)}: ${actions.map(describeAction).join(", ")}.`;
}

/** One line of the header for a run of several rules: "Receipts · label Receipts · 30". */
export function ruleLine(rule: string, actions: RuleAction[], n: number): string {
  return `${rule} · ${actions.map((a) => lowerFirst(describeAction(a))).join(", ")} · ${n.toLocaleString()}`;
}

/** How many picked mails would be offloaded or deleted forever, and by which actions. */
export function heavyKinds(rules: RulePick[]): { count: number; kinds: Set<string> } {
  const kinds = new Set<string>();
  let count = 0;
  for (const r of rules) {
    const heavy = r.actions.map((a) => a.kind).filter((k) => HEAVY_KINDS.includes(k));
    if (heavy.length === 0 || r.picked === 0) continue;
    count += r.picked;
    heavy.forEach((k) => kinds.add(k));
  }
  return { count, kinds };
}

/** The label of the typed-count field. */
export function confirmLabel(kinds: Set<string>, n: number): string {
  const what =
    kinds.has("offload") && kinds.has("delete_forever")
      ? "offload or delete forever these mails"
      : kinds.has("delete_forever")
        ? "delete these mails forever"
        : "offload these mails";
  return `Type ${n.toLocaleString()} to ${what}`;
}

/** What "Run it on older mail…" covers after Make a rule…: the one sender's mail anywhere in
 * Gmail; otherwise the list on screen as a pick-all; a list rules can't take (Suggested,
 * Offloaded, Deleted in Gmail, or All mail with no search or label) falls back to the Inbox. */
export function olderMailScope(senders: string[], view: string, label: string, q: string): RunScope {
  if (senders.length === 1) return { view: "all", q: `from:${senders[0]}` };
  if (viewKind(view) === "gmail" && (q.trim() !== "" || label !== "" || view !== "all")) return { view, ...(label ? { label } : {}), q, pick_all: true };
  return { inbox: true };
}
