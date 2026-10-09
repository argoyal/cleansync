import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../api/client";
import { useBrowse } from "../api/queries";
import type { FileItem } from "../api/types";
import { useFileActions } from "../components/FileActions";
import { FileView } from "../components/FileView";
import { Icon } from "../components/Icon";
import { PairStatusPill } from "../components/StatusBadge";
import { Empty, IconButton, Menu, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { NOT_SYNCED_NOTE, ONE_WAY_NOTE } from "../lib/files";

const VIEW_KEY = "drivesync.view";

export function useViewMode(): ["grid" | "list", (v: "grid" | "list") => void] {
  const [view, setView] = useState<"grid" | "list">(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "grid" || saved === "list") return saved;
    } catch {
      // ignore: default below
    }
    return window.innerWidth < 640 ? "list" : "grid";
  });
  const set = (v: "grid" | "list") => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // not remembered in a private window
    }
  };
  return [view, set];
}

export function ViewToggle({ view, onChange }: { view: "grid" | "list"; onChange: (v: "grid" | "list") => void }) {
  return view === "grid" ? (
    <IconButton icon="view_list" label="Show as list" onClick={() => onChange("list")} />
  ) : (
    <IconButton icon="grid_view" label="Show as grid" onClick={() => onChange("grid")} />
  );
}

/** A file list with selection, the right-click menu and the action dialogs wired up. */
export function FileArea({ items, showFolder = false, view }: { items: FileItem[]; showFolder?: boolean; view: "grid" | "list" }) {
  const { accountId } = useCurrentAccount();
  const actions = useFileActions(accountId);
  const [selected, setSelected] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ item: FileItem; x: number; y: number; opener: HTMLElement | null } | null>(null);
  // The Delete key listener is added once and reads the latest values from here.
  const latest = useRef({ items, selected, askDelete: actions.askDelete });
  latest.current = { items, selected, askDelete: actions.askDelete };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Delete") return;
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable], [role=dialog], [role=menu]")) return;
      const { items, selected, askDelete } = latest.current;
      const path = target.closest<HTMLElement>("[data-path]")?.dataset.path ?? selected;
      const item = items.find((i) => i.path === path);
      if (item?.local) askDelete([item]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (accountId === null) return null;
  return (
    <>
      <FileView
        items={items}
        accountId={accountId}
        view={view}
        selected={selected}
        onSelect={setSelected}
        onOpen={actions.open}
        onMenu={(item, x, y) => setMenu({ item, x, y, opener: document.activeElement as HTMLElement | null })}
        showFolder={showFolder}
      />
      {menu && (
        <Menu
          key={`${menu.item.path}:${menu.x}:${menu.y}`}
          label={`Actions for ${menu.item.name}`}
          items={actions.menuFor(menu.item)}
          x={menu.x}
          y={menu.y}
          opener={menu.opener}
          onClose={() => setMenu(null)}
        />
      )}
      {actions.dialogs}
    </>
  );
}

export function Breadcrumbs({ path }: { path: string }) {
  const parts = path ? path.split("/") : [];
  return (
    <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-0.5 text-title">
      <Link to="/drive" className="rounded-full px-2 py-1 hover:bg-sunken">
        My Drive
      </Link>
      {parts.map((part, i) => {
        const to = parts.slice(0, i + 1).join("/");
        const last = i === parts.length - 1;
        return (
          <span key={to} className="flex min-w-0 items-center gap-0.5">
            <Icon name="chevron_right" className="text-ink-2" />
            {last ? (
              <span aria-current="page" className="truncate px-2 py-1 font-semibold">
                {part}
              </span>
            ) : (
              <Link to={`/drive?path=${encodeURIComponent(to)}`} className="truncate rounded-full px-2 py-1 hover:bg-sunken">
                {part}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}

export function DrivePage() {
  const [params] = useSearchParams();
  const path = params.get("path") ?? "";
  const { accountId, account } = useCurrentAccount();
  const browse = useBrowse(accountId, path);
  const [view, setView] = useViewMode();
  if (!account) {
    return (
      <Empty icon="person_add" title="No Google account yet">
        <Link to="/setup" className="font-semibold text-accent">
          Connect one to start
        </Link>
      </Empty>
    );
  }
  const pair = browse.data?.pair;
  const oneWay = pair?.direction === "REMOTE_TO_LOCAL";
  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex items-center gap-3">
        <Breadcrumbs path={path} />
        <div className="ml-auto">
          <ViewToggle view={view} onChange={setView} />
        </div>
      </div>
      {pair && pair.status !== "HEALTHY" && (
        <div className="mb-4 flex flex-wrap items-center gap-3 text-table">
          <PairStatusPill status={pair.status} />
          <Link to={`/activity/${pair.id}`} className="font-semibold text-accent">
            {pair.initialized ? "Open sync details" : "Finish setting up this folder"}
          </Link>
        </div>
      )}
      {account.status === "NEEDS_REAUTH" && (
        <div className="mb-4">
          <Notice tone="warn">
            Google asked to sign in again for {account.email}. Syncing is paused until you{" "}
            <Link to="/settings?tab=accounts" className="font-semibold underline">
              reconnect it
            </Link>
            .
          </Notice>
        </div>
      )}
      {oneWay && (
        <div className="mb-4">
          <Notice tone="run">{ONE_WAY_NOTE}</Notice>
        </div>
      )}
      {browse.data && !browse.data.pair && (
        <div className="mb-4">
          <Notice tone="run">{NOT_SYNCED_NOTE}</Notice>
        </div>
      )}
      {browse.isPending && <p className="text-ink-2">Loading</p>}
      {browse.isError && <Notice tone="err">{friendlyError(browse.error)}</Notice>}
      {browse.data && browse.data.items.length === 0 && (
        <Empty icon="folder" title="This folder is empty">
          {oneWay
            ? "Files added on Google Drive appear here after the next sync."
            : !browse.data.pair
              ? NOT_SYNCED_NOTE
              : "Use New to add a folder or upload files."}
        </Empty>
      )}
      {browse.data && browse.data.items.length > 0 && <FileArea items={browse.data.items} view={view} />}
    </div>
  );
}
