import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type KeyboardEvent, type Ref, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { browseOptions, useBrowse, useOffloads, useStatus } from "../api/queries";
import type { Browse, FileItem, Offload, OffloadPreview, OffloadState } from "../api/types";
import { FILE_QUERY_KEYS, RESTORE_NEWER_NOTE, RestoreDialog, useOneWay } from "../components/FileActions";
import { Icon } from "../components/Icon";
import { OffloadDialog } from "../components/OffloadDialog";
import { useToast } from "../components/Toasts";
import { Button, Dialog, Empty, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { formatBytes, formatWhen } from "../lib/format";

const LABEL: Record<OffloadState, string> = {
  PENDING: "Starting",
  DELETING: "Removing from Google Drive",
  VERIFYING: "Checking Drive's trash",
  ACTIVE: "Offloaded, in Drive's trash",
  ATTENTION: "Needs a look",
  RESTORING: "Going back to Google Drive",
  RESTORED: "Back on Google Drive",
};
const label = (o: Offload) => (o.state === "ACTIVE" && o.trash_emptied ? "Offloaded, deleted from Drive's trash" : LABEL[o.state]);
const restorable = (o: Offload) => (o.state === "ACTIVE" || o.state === "ATTENTION") && !o.busy;
const emptiable = (o: Offload) => o.state === "ACTIVE" && o.trash_verified && !o.trash_emptied && !o.busy;
const nameOf = (path: string) => path.split("/").pop() || path;
const files = (n: number) => `${n} ${n === 1 ? "file" : "files"}`;
const items = (n: number) => `${n} ${n === 1 ? "item" : "items"}`;
const NATIVE_NOTE = "Stays on Drive — not a file DriveSync can keep a copy of yet.";

/**
 * A checkbox that can also be partly checked ("mixed"), for a folder with only some of its
 * contents picked. A real button, so Space and Enter toggle it and screen readers hear the state.
 */
function TriBox({
  label,
  checked,
  disabled,
  onToggle,
  describedBy,
}: {
  label: string;
  checked: boolean | "mixed";
  disabled?: boolean;
  onToggle: () => void;
  describedBy?: string;
}) {
  const on = checked !== false;
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={onToggle}
      className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[4px] border-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40 ${
        on ? "border-accent bg-accent text-accent-ink" : "border-ink-2 bg-surface"
      }`}
    >
      {checked === true && <Icon name="check" className="text-[16px]" />}
      {checked === "mixed" && <Icon name="check_indeterminate_small" className="text-[16px]" />}
    </button>
  );
}

/** Spec §7.2a: every offload, with Restore to Drive (one or several) and Empty from trash now. */
export function OffloadedPage() {
  const { accountId } = useCurrentAccount();
  const offloads = useOffloads(accountId);
  const stopped = useStatus().data?.sync_stopped ?? null;
  const [checked, setChecked] = useState<number[]>([]);
  const [confirmMany, setConfirmMany] = useState<Offload[] | null>(null);
  const [confirmOne, setConfirmOne] = useState<Offload | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState<Offload | null>(null);
  const rows = offloads.data ?? [];
  const canRestore = rows.filter(restorable);
  // A row that stopped being restorable (it started going back, say) drops out of the selection.
  const selected = canRestore.filter((o) => checked.includes(o.id));
  const all: boolean | "mixed" = selected.length === 0 ? false : selected.length === canRestore.length ? true : "mixed";
  const toggle = (id: number) => setChecked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-title font-semibold">Offloaded</h1>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button icon="cloud_sync" disabled={selected.length === 0 || !!stopped} onClick={() => setConfirmMany(selected)}>
            Restore selected ({selected.length})
          </Button>
          <Link
            to="/offloaded/pick"
            aria-disabled={stopped ? "true" : undefined}
            tabIndex={stopped ? -1 : undefined}
            onClick={(e) => stopped && e.preventDefault()}
            className={`inline-flex h-9 items-center gap-2 rounded-ctl bg-accent px-3.5 font-medium text-accent-ink hover:bg-accent/90 ${
              stopped ? "pointer-events-none opacity-50" : ""
            }`}
          >
            <Icon name="inventory_2" />
            Offload files…
          </Link>
        </div>
      </div>
      <p className="mb-4 max-w-3xl text-ink-2">
        Offloaded files stay on this server and are no longer on Google Drive, so they don't use Drive space once Drive's trash
        is emptied. They don't sync. Restore to Drive puts them back.
      </p>
      {stopped && (
        <div className="mb-4">
          <Notice tone="err">Offload and restore are paused. {stopped}</Notice>
        </div>
      )}
      {offloads.isPending && accountId !== null && <p className="text-ink-2">Loading…</p>}
      {offloads.isError && <Notice tone="err">{friendlyError(offloads.error)}</Notice>}
      {offloads.data && rows.length === 0 && (
        <Empty icon="inventory_2" title="Nothing is offloaded">
          Right-click files or folders in My Drive and choose Offload…, or use Offload files….
        </Empty>
      )}
      {rows.length > 0 && (
        <table className="w-full table-fixed text-table">
          <thead className="text-left text-meta text-ink-2">
            <tr className="border-b border-line">
              <th className="w-10 py-2">
                <TriBox
                  label="Select all"
                  checked={all}
                  disabled={canRestore.length === 0}
                  onToggle={() => setChecked(all === true ? [] : canRestore.map((o) => o.id))}
                />
              </th>
              <th className="py-2 font-medium">Item</th>
              <th className="w-36 font-medium max-sm:hidden">Size</th>
              <th className="w-32 font-medium sm:w-52">State</th>
              <th className="w-40 text-right font-medium sm:w-80">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className="border-b border-line/60 align-top">
                <td className="py-3">
                  <TriBox
                    label={`Select ${nameOf(o.path)}`}
                    checked={checked.includes(o.id) && restorable(o)}
                    disabled={!restorable(o)}
                    onToggle={() => toggle(o.id)}
                  />
                </td>
                <td className="py-2">
                  <p className="flex items-center gap-2 font-medium">
                    <Icon name={o.is_dir ? "folder" : "draft"} className="text-ink-2" />
                    <span className="break-all">{o.path}</span>
                  </p>
                  <p className="text-meta text-ink-2">
                    {o.pair_name}, offloaded {formatWhen(o.created_at)}
                  </p>
                  {o.note && <p className="mt-1 text-meta text-ink">{o.note}</p>}
                </td>
                <td className="py-2 tabular text-ink-2 max-sm:hidden">
                  {files(o.file_count)}, {formatBytes(o.total_bytes)}
                </td>
                <td className="py-2">{o.busy ? "Emptying Drive's trash…" : label(o)}</td>
                <td className="py-2 text-right">
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button
                      aria-label={`Restore to Drive: ${nameOf(o.path)}`}
                      disabled={!restorable(o) || !!stopped}
                      onClick={() => setConfirmOne(o)}
                    >
                      Restore to Drive
                    </Button>
                    <Button
                      tone="quiet"
                      aria-label={`Empty from trash now: ${nameOf(o.path)}`}
                      disabled={!emptiable(o) || !!stopped}
                      onClick={() => setConfirmEmpty(o)}
                    >
                      Empty from trash now
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {confirmOne && <RestoreDialog offload={confirmOne} onClose={() => setConfirmOne(null)} />}
      {confirmMany && (
        <RestoreMany
          offloads={confirmMany}
          onClose={() => setConfirmMany(null)}
          onDone={() => {
            setChecked([]);
            setConfirmMany(null);
          }}
        />
      )}
      {confirmEmpty && <EmptyTrashDialog offload={confirmEmpty} onClose={() => setConfirmEmpty(null)} />}
    </div>
  );
}

type Failure = { offload: Offload; message: string };

/** One confirm for several offloads; they are restored one after another, then one message. */
function RestoreMany({ offloads, onClose, onDone }: { offloads: Offload[]; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<Failure[] | null>(null);
  const run = async () => {
    setBusy(true);
    const out: Failure[] = [];
    for (const o of offloads) {
      try {
        await api<Offload>(`/api/offloads/${o.id}/restore`, { method: "POST" });
      } catch (e) {
        out.push({ offload: o, message: friendlyError(e) });
      }
    }
    [...FILE_QUERY_KEYS, "offloads", "recovery"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    setBusy(false);
    if (out.length === 0) {
      toast(`${items(offloads.length)} ${offloads.length === 1 ? "is" : "are"} going back to Google Drive with the next sync.`);
      onDone();
    } else {
      setFailed(out);
    }
  };
  const title = `Restore ${items(offloads.length)} to Drive?`;

  if (failed) {
    const ok = offloads.length - failed.length;
    return (
      <Dialog
        key="results"
        title={title}
        onClose={onDone}
        footer={
          <Button tone="primary" data-autofocus onClick={onDone}>
            Close
          </Button>
        }
      >
        <div aria-live="polite" className="space-y-3">
          <p>
            {ok} of {offloads.length} {ok === 1 ? "is" : "are"} going back to Google Drive with the next sync. These were not
            restored:
          </p>
          <ul aria-label="Not restored" className="max-h-80 space-y-2 overflow-y-auto">
            {failed.map((f) => (
              <li key={f.offload.id} className="rounded-ctl border border-line px-3 py-2">
                <p className="break-all font-medium">{f.offload.path}</p>
                <p className="text-table text-err">{f.message}</p>
              </li>
            ))}
          </ul>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      key="confirm"
      title={title}
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button tone="primary" data-autofocus disabled={busy} onClick={run}>
            {busy ? "Restoring…" : "Restore to Drive"}
          </Button>
        </>
      }
    >
      <p>These sync again: the next sync uploads them to Google Drive. Nothing on this server changes.</p>
      <ul className="max-h-60 list-disc overflow-y-auto pl-5">
        {offloads.map((o) => (
          <li key={o.id} className="break-all">
            {o.path} <span className="text-ink-2">({files(o.file_count)})</span>
          </li>
        ))}
      </ul>
      <p className="text-ink-2">{RESTORE_NEWER_NOTE}</p>
    </Dialog>
  );
}

/** Spec §7.2: its own confirm. Permanent, and only this offload's files. */
function EmptyTrashDialog({ offload, onClose }: { offload: Offload; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const empty = useMutation({
    mutationFn: () => api<Offload>(`/api/offloads/${offload.id}/empty-trash`, { method: "POST" }),
    onSuccess: () => {
      ["offloads", "storage", "status"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast("Checking the copy on this server, then deleting them from Drive's trash.");
      onClose();
    },
  });
  // While the request is on its way the dialog stays, so its answer (or refusal) is seen.
  const close = empty.isPending ? () => undefined : onClose;
  return (
    <Dialog
      title="Delete these from Google Drive's trash for good?"
      onClose={close}
      footer={
        <>
          <Button onClick={close} disabled={empty.isPending} data-autofocus>
            Cancel
          </Button>
          <Button tone="danger" disabled={empty.isPending} onClick={() => empty.mutate()}>
            {empty.isPending ? "Emptying…" : "Empty from trash now"}
          </Button>
        </>
      }
    >
      <p>
        The {files(offload.file_count)} offloaded from <strong className="break-all">{offload.path}</strong> are deleted from
        Google Drive's trash for good, which frees their space now. DriveSync first checks that the copy on this server is still
        complete. Nothing else in the trash is touched.
      </p>
      <p className="font-medium">This can't be undone on Google Drive. The copy on this server stays.</p>
      {empty.isError && <Notice tone="err">{friendlyError(empty.error)}</Notice>}
    </Dialog>
  );
}

// ---- Offload files…: a tree of the account's folders with checkboxes -----------------------
//
// An ARIA tree (role tree / treeitem / group): one Tab stop (roving tabindex), Up/Down move,
// Right opens a folder or steps into it, Left closes it or steps out, Home/End, Space picks,
// Enter opens or closes. Each item says checked, not checked or partly checked ("mixed").

const under = (path: string, folder: string) => path.startsWith(`${folder}/`);
const keyOf = (i: FileItem) => i.path + (i.native ? `#${i.native}` : "");
const parentOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
const canOpen = (i: FileItem) => i.is_dir && i.local;

type Pick = {
  isPickable: (i: FileItem) => boolean;
  stateOf: (i: FileItem) => boolean | "mixed";
  toggle: (i: FileItem) => void;
  open: string[];
  setOpen: (path: string, open: boolean) => void;
  focus: string | null;
  setFocus: (key: string) => void;
};

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

function noteFor(i: FileItem, pickable: boolean, oneWay: boolean): string | null {
  if (i.native) return "Stays on Drive";
  if (i.status === "unsynced" || (!i.pair_id && !pickable)) return "Not synced";
  if (i.status === "offloaded") return "Offloaded";
  if (i.pair_root) return "Synced folder: pick what's inside it";
  if (oneWay) return "Only receives changes from Google Drive";
  if (!i.local) return "Only on Google Drive";
  return null;
}

function Folder({
  accountId,
  path,
  level,
  pick,
  oneWay,
  tree,
}: {
  accountId: number;
  path: string;
  level: number;
  pick: Pick;
  oneWay: (i: FileItem) => boolean;
  tree?: { label: string; onKeyDown: (e: KeyboardEvent<HTMLUListElement>) => void; ref: Ref<HTMLUListElement> };
}) {
  const browse = useBrowse(accountId, path);
  if (browse.isPending) return <p className="py-1 pl-10 text-ink-2">Loading…</p>;
  if (browse.isError) return <Notice tone="err">{friendlyError(browse.error)}</Notice>;
  if (browse.data.items.length === 0) return <p className="py-1 pl-10 text-ink-2">Empty folder</p>;
  const list = browse.data.items;
  return (
    <ul
      role={tree ? "tree" : "group"}
      aria-label={tree?.label}
      aria-multiselectable={tree ? true : undefined}
      ref={tree?.ref}
      onKeyDown={tree?.onKeyDown}
      className={tree ? "" : "pl-6"}
    >
      {list.map((i, n) => {
        const key = keyOf(i);
        const isOpen = canOpen(i) && pick.open.includes(i.path);
        const pickable = pick.isPickable(i);
        const note = noteFor(i, pickable, oneWay(i));
        const noteId = `pick-note-${key}`;
        // The one Tab stop: the item last focused, or the first item at the top.
        const stop = pick.focus === key || (pick.focus === null && level === 1 && n === 0);
        return (
          <li
            key={key}
            role="treeitem"
            aria-label={i.name}
            aria-level={level}
            aria-checked={pick.stateOf(i)}
            aria-expanded={canOpen(i) ? isOpen : undefined}
            aria-disabled={!pickable && !canOpen(i) ? true : undefined}
            aria-describedby={note ? noteId : undefined}
            data-key={key}
            data-path={i.path}
            data-native={i.native ?? ""}
            tabIndex={stop ? 0 : -1}
            onFocus={(e) => e.target === e.currentTarget && pick.setFocus(key)}
            className="outline-none [&:focus-visible>div]:ring-2 [&:focus-visible>div]:ring-accent"
          >
            <div
              data-row
              className={`flex min-h-9 items-center gap-2 rounded-ctl py-0.5 ${pickable ? "cursor-pointer hover:bg-sunken" : ""}`}
              onClick={() => pickable && pick.toggle(i)}
            >
              {canOpen(i) ? (
                <span
                  data-arrow
                  aria-hidden="true"
                  title={isOpen ? "Close" : "Open"}
                  className="grid h-7 w-7 shrink-0 cursor-pointer place-items-center rounded-full text-ink-2 hover:bg-line"
                  onClick={(e) => {
                    e.stopPropagation();
                    pick.setOpen(i.path, !isOpen);
                  }}
                >
                  <Icon name={isOpen ? "keyboard_arrow_down" : "chevron_right"} />
                </span>
              ) : (
                <span className="w-7 shrink-0" />
              )}
              <CheckMark state={pick.stateOf(i)} dim={!pickable} />
              <Icon name={i.is_dir ? "folder" : "draft"} className="text-ink-2" />
              <span className="min-w-0 truncate">{i.name}</span>
              {note && (
                <span id={noteId} title={i.native ? NATIVE_NOTE : undefined} className="shrink-0 text-meta text-ink-2">
                  {note}
                </span>
              )}
              {!i.is_dir && i.size !== null && i.local && !note && (
                <span className="ml-auto shrink-0 pr-2 text-meta tabular text-ink-2">{formatBytes(i.size)}</span>
              )}
            </div>
            {isOpen && <Folder accountId={accountId} path={i.path} level={level + 1} pick={pick} oneWay={oneWay} />}
          </li>
        );
      })}
    </ul>
  );
}

/** The look of a tree item's checkbox; the state itself is the item's aria-checked. */
function CheckMark({ state, dim }: { state: boolean | "mixed"; dim: boolean }) {
  const on = state !== false;
  return (
    <span
      aria-hidden="true"
      className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[4px] border-2 ${
        on ? "border-accent bg-accent text-accent-ink" : "border-ink-2 bg-surface"
      } ${dim ? "opacity-40" : ""}`}
    >
      {state === true && <Icon name="check" className="text-[16px]" />}
      {state === "mixed" && <Icon name="check_indeterminate_small" className="text-[16px]" />}
    </span>
  );
}

/** Spec §7.2a: pick any number of files and folders across synced folders, see the total, confirm once. */
export function OffloadPickPage() {
  const { accountId } = useCurrentAccount();
  const qc = useQueryClient();
  const oneWay = useOneWay();
  const stopped = useStatus().data?.sync_stopped ?? null;
  const [picked, setPicked] = useState<string[]>([]);
  const pickedNow = useRef(picked);
  pickedNow.current = picked;
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const [open, setOpenList] = useState<string[]>([]);
  const [focus, setFocus] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const treeRef = useRef<HTMLUListElement>(null);

  // What could go: on this server, in a two-way synced folder, not a Google Doc, not offloaded
  // already, and not a synced folder's own top (offload what is inside it). The server checks
  // all of this again in the preview.
  const isPickable = (i: FileItem) =>
    i.local && i.pair_id !== null && !i.native && i.status !== "offloaded" && !i.pair_root && !oneWay(i);

  const stateOf = (i: FileItem): boolean | "mixed" => {
    const inside = i.is_dir && picked.some((p) => under(p, i.path));
    if (!isPickable(i)) return inside ? "mixed" : false; // e.g. a folder that isn't synced, above a synced one
    if (picked.some((p) => p === i.path || under(i.path, p))) return true;
    return inside ? "mixed" : false;
  };

  const toggle = async (i: FileItem) => {
    setProblem(null);
    const before = pickedNow.current;
    if (before.includes(i.path)) {
      setPicked(before.filter((p) => p !== i.path));
      return;
    }
    const above = before.find((p) => under(i.path, p));
    if (above === undefined) {
      // Picking a folder covers everything in it, so drop anything picked inside it.
      setPicked([...before.filter((p) => !under(p, i.path)), i.path]);
      return;
    }
    // Taking one item out of a picked folder: pick the rest of that folder, level by level.
    // A listing that isn't loaded is loaded first.
    const next = before.filter((p) => p !== above);
    let level = above;
    try {
      while (level !== i.path) {
        const listing = await qc.fetchQuery({ ...browseOptions(accountId, level), staleTime: Infinity });
        const step = listing.items.find((c) => c.path === i.path || under(i.path, c.path));
        if (!step) throw new Error(`it is no longer in ${nameOf(level)}`);
        next.push(...listing.items.filter((c) => c !== step && isPickable(c)).map((c) => c.path));
        level = step.path;
      }
    } catch (e) {
      setProblem(`Couldn't leave out ${i.name}: ${friendlyError(e)}. ${nameOf(above)} is still picked as a whole.`);
      return;
    }
    // Only if nothing else changed the picks while a listing loaded.
    if (pickedNow.current === before) setPicked(next);
    else setProblem(`Couldn't leave out ${i.name}: the picks changed meanwhile. Try again.`);
  };

  const setOpen = (path: string, isOpen: boolean) => {
    setOpenList((o) => (isOpen ? [...o, path] : o.filter((x) => x !== path && !under(x, path))));
    // Closing a folder that holds the focus stop moves the stop to the folder.
    if (!isOpen && focus && under(focus, path)) setFocus(path);
  };

  // If the focused item went away (moved, or a listing changed), the first item takes the stop.
  // Runs after every render on purpose: any listing can change it. It only ever sets null once.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (focus !== null && treeRef.current && !treeRef.current.querySelector('[role="treeitem"][tabindex="0"]')) setFocus(null);
  });

  const itemOf = (el: HTMLElement): FileItem | undefined =>
    qc
      .getQueryData<Browse>(["browse", accountId, parentOf(el.dataset.path ?? "")])
      ?.items.find((c) => c.path === el.dataset.path && (c.native ?? "") === el.dataset.native);

  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[role="treeitem"]');
    if (!el || e.altKey || e.ctrlKey || e.metaKey) return;
    const all = Array.from(treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []);
    const at = all.indexOf(el);
    const path = el.dataset.path ?? "";
    const expandable = el.hasAttribute("aria-expanded");
    const isOpen = el.getAttribute("aria-expanded") === "true";
    const go = (target: HTMLElement | undefined) => target?.focus();
    switch (e.key) {
      case "ArrowDown":
        go(all[at + 1]);
        break;
      case "ArrowUp":
        go(all[at - 1]);
        break;
      case "Home":
        go(all[0]);
        break;
      case "End":
        go(all[all.length - 1]);
        break;
      case "ArrowRight":
        if (!expandable) return;
        if (!isOpen) setOpen(path, true);
        else if (all[at + 1] && parentOf(all[at + 1].dataset.path ?? "") === path) go(all[at + 1]);
        break;
      case "ArrowLeft":
        if (isOpen) setOpen(path, false);
        else go(all.find((x) => x.dataset.path === parentOf(path) && !x.dataset.native));
        break;
      case "Enter":
        if (!expandable) return;
        setOpen(path, !isOpen);
        break;
      case " ": {
        const i = itemOf(el);
        if (i && isPickable(i)) void toggle(i);
        break;
      }
      default:
        return;
    }
    e.preventDefault();
  };

  const settled = useDebounced(picked, 300);
  const summary = useQuery({
    queryKey: ["offload-preview", accountId, settled],
    // The signal cancels a total that a newer pick made out of date.
    queryFn: ({ signal }) =>
      api<OffloadPreview>("/api/offloads/preview", { method: "POST", body: { account_id: accountId, paths: settled }, signal }),
    enabled: accountId !== null && settled.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const sum = picked.length > 0 ? summary.data : undefined;
  const blocked = sum?.items.filter((x) => x.blocked).length ?? 0;
  const adding = settled !== picked || summary.isFetching;

  if (accountId === null) return null;
  const pick: Pick = { isPickable, stateOf, toggle: (i) => void toggle(i), open, setOpen, focus, setFocus };
  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Link to="/offloaded" className="text-accent hover:underline">
          Offloaded
        </Link>
        <Icon name="chevron_right" className="text-ink-2" />
        <h1 className="text-title font-semibold">Offload files</h1>
      </div>
      <p className="mb-4 max-w-3xl text-ink-2">
        Pick files and folders in your synced folders. You see what will be removed from Google Drive, and the space freed,
        before anything happens. Google Docs, Sheets and Slides stay on Drive.
      </p>
      <p className="sr-only" id="pick-keys">
        Up and down arrows move, right opens a folder, left closes it, Space picks or unpicks.
      </p>
      {stopped && (
        <div className="mb-4">
          <Notice tone="err">Offload is paused. {stopped}</Notice>
        </div>
      )}
      <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-card border border-line bg-surface px-4 py-2">
        <p aria-live="polite" className="min-w-0 flex-1 text-table">
          {picked.length === 0 ? (
            "Nothing picked yet."
          ) : (
            <>
              <span className="font-medium">{items(picked.length)} picked</span>
              {sum && (
                <span className="tabular">
                  {": "}
                  {files(sum.total_files)}, {formatBytes(sum.total_bytes)} freed on Google Drive once Drive's trash is emptied
                </span>
              )}
              {blocked > 0 && (
                <span className="text-err">
                  {". "}
                  {blocked} can't be offloaded; the next step says why
                </span>
              )}
              {adding && <span className="text-ink-2"> (adding up…)</span>}
              {summary.isError && <span className="text-err"> ({friendlyError(summary.error)})</span>}
            </>
          )}
        </p>
        {picked.length > 0 && (
          <Button tone="quiet" onClick={() => setPicked([])}>
            Clear
          </Button>
        )}
        <Button tone="primary" icon="inventory_2" disabled={picked.length === 0 || !!stopped} onClick={() => setConfirm(picked)}>
          Offload selected ({picked.length})…
        </Button>
      </div>
      {problem && (
        <div className="mb-3">
          <Notice tone="err">{problem}</Notice>
        </div>
      )}
      <div className="rounded-card border border-line px-2 py-2" aria-describedby="pick-keys">
        <Folder
          accountId={accountId}
          path=""
          level={1}
          pick={pick}
          oneWay={oneWay}
          tree={{ label: "Your folders", onKeyDown, ref: treeRef }}
        />
      </div>
      {confirm && (
        <OffloadDialog
          accountId={accountId}
          paths={confirm}
          onClose={(started) => {
            setConfirm(null);
            if (started) setPicked([]);
          }}
        />
      )}
    </div>
  );
}
