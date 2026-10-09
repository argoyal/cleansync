import { useId, useRef, useState } from "react";

import { thumbUrl } from "../api/queries";
import type { FileItem } from "../api/types";
import { formatBytes, formatWhen } from "../lib/format";
import { iconFor, parentOf } from "../lib/files";
import { Icon } from "./Icon";
import { itemStatusLabel, StatusBadge } from "./StatusBadge";

type Props = {
  items: FileItem[];
  accountId: number;
  view: "grid" | "list";
  /** Paths of the selected items. */
  selected: string[];
  /** "only" selects just this item, "toggle" adds or removes it (Ctrl/⌘), "range" extends from the last pick (Shift). */
  onSelect: (path: string, how: "only" | "toggle" | "range") => void;
  onOpen: (item: FileItem) => void;
  onMenu: (item: FileItem, x: number, y: number) => void;
  showFolder?: boolean;
};

function Thumb({ accountId, item }: { accountId: number; item: FileItem }) {
  const [failed, setFailed] = useState(false);
  if (item.thumb && !failed) {
    return (
      <img
        src={thumbUrl(accountId, item)}
        alt=""
        loading="lazy"
        className="h-full w-full object-cover"
        onError={() => setFailed(true)}
      />
    );
  }
  return <Icon name={iconFor(item)} className="text-[44px] text-ink-2/70" />;
}

const SYNCED_FOLDER = "Synced folder: kept in step with Google Drive.";
const KEYS_HELP =
  "Arrow keys move and select. Shift with an arrow selects a range. Ctrl or Command with an arrow moves without selecting. Space adds or removes the item from the selection. Enter opens it.";
const NOT_SYNCED_FOLDER = "Not synced: only on this server. Open its menu to start syncing it.";

/** What screen readers hear about an item: its sync state, plus what a folder's mark means. */
function describeItem(item: FileItem): string {
  if (item.is_dir && item.pair_root) return `${itemStatusLabel(item.status)}. ${SYNCED_FOLDER}`;
  if (item.is_dir && item.status === "unsynced") return NOT_SYNCED_FOLDER;
  return itemStatusLabel(item.status);
}

/** Folder icon with a mark: synced folder (a sync pair's own folder), or not synced. The title is the legend. */
function FolderIcon({ item, className }: { item: FileItem; className: string }) {
  const synced = !!item.pair_root;
  const notSynced = !synced && item.status === "unsynced";
  const title = synced
    ? "Synced folder: kept in step with Google Drive"
    : notSynced
      ? NOT_SYNCED_FOLDER
      : undefined;
  return (
    <span className="relative inline-flex shrink-0" title={title}>
      <Icon name="folder" className={className} />
      {synced && (
        <span className="absolute -bottom-1 -right-1 inline-flex rounded-full bg-surface">
          <Icon name="sync" label="Synced folder" className="text-[12px] text-ok" />
        </span>
      )}
      {notSynced && (
        <span aria-hidden="true" className="absolute -bottom-1 -right-1 inline-flex rounded-full bg-surface">
          <Icon name="cloud_off" className="text-[12px] text-ink-2" />
        </span>
      )}
    </span>
  );
}

type Nav = { onKeyDown: (e: React.KeyboardEvent) => void; onFocus: () => void; tabIndex: number };

