import type { IconName } from "../components/Icon";

export type SectionId = "files" | "mail" | "activity" | "recovery" | "settings";
export type Section = { id: SectionId; label: string; icon: IconName; to: string };

/** The rail, top to bottom; Settings sits at the bottom on its own (spec §9). */
export const SECTIONS: Section[] = [
  { id: "files", label: "Files", icon: "folder", to: "/drive" },
  { id: "mail", label: "Mail", icon: "mail", to: "/mail" },
  { id: "activity", label: "Activity", icon: "monitoring", to: "/activity" },
  { id: "recovery", label: "Recovery", icon: "history", to: "/recovery" },
  { id: "settings", label: "Settings", icon: "settings", to: "/settings" },
];

export function sectionFor(pathname: string): SectionId {
  if (/^\/mail(\/|$)/.test(pathname)) return "mail";
  if (/^\/(activity|storage)(\/|$)/.test(pathname)) return "activity";
  if (/^\/recovery(\/|$)/.test(pathname)) return "recovery";
  if (/^\/settings(\/|$)/.test(pathname)) return "settings";
  return "files";
}

/** Whole class names, so Tailwind finds them. The colours are tokens in index.css. */
export const SECTION_STYLE: Record<SectionId, { tile: string; soft: string }> = {
  files: { tile: "bg-sec-files text-sec-files-ink", soft: "bg-sec-files-soft text-sec-files-soft-ink" },
  mail: { tile: "bg-sec-mail text-sec-mail-ink", soft: "bg-sec-mail-soft text-sec-mail-soft-ink" },
  activity: { tile: "bg-sec-activity text-sec-activity-ink", soft: "bg-sec-activity-soft text-sec-activity-soft-ink" },
  recovery: { tile: "bg-sec-recovery text-sec-recovery-ink", soft: "bg-sec-recovery-soft text-sec-recovery-soft-ink" },
  settings: { tile: "bg-sec-settings text-sec-settings-ink", soft: "bg-sec-settings-soft text-sec-settings-soft-ink" },
};

/** What a screen reader hears for a badge on the rail. */
export const BADGE_TEXT: Partial<Record<SectionId, (n: number) => string>> = {
  files: (n) => `${n} open ${n === 1 ? "conflict" : "conflicts"}`,
  mail: (n) => `${n} ${n === 1 ? "mail" : "mails"} could not be backed up`,
};

const VIEW_KEYS = ["view", "label", "tab", "source"];

/** A panel link is active on its own path (or below it) with the same view, label, tab or source. */
export function linkActive(to: string, pathname: string, search: string): boolean {
  const [path, query = ""] = to.split("?");
  if (pathname !== path && !pathname.startsWith(`${path}/`)) return false;
  const want = new URLSearchParams(query);
  const have = new URLSearchParams(search);
  return VIEW_KEYS.every((k) => (want.get(k) ?? "") === (have.get(k) ?? ""));
}
