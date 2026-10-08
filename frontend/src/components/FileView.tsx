import { useState } from "react";

import { thumbUrl } from "../api/queries";
import type { FileItem } from "../api/types";
import { formatBytes, formatWhen } from "../lib/format";
import { iconFor, parentOf } from "../lib/files";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";

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

function handlers(item: FileItem, p: Props) {
  return {
    onClick: () => p.onSelect(item.path),
    onDoubleClick: () => p.onOpen(item),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.target !== e.currentTarget) return; // keys meant for the ⋮ button
      if (e.key === "Enter") p.onOpen(item);
      if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        e.preventDefault();
        p.onMenu(item, r.left + 24, r.top + 24);
      }
    },
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault();
      p.onSelect(item.path);
      p.onMenu(item, e.clientX, e.clientY);
    },
  };
}

function MoreButton({ item, p }: { item: FileItem; p: Props }) {
  return (
    <button
      type="button"
      aria-label={`More actions for ${item.name}`}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-2 hover:bg-sunken"
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
  if (p.view === "list") {
    return (
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
          {[...folders, ...files].map((item) => (
            <tr
              key={item.path + (item.native ?? "")}
              tabIndex={0}
              aria-selected={p.selected === item.path}
              className={`cursor-default border-b border-line/60 outline-none hover:bg-sunken focus:bg-sunken ${
                p.selected === item.path ? "bg-accent-soft" : ""
              }`}
              {...handlers(item, p)}
            >
              <td className="py-1.5 pl-2">
                <span className="flex items-center gap-3">
                  <Icon name={iconFor(item)} className="text-[20px] text-ink-2" />
                  <span className={`truncate ${item.local || item.native ? "" : "text-ink-2"}`}>{item.name}</span>
                  {item.starred && <Icon name="star" label="Starred" className="text-[16px] text-ink-2" />}
                  <StatusBadge status={item.status} />
                </span>
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
    );
  }
  return (
    <div className="space-y-6">
      {folders.length > 0 && (
        <section aria-label="Folders">
          <h2 className="mb-2 text-table font-semibold text-ink-2">Folders</h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
            {folders.map((item) => (
              <div
                key={item.path}
                role="button"
                tabIndex={0}
                aria-label={item.name}
                aria-pressed={p.selected === item.path}
                className={`flex h-12 items-center gap-3 rounded-card border pl-4 pr-1 outline-none ${
                  p.selected === item.path ? "border-accent bg-accent-soft" : "border-line bg-sunken/50 hover:bg-sunken"
                }`}
                {...handlers(item, p)}
              >
                <Icon name="folder" className="text-[20px] text-ink-2" />
                <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                <StatusBadge status={item.status} />
                <MoreButton item={item} p={p} />
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
              <div
                key={item.path + (item.native ?? "")}
                role="button"
                tabIndex={0}
                aria-label={item.name}
                aria-pressed={p.selected === item.path}
                className={`[contain-intrinsic-size:auto_240px] [content-visibility:auto] overflow-hidden rounded-card border outline-none ${
                  p.selected === item.path ? "border-accent bg-accent-soft" : "border-line bg-sunken/50 hover:bg-sunken"
                }`}
                {...handlers(item, p)}
              >
                <div className="flex items-center gap-2 py-1.5 pl-3 pr-1">
                  <Icon name={iconFor(item)} className="text-[18px] text-ink-2" />
                  <span className="min-w-0 flex-1 truncate text-table font-medium">{item.name}</span>
                  <MoreButton item={item} p={p} />
                </div>
                <div className="mx-2 grid h-36 place-items-center overflow-hidden rounded-ctl bg-surface">
                  <Thumb accountId={p.accountId} item={item} />
                </div>
                <div className="flex items-center justify-between gap-2 px-3 py-2 text-meta text-ink-2">
                  <span className="tabular">{formatWhen(item.mtime)}</span>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
