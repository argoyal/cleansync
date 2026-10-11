import type { IconName } from "../components/Icon";

export const MAIL_VIEWS: { id: string; label: string; icon: IconName }[] = [
  { id: "all", label: "All mail", icon: "mail" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "sent", label: "Sent", icon: "send" },
  { id: "starred", label: "Starred", icon: "star" },
  { id: "important", label: "Important", icon: "label_important" },
  { id: "offloaded", label: "Offloaded", icon: "inventory_2" },
  { id: "deleted", label: "Deleted in Gmail", icon: "delete" },
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
