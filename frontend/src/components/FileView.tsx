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
  selected: string | null;
  onSelect: (path: string | null) => void;
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

  /** One Tab stop for the whole view; arrows, Home and End move between items. */
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
        if (target) p.onSelect(target.path);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        p.onOpen(item);
      } else if (e.key === " ") {
        e.preventDefault();
        p.onSelect(item.path);
      } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        e.preventDefault();
        p.onMenu(item, r.left + 24, r.top + 24);
      }
    },
  });

  const mouse = (item: FileItem) => ({
    onClick: () => p.onSelect(item.path),
    onDoubleClick: () => p.onOpen(item),
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault();
      p.onSelect(item.path);
      p.onMenu(item, e.clientX, e.clientY);
    },
  });

  const index = new Map(ordered.map((i, n) => [i, n]));
  const describe = (item: FileItem) => `${uid}-${index.get(item)}`;
  const status = (item: FileItem) => (
    <span id={describe(item)} className="sr-only">
      {itemStatusLabel(item.status)}
    </span>
  );
  const aria = (item: FileItem) => ({
    role: "button" as const,
    "data-nav": "",
    "data-path": item.path,
    "aria-label": item.name,
    "aria-describedby": describe(item),
    "aria-pressed": p.selected === item.path,
  });

  if (p.view === "list") {
    return (
      <div ref={root}>
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
                  p.selected === item.path ? "bg-accent-soft" : ""
                }`}
                {...mouse(item)}
              >
                <td className="py-1.5 pl-2">
                  <div className="flex items-center gap-3 rounded-ctl outline-none focus-visible:ring-2 focus-visible:ring-accent" {...aria(item)} {...nav(item)}>
                    <Icon name={iconFor(item)} className="text-[20px] text-ink-2" />
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
                  className={`flex h-12 items-center gap-3 pl-4 pr-11 ${frame(p.selected === item.path)}`}
                  {...aria(item)}
                  {...nav(item)}
                  {...mouse(item)}
                >
                  <Icon name="folder" className="text-[20px] text-ink-2" />
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
                <div className={`overflow-hidden ${frame(p.selected === item.path)}`} {...aria(item)} {...nav(item)} {...mouse(item)}>
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
    </div>
  );
}
