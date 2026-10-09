import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { useBrowse, useOffloads, useStatus } from "../api/queries";
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
  return (
    <Dialog
      title="Delete these from Google Drive's trash for good?"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose} data-autofocus>
            Cancel
          </Button>
          <Button tone="danger" disabled={empty.isPending} onClick={() => empty.mutate()}>
            Empty from trash now
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

const under = (path: string, folder: string) => path.startsWith(`${folder}/`);

type Pick = {
  picked: string[];
  isPickable: (i: FileItem) => boolean;
  stateOf: (i: FileItem) => boolean | "mixed";
  toggle: (i: FileItem) => void;
};

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

function Folder({ accountId, path, pick, label }: { accountId: number; path: string; pick: Pick; label?: string }) {
  const browse = useBrowse(accountId, path);
  const [open, setOpen] = useState<string[]>([]);
  if (browse.isPending) return <p className="py-1 pl-10 text-ink-2">Loading…</p>;
  if (browse.isError) return <Notice tone="err">{friendlyError(browse.error)}</Notice>;
  if (browse.data.items.length === 0) return <p className="py-1 pl-10 text-ink-2">Empty folder</p>;
  return (
    <ul className={path ? "pl-6" : ""} aria-label={label}>
      {browse.data.items.map((i) => {
        const isOpen = open.includes(i.path);
        const noteId = `pick-note-${i.path}-${i.native ?? ""}`;
        const note = i.native ? "Stays on Drive" : i.status === "unsynced" ? "Not synced" : i.status === "offloaded" ? "Offloaded" : null;
        return (
          <li key={i.path + (i.native ?? "")}>
            <div className="flex min-h-9 items-center gap-2 py-0.5">
              {i.is_dir && i.local ? (
                <button
                  type="button"
                  aria-label={`${isOpen ? "Close" : "Open"} ${i.name}`}
                  aria-expanded={isOpen}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-ink-2 hover:bg-sunken"
                  onClick={() => setOpen((o) => (isOpen ? o.filter((x) => x !== i.path) : [...o, i.path]))}
                >
                  <Icon name={isOpen ? "keyboard_arrow_down" : "chevron_right"} />
                </button>
              ) : (
                <span className="w-7 shrink-0" />
              )}
              <TriBox
                label={`Select ${i.name}`}
                checked={pick.stateOf(i)}
                disabled={!pick.isPickable(i)}
                onToggle={() => pick.toggle(i)}
                describedBy={note ? noteId : undefined}
              />
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
            {isOpen && <Folder accountId={accountId} path={i.path} pick={pick} />}
          </li>
        );
      })}
    </ul>
  );
}

/** Spec §7.2a: pick any number of files and folders across synced folders, see the total, confirm once. */
export function OffloadPickPage() {
  const { accountId } = useCurrentAccount();
  const qc = useQueryClient();
  const oneWay = useOneWay();
  const stopped = useStatus().data?.sync_stopped ?? null;
  const [picked, setPicked] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<string[] | null>(null);

  // What could go: on this server, in a two-way synced folder, not a Google Doc, not offloaded
  // already, and not a synced folder's own top (offload what is inside it). The server checks
  // all of this again in the preview.
  const isPickable = (i: FileItem) =>
    i.local && i.pair_id !== null && !i.native && i.status !== "offloaded" && !i.pair_root && !oneWay(i);
  const listing = (path: string) => qc.getQueryData<Browse>(["browse", accountId, path])?.items;

  const stateOf = (i: FileItem): boolean | "mixed" => {
    if (!isPickable(i)) return false;
    if (picked.some((p) => p === i.path || under(i.path, p))) return true;
    return i.is_dir && picked.some((p) => under(p, i.path)) ? "mixed" : false;
  };

  const toggle = (i: FileItem) => {
    if (picked.includes(i.path)) {
      setPicked(picked.filter((p) => p !== i.path));
      return;
    }
    const above = picked.find((p) => under(i.path, p));
    if (above === undefined) {
      // Picking a folder covers everything in it, so drop anything picked inside it.
      setPicked([...picked.filter((p) => !under(p, i.path)), i.path]);
      return;
    }
    // Taking one item out of a picked folder: pick the rest of that folder, level by level.
    const next = picked.filter((p) => p !== above);
    let level = above;
    while (level !== i.path) {
      const children = listing(level);
      if (!children) return; // not on screen: nothing to split
      const step = children.find((c) => c.path === i.path || under(i.path, c.path));
      if (!step) return;
      next.push(...children.filter((c) => c !== step && isPickable(c)).map((c) => c.path));
      level = step.path;
    }
    setPicked(next);
  };

  const settled = useDebounced(picked, 300);
  const summary = useQuery({
    queryKey: ["offload-preview", accountId, settled],
    queryFn: () => api<OffloadPreview>("/api/offloads/preview", { method: "POST", body: { account_id: accountId, paths: settled } }),
    enabled: accountId !== null && settled.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });
  const sum = picked.length > 0 ? summary.data : undefined;
  const blocked = sum?.items.filter((x) => x.blocked).length ?? 0;
  const adding = settled !== picked || summary.isFetching;

  if (accountId === null) return null;
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
      <div className="rounded-card border border-line px-2 py-2">
        <Folder accountId={accountId} path="" pick={{ picked, isPickable, stateOf, toggle }} label="Your folders" />
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
