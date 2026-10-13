import { actionKind, describeAction, type RuleAction, type RunScope } from "../api/rules";
import { hasSearch, viewKind } from "./mail";
import { mails } from "./plural";

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Actions that take mail out of Gmail: approving them needs the typed count (running rules spec §5). */
export const HEAVY_KINDS: readonly string[] = ["offload", "delete_forever"];

/** One rule of a run, with how many of its matches are picked. */
export type RulePick = { actions: RuleAction[]; picked: number };

/** One action done to `n` mails, as the approve button says it. */
export function actionText(a: RuleAction, n: number): string {
  if (a.kind === "label") return `Add the label ${a.label} to ${mails(n)}`;
  switch (actionKind(a)) {
    case "archive":
      return `Archive ${mails(n)}`;
    case "star":
      return `Star ${mails(n)}`;
    case "mark_read":
      return `Mark ${mails(n)} read`;
    case "label":
      return ""; // handled above
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
    const heavy = r.actions.map(actionKind).filter((k) => HEAVY_KINDS.includes(k));
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
  if (viewKind(view) === "gmail" && (hasSearch(q) || label !== "" || view !== "all")) return { view, ...(label ? { label } : {}), q, pick_all: true };
  return { inbox: true };
}

/** Why a rule can't run now (running rules spec §3), or null. Any rule that isn't
 * fixed-conditions-only may ask the model. `modelSet` is null while Sorting is still loading:
 * such a rule waits, it isn't treated as ready. */
export function runBlock(rule: { enabled: boolean; combine: string }, modelSet: boolean | null): string | null {
  if (!rule.enabled) return "Turn the rule on to run it.";
  if (rule.combine !== "fixed_only" && modelSet === null) return "Checking whether a model is set up…";
  if (rule.combine !== "fixed_only" && !modelSet) return "Set up a model in Settings → Models.";
  return null;
}

/** The rule picker's first line (running rules spec §4): what the ticked rules will run on. */
export function runOnText(on: { threads: number } | { total: number | undefined; q: string; place: string }): string {
  if ("threads" in on) return `Run on: ${on.threads.toLocaleString()} picked ${on.threads === 1 ? "thread" : "threads"}`;
  const n = on.total === undefined ? "the mails" : mails(on.total);
  return hasSearch(on.q) ? `Run on: ${n} matching ${on.q.trim()}` : `Run on: ${n} in ${on.place}`;
}
