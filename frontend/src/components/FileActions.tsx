import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api, friendlyError } from "../api/client";
import { fileUrl, zipUrl } from "../api/queries";
import type { FileItem } from "../api/types";
import { isOffice, opensInBrowser, parentOf } from "../lib/files";
import { FolderPicker } from "./FolderPicker";
import { useToast } from "./Toasts";
import { Button, Dialog, Field, inputClass, type MenuItem, Notice } from "./ui";

export const FILE_QUERY_KEYS = ["browse", "recent", "starred", "search", "storage", "status"];

export function useInvalidateFiles() {
  const qc = useQueryClient();
  return () => FILE_QUERY_KEYS.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

function startDownload(href: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

type DialogState = { kind: "rename"; item: FileItem } | { kind: "move"; items: FileItem[] } | { kind: "delete"; items: FileItem[] } | null;

/** Open, download and the right-click actions for files in one account, plus their dialogs. */
export function useFileActions(accountId: number | null) {
  const navigate = useNavigate();
  const toast = useToast();
  const [dialog, setDialog] = useState<DialogState>(null);

  const qc = useQueryClient();
  const syncNow = (pairId: number | null) => {
    if (pairId === null) return;
    api(`/api/sync-pairs/${pairId}/sync`, { method: "POST" })
      .then(() => {
        toast("Sync started.");
        ["pairs", "status"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      })
      .catch((e) => toast(friendlyError(e), { tone: "error" }));
  };

  const download = (item: FileItem) => {
    if (accountId === null) return;
    if (!item.local) {
      toast("This is only on Google Drive so far. It arrives with the next sync.");
      return;
    }
    startDownload(item.is_dir ? zipUrl(accountId, item.path) : fileUrl(accountId, item.path, true));
  };

  const openInGoogle = (item: FileItem) => {
    if (item.google_url) {
      // Only ever open web links we expect; anything else is ignored.
      if (item.google_url.startsWith("https://")) window.open(item.google_url, "_blank", "noopener,noreferrer");
    } else if (item.native) {
      toast("This Google file has no link yet. Try again after the next sync.");
    } else {
      toast("It opens in Google after the next sync.", {
        action: item.pair_id !== null ? { label: "Sync now", run: () => syncNow(item.pair_id) } : undefined,
      });
    }
  };

  const open = (item: FileItem) => {
    if (accountId === null) return;
    if (item.is_dir) {
      if (item.local) navigate(`/drive?path=${encodeURIComponent(item.path)}`);
      else toast("This folder is only on Google Drive so far. It arrives with the next sync.");
      return;
    }
    if (item.native || isOffice(item)) return openInGoogle(item);
    if (!item.local) return download(item);
    if (opensInBrowser(item)) window.open(fileUrl(accountId, item.path), "_blank", "noopener,noreferrer");
    else download(item);
  };

  const menuFor = (item: FileItem): MenuItem[] => {
    const local = item.local;
    const items: MenuItem[] = [
      { label: "Open", icon: "open_in_new", onSelect: () => open(item) },
    ];
    if (!item.is_dir && (item.native || isOffice(item) || item.google_url)) {
      items.push({ label: "Open in Google", icon: "open_in_new", onSelect: () => openInGoogle(item), disabled: !item.google_url && !isOffice(item) });
    } else if (item.is_dir && item.google_url) {
      items.push({ label: "Open in Google Drive", icon: "open_in_new", onSelect: () => openInGoogle(item) });
    }
    items.push({ label: item.is_dir ? "Download as zip" : "Download", icon: "download", onSelect: () => download(item), disabled: !local });
    items.push({ separator: true });
    items.push({ label: "Rename", icon: "edit", onSelect: () => setDialog({ kind: "rename", item }), disabled: !local });
    items.push({ label: "Move", icon: "drive_file_move", onSelect: () => setDialog({ kind: "move", items: [item] }), disabled: !local });
    items.push({ separator: true });
    items.push({ label: "Delete", icon: "delete", danger: true, onSelect: () => setDialog({ kind: "delete", items: [item] }), disabled: !local });
    return items;
  };

  const close = useCallback(() => setDialog(null), []);
  let dialogs: ReactNode = null;
  if (accountId !== null && dialog?.kind === "rename") dialogs = <RenameDialog accountId={accountId} item={dialog.item} onClose={close} />;
  if (accountId !== null && dialog?.kind === "move") dialogs = <MoveDialog accountId={accountId} items={dialog.items} onClose={close} />;
  if (accountId !== null && dialog?.kind === "delete") dialogs = <DeleteDialog accountId={accountId} items={dialog.items} onClose={close} />;

  return { open, download, openInGoogle, menuFor, dialogs, askDelete: (items: FileItem[]) => setDialog({ kind: "delete", items }) };
}

function useFileOp(path: string, done: string, onClose: () => void) {
  const toast = useToast();
  const invalidate = useInvalidateFiles();
  return useMutation({
    mutationFn: (body: unknown) => api(`/api/files/${path}`, { method: "POST", body }),
    onSuccess: () => {
      invalidate();
      toast(done);
      onClose();
    },
    // A half-finished action still changed some items: refresh so the list tells the truth.
    onError: (e) => {
      if (e instanceof ApiError && e.partial) invalidate();
    },
  });
}

const names = (paths: string[]) => paths.join(", ");

/** The server's message, plus which items were done, failed or left alone when it stopped half way. */
function OpError({ error }: { error: unknown }) {
  const partial = error instanceof ApiError ? error.partial : undefined;
  return (
    <Notice tone="err">
      <p>{friendlyError(error)}</p>
      {partial && partial.done.length > 0 && <p className="mt-1 text-table">Done: {names(partial.done)}</p>}
      {partial && partial.failed.length > 0 && <p className="mt-1 text-table">Failed: {names(partial.failed)}</p>}
      {partial && partial.not_done.length > 0 && <p className="mt-1 text-table">Not done: {names(partial.not_done)}</p>}
    </Notice>
  );
}

function RenameDialog({ accountId, item, onClose }: { accountId: number; item: FileItem; onClose: () => void }) {
  const [name, setName] = useState(item.name);
  const op = useFileOp("rename", "Renamed. Google Drive gets the change at the next sync.", onClose);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    op.mutate({ account_id: accountId, path: item.path, new_name: name.trim() });
  };
  return (
    <Dialog
      title="Rename"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" type="submit" form="rename-form" disabled={!name.trim() || name.trim() === item.name || op.isPending}>
            Rename
          </Button>
        </>
      }
    >
      <form id="rename-form" onSubmit={submit}>
        <Field label="New name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      </form>
      {item.is_dir && (
        <p className="text-table text-ink-2">
          Google Drive sees a renamed folder as removed and added again, so every file inside counts toward the
          deletion limit of the next sync.
        </p>
      )}
      {op.isError && <OpError error={op.error} />}
    </Dialog>
  );
}

function MoveDialog({ accountId, items, onClose }: { accountId: number; items: FileItem[]; onClose: () => void }) {
  const [to, setTo] = useState(parentOf(items[0].path));
  const op = useFileOp("move", "Moved. Google Drive gets the change at the next sync.", onClose);
  return (
    <Dialog
      title={items.length === 1 ? `Move "${items[0].name}"` : `Move ${items.length} items`}
      onClose={onClose}
      wide
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            data-autofocus
            disabled={op.isPending || to === parentOf(items[0].path)}
            onClick={() => op.mutate({ account_id: accountId, paths: items.map((i) => i.path), to })}
          >
            Move here
          </Button>
        </>
      }
    >
      <FolderPicker source="local" accountId={accountId} value={to} onChange={setTo} rootLabel="My Drive" />
      {op.isError && <OpError error={op.error} />}
    </Dialog>
  );
}

function DeleteDialog({ accountId, items, onClose }: { accountId: number; items: FileItem[]; onClose: () => void }) {
  const op = useFileOp("delete", "Moved to Recovery. It leaves Google Drive (to Drive's trash) at the next sync.", onClose);
  const what = items.length === 1 ? `"${items[0].name}"` : `${items.length} items`;
  return (
    <Dialog
      title={`Delete ${what}?`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose} data-autofocus>
            Cancel
          </Button>
          <Button tone="danger" disabled={op.isPending} onClick={() => op.mutate({ account_id: accountId, paths: items.map((i) => i.path) })}>
            Delete
          </Button>
        </>
      }
    >
      <p>
        DriveSync moves it into Recovery on this server first, so you can get it back. At the next sync it is removed
        from Google Drive too, into Drive's trash.
      </p>
      {op.isError && <OpError error={op.error} />}
    </Dialog>
  );
}
