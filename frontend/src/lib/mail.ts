import type { IconName } from "../components/Icon";
import { formatDay } from "./format";

export const MAIL_VIEWS: { id: string; label: string; icon: IconName }[] = [
  { id: "all", label: "All mail", icon: "mail" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "suggested", label: "Suggested", icon: "auto_awesome" },
  { id: "sent", label: "Sent", icon: "send" },
  { id: "starred", label: "Starred", icon: "star" },
  { id: "important", label: "Important", icon: "label_important" },
  { id: "offloaded", label: "Offloaded", icon: "inventory_2" },
  { id: "deleted", label: "Deleted in Gmail", icon: "delete" },
  { id: "archive", label: "Local archive", icon: "archive" },
];

export function viewTitle(view: string, labelName?: string | null): string {
  if (labelName) return labelName;
  return MAIL_VIEWS.find((v) => v.id === view)?.label ?? "All mail";
}

/** Whole class names so Tailwind finds them; the colours are tokens in index.css. */
export const LABEL_DOTS = ["bg-label-1", "bg-label-2", "bg-label-3", "bg-label-4", "bg-label-5", "bg-label-6"];

export function labelDot(id: string): string {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return LABEL_DOTS[h % LABEL_DOTS.length];
}

export function mailHref(p: { view?: string; label?: string; q?: string; thread?: string }): string {
  const params = new URLSearchParams();
  if (p.view && p.view !== "all") params.set("view", p.view);
  if (p.label) params.set("label", p.label);
  if (p.q) params.set("q", p.q);
  if (p.thread) params.set("thread", p.thread);
  const s = params.toString();
  return s ? `/mail?${s}` : "/mail";
}

const quoted = (v: string) => (/\s/.test(v) ? `"${v}"` : v);
const RUN_RULES_VIEWS = ["all", "inbox"];

/** What "Run rules on…" starts from for the list on screen, or null for a view the rules can't
 * take (Sent, Suggested, the Local archive…). A label isn't a view the rules know: it goes into
 * the search, with the view "all". `labelName` is the label's name when known. */
export function runRulesStart(view: string, label: string, labelName: string | null | undefined, q: string): { q: string; view: string } | null {
  if (label) return { q: [`label:${quoted(labelName ?? label)}`, q].filter(Boolean).join(" "), view: "all" };
  return RUN_RULES_VIEWS.includes(view) ? { q, view } : null;
}

export const OFFLOAD_STATE_TEXT: Record<string, string> = {
  WAITING: "Waiting",
  RUNNING: "Offloading",
  DONE: "Done",
  DONE_WITH_SKIPS: "Done, some skipped",
  FAILED: "Stopped",
};

/** Where the space of an offload comes back: Gmail empties its own trash after 30 days. */
export function trashLine(o: { trash_until: string; google_emptied: boolean }): string {
  if (o.google_emptied) return "Gmail has emptied its trash.";
  return `In Gmail's trash until about ${formatDay(o.trash_until)}.`;
}