function MoreButton({ item, p, className = "" }: { item: FileItem; p: Props; className?: string }) {
  return (
    <button
      type="button"
      aria-label={`More actions for ${item.name}`}
      className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-2 hover:bg-sunken ${className}`}
      onDoubleClick={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        p.onMenu(item, r.left, r.bottom + 4);
      }}
    >
      <Icon name="more_vert" />
    </button>
  );
}

/** Folders first, then files: a Drive-style grid with previews, or a dense list. */
export function FileView(p: Props) {
  const folders = p.items.filter((i) => i.is_dir);
  const files = p.items.filter((i) => !i.is_dir);
  const ordered = [...folders, ...files];
  const root = useRef<HTMLDivElement>(null);
  const uid = useId().replace(/:/g, "");
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const keyOf = (i: FileItem) => i.path + (i.native ?? "");
  const stop = ordered.some((i) => keyOf(i) === focusKey) ? focusKey : ordered[0] ? keyOf(ordered[0]) : null;

  /**
   * One Tab stop for the whole view; arrows, Home and End move between items and select the
   * one they land on. Shift with an arrow selects a range, Ctrl/⌘ with an arrow only moves,
   * Space adds or removes the focused item, Enter opens it.
   */
  const nav = (item: FileItem): Nav => ({
    tabIndex: keyOf(item) === stop ? 0 : -1,
    onFocus: () => setFocusKey(keyOf(item)),
    onKeyDown: (e) => {
      if (e.target !== e.currentTarget) return;
      const els = Array.from(root.current?.querySelectorAll<HTMLElement>("[data-nav]") ?? []);
      const at = els.indexOf(e.currentTarget as HTMLElement);
      let to = -1;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") to = Math.min(els.length - 1, at + 1);
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") to = Math.max(0, at - 1);
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = els.length - 1;
      if (to >= 0) {
        e.preventDefault();
        els[to]?.focus();
        const target = ordered[to];
        // Shift extends the selection; Ctrl/⌘ only moves, so Space can then add the item.
        if (target && !(e.ctrlKey || e.metaKey)) p.onSelect(target.path, e.shiftKey ? "range" : "only");
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        p.onOpen(item);
      } else if (e.key === " ") {
        e.preventDefault();
        p.onSelect(item.path, "toggle");
      } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        e.preventDefault();
        p.onMenu(item, r.left + 24, r.top + 24);
      }
    },
  });

  const mouse = (item: FileItem) => ({
    onClick: (e: React.MouseEvent) => p.onSelect(item.path, e.ctrlKey || e.metaKey ? "toggle" : e.shiftKey ? "range" : "only"),
    onDoubleClick: () => p.onOpen(item),
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault();
      // Right-click inside the selection acts on all of it; elsewhere it selects that one item.
      if (!p.selected.includes(item.path)) p.onSelect(item.path, "only");
      p.onMenu(item, e.clientX, e.clientY);
    },
  });

  const isSelected = (item: FileItem) => p.selected.includes(item.path);
  const index = new Map(ordered.map((i, n) => [i, n]));
  const describe = (item: FileItem) => `${uid}-${index.get(item)}`;
  const status = (item: FileItem) => (
    <span id={describe(item)} className="sr-only">
      {describeItem(item)}
    </span>
  );
  const aria = (item: FileItem) => ({
    role: "button" as const,
    "data-nav": "",
    "data-path": item.path,
    "aria-label": item.name,
    "aria-describedby": describe(item),
    "aria-pressed": isSelected(item),
  });

  if (p.view === "list") {
    return (
      <div ref={root}>
        <p className="sr-only">{KEYS_HELP}</p>
        <table className="w-full table-fixed text-table">
          <thead className="text-left text-meta text-ink-2">
            <tr className="border-b border-line">
              <th className="py-2 pl-2 font-medium">Name</th>
              {p.showFolder && <th className="w-1/4 py-2 font-medium max-md:hidden">Folder</th>}
              <th className="w-36 py-2 font-medium max-sm:hidden">Modified</th>
              <th className="w-24 py-2 text-right font-medium max-sm:hidden">Size</th>
              <th className="w-12" />
            </tr>
          </thead>
          <tbody>
            {ordered.map((item) => (
              <tr
                key={keyOf(item)}
                className={`cursor-default border-b border-line/60 hover:bg-sunken focus-within:bg-sunken ${
                  isSelected(item) ? "bg-accent-soft" : ""
                }`}
                {...mouse(item)}
              >
                <td className="py-1.5 pl-2">
                  <div className="flex items-center gap-3 rounded-ctl outline-none focus-visible:ring-2 focus-visible:ring-accent" {...aria(item)} {...nav(item)}>
                    {item.is_dir ? (
                      <FolderIcon item={item} className="text-[20px] text-ink-2" />
                    ) : (
                      <Icon name={iconFor(item)} className="text-[20px] text-ink-2" />
                    )}
                    <span className={`truncate ${item.local || item.native ? "" : "text-ink-2"}`}>{item.name}</span>
                    {item.starred && <Icon name="star" label="Starred" className="text-[16px] text-ink-2" />}
                    <StatusBadge status={item.status} />
                    {status(item)}
                  </div>
                </td>
                {p.showFolder && <td className="truncate text-ink-2 max-md:hidden">{parentOf(item.path) || "My Drive"}</td>}
                <td className="tabular text-ink-2 max-sm:hidden">{formatWhen(item.mtime)}</td>
                <td className="tabular text-right text-ink-2 max-sm:hidden">{item.is_dir ? "" : formatBytes(item.size)}</td>
                <td className="text-right">
                  <MoreButton item={item} p={p} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const frame = (selected: boolean) =>
    `rounded-card border outline-none focus-visible:ring-2 focus-visible:ring-accent ${
      selected ? "border-accent bg-accent-soft" : "border-line bg-sunken/50 hover:bg-sunken"
    }`;
  return (
    <div ref={root} className="space-y-6">
      {folders.length > 0 && (
        <section aria-label="Folders">
          <h2 className="mb-2 text-table font-semibold text-ink-2">Folders</h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            {folders.map((item) => (
              <div key={keyOf(item)} className="relative">
                <div
                  className={`flex h-12 items-center gap-3 pl-4 pr-11 ${frame(isSelected(item))}`}
                  {...aria(item)}
                  {...nav(item)}
                  {...mouse(item)}
                >
                  <FolderIcon item={item} className="text-[20px] text-ink-2" />
                  <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                  <StatusBadge status={item.status} />
                  {status(item)}
                </div>
                <MoreButton item={item} p={p} className="absolute right-1 top-2" />
              </div>
            ))}
          </div>
        </section>
      )}
      {files.length > 0 && (
        <section aria-label="Files">
          <h2 className="mb-2 text-table font-semibold text-ink-2">Files</h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            {files.map((item) => (
              <div key={keyOf(item)} className="relative [contain-intrinsic-size:auto_240px] [content-visibility:auto]">
                <div className={`overflow-hidden ${frame(isSelected(item))}`} {...aria(item)} {...nav(item)} {...mouse(item)}>
                  <div className="flex items-center gap-2 py-1.5 pl-3 pr-11">
                    <Icon name={iconFor(item)} className="text-[18px] text-ink-2" />
                    <span className="min-w-0 flex-1 truncate text-table font-medium">{item.name}</span>
                  </div>
                  <div className="mx-2 grid h-36 place-items-center overflow-hidden rounded-ctl bg-surface">
                    <Thumb accountId={p.accountId} item={item} />
                  </div>
                  <div className="flex items-center justify-between gap-2 px-3 py-2 text-meta text-ink-2">
                    <span className="tabular">{formatWhen(item.mtime)}</span>
                    <StatusBadge status={item.status} />
                    {status(item)}
                  </div>
                </div>
                <MoreButton item={item} p={p} className="absolute right-1 top-1" />
              </div>
            ))}
          </div>
        </section>
      )}
      <p className="sr-only">{KEYS_HELP}</p>
    </div>
  );
}
