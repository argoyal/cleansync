import type { ItemStatus, PairStatus } from "../api/types";
import { Icon, type IconName } from "./Icon";

const ITEM: Record<ItemStatus, { icon: IconName | null; label: string; color: string; spin?: boolean }> = {
  synced: { icon: "check_circle", label: "Synced", color: "text-ok" },
  syncing: { icon: "sync", label: "Syncing", color: "text-run", spin: true },
  upload: { icon: "arrow_upward", label: "Waiting to upload", color: "text-ink-2" },
  download: { icon: "arrow_downward", label: "Waiting to download", color: "text-ink-2" },
  conflict: { icon: "warning", label: "Conflict: two versions", color: "text-warn" },
  native: { icon: null, label: "Google file (lives in Google Drive)", color: "text-ink-2" },
  offloaded: { icon: "inventory_2", label: "Offloaded: only on this server", color: "text-ink-2" },
  unsynced: { icon: null, label: "Not synced", color: "text-ink-2" },
  unknown: { icon: null, label: "Not checked yet", color: "text-ink-2" },
};

/** The small mark a folder carries on its icon, or null when there is nothing to show. */
export function folderMark(status: ItemStatus): { icon: IconName; label: string; color: string; spin?: boolean } | null {
  // A green cloud when it's up to date; the spinning arrows only while a sync is running.
  if (status === "synced") return { icon: "cloud_done", label: ITEM.synced.label, color: "text-ok" };
  if (status === "unsynced") return { icon: "cloud_off", label: ITEM.unsynced.label, color: "text-ink-2" };
  const s = ITEM[status];
  return s.icon ? { icon: s.icon, label: s.label, color: s.color, spin: s.spin } : null;
}

export function itemStatusLabel(status: ItemStatus): string {
  return ITEM[status].label;
}

/** The small mark next to every file and folder. */
export function StatusBadge({ status }: { status: ItemStatus }) {
  const s = ITEM[status];
  if (status === "native") {
    return (
      <span
        role="img"
        aria-label={s.label}
        title={s.label}
        className="grid h-[18px] w-[18px] place-items-center rounded-full border border-ink-2/60 text-[10px] font-bold leading-none text-ink-2"
      >
        G
      </span>
    );
  }
  if (status === "unsynced") {
    // A visible word, not just a mark (spec §8). Screen readers get it from the item's status text.
    return (
      <span aria-hidden="true" className="shrink-0 rounded-full border border-line px-2 text-meta text-ink-2">
        {s.label}
      </span>
    );
  }
  if (!s.icon) return null;
  return (
    <span title={s.label} className={`inline-flex ${s.color}`}>
      <Icon name={s.icon} label={s.label} className={`text-[18px] ${s.spin ? "spin" : ""}`} />
    </span>
  );
}

const PAIR: Record<PairStatus, { label: string; dot: string; text: string }> = {
  NEW: { label: "Waiting for first sync", dot: "bg-warn", text: "text-warn" },
  HEALTHY: { label: "Healthy", dot: "bg-ok", text: "text-ok" },
  RUNNING: { label: "Syncing", dot: "bg-run", text: "text-run" },
  ATTENTION: { label: "Needs attention", dot: "bg-warn", text: "text-warn" },
  ERROR: { label: "Error", dot: "bg-err", text: "text-err" },
  PAUSED: { label: "Sync off", dot: "bg-paused", text: "text-paused" },
};

export function pairStatusLabel(status: PairStatus): string {
  return PAIR[status].label;
}

export function StatusDot({ status, className = "" }: { status: PairStatus; className?: string }) {
  return <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${PAIR[status].dot} ${className}`} />;
}

export function PairStatusPill({ status }: { status: PairStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-table font-medium ${PAIR[status].text}`}>
      <StatusDot status={status} />
      {PAIR[status].label}
    </span>
  );
}

export const ACCOUNT_DOT: Record<string, string> = {
  CONNECTED: "bg-ok",
  NEEDS_REAUTH: "bg-warn",
  DISCONNECTED: "bg-paused",
};
