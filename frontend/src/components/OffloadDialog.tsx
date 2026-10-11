import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, friendlyError } from "../api/client";
import { useOffloadBatch } from "../api/queries";
import type { OffloadBatch, OffloadBatchItem, OffloadPreview } from "../api/types";
import { formatBytes } from "../lib/format";
import { Button, Dialog, Notice } from "./ui";

const NATIVE_NOTE = "Stays on Drive — not a file CleanSync can keep a copy of yet.";
const RESULT: Record<OffloadBatchItem["state"], string> = {
  waiting: "Waiting",
  running: "Working on it",
  done: "Done",
  attention: "Needs a look",
  failed: "Not offloaded",
};

// What to refetch once an offload ran (kept here, not imported from FileActions, which imports this file).
const REFRESH = ["browse", "recent", "starred", "search", "storage", "status", "offloads", "recovery"];
const nameOf = (path: string) => path.split("/").pop() || path;
const files = (n: number) => `${n} ${n === 1 ? "file" : "files"}`;

/** Spec §7.2a: one preview and one confirm for any number of items, then each item's result. */
export function OffloadDialog({
  accountId,
  paths,
  onClose,
}: {
  accountId: number;
  paths: string[];
  /** started: an offload was started (not just looked at and cancelled). */
  onClose: (started: boolean) => void;
}) {
  const qc = useQueryClient();
  // Asked once when the dialog opens: what the person confirms must not change under them
  // (no refetch on focus, reconnect or a live update), so this is not a cached query.
  const preview = useMutation({
    mutationFn: () => api<OffloadPreview>("/api/offloads/preview", { method: "POST", body: { account_id: accountId, paths } }),
  });
  const loadPreview = preview.mutate;
  useEffect(() => loadPreview(), [loadPreview]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const batch = useOffloadBatch(batchId);
  const ready = (preview.data?.items ?? []).filter((i) => !i.blocked);
  const start = useMutation({
    mutationFn: () => api<OffloadBatch>("/api/offloads", { method: "POST", body: { account_id: accountId, paths: ready.map((i) => i.path) } }),
    onSuccess: (b) => {
      qc.setQueryData(["offload-batch", b.id], b);
      setBatchId(b.id);
    },
  });
  const close = () => {
    if (batchId) REFRESH.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    onClose(batchId !== null);
  };
  const title = paths.length === 1 ? `Offload "${nameOf(paths[0])}"?` : `Offload ${paths.length} items?`;

  if (batchId) {
    const b = batch.data;
    const lost = batch.isError && !b?.done;
    return (
      // A new key: the results view opens as a fresh dialog, so focus lands on its button.
      <Dialog
        key="results"
        title={title}
        onClose={close}
        wide
        footer={
          <Button tone="primary" data-autofocus onClick={close}>
            {b?.done ? "Close" : "Hide"}
          </Button>
        }
      >
        <div aria-live="polite" className="space-y-3">
          {!lost && <p>{b?.done ? "Finished." : "Working on it. You can close this; the work goes on."}</p>}
          <ul aria-label="Results" className="max-h-80 space-y-2 overflow-y-auto">
            {(b?.items ?? []).map((i) => (
              <li key={i.path} className="rounded-ctl border border-line px-3 py-2">
                <p className="break-all font-medium">{i.path}</p>
                <p className={`text-table ${i.state === "failed" || i.state === "attention" ? "text-err" : "text-ink-2"}`}>
                  {RESULT[i.state]}
                  {i.message ? `: ${i.message}` : ""}
                </p>
              </li>
            ))}
          </ul>
          {lost && (
            <Notice tone="err">
              <p>
                Lost track of this offload: {friendlyError(batch.error)}. The work may still be going on.
              </p>
              <Button className="mt-2" icon="refresh" onClick={() => batch.refetch()}>
                Retry
              </Button>
            </Notice>
          )}
        </div>
      </Dialog>
    );
  }

  const p = preview.data;
  return (
    <Dialog
      key="preview"
      title={title}
      onClose={close}
      wide
      footer={
        <>
          <Button onClick={close} data-autofocus>
            Cancel
          </Button>
          <Button tone="primary" disabled={!p || ready.length === 0 || !!p.stopped || start.isPending} onClick={() => start.mutate()}>
            Offload
          </Button>
        </>
      }
    >
      <p>
        Offloaded files stay on this server and are removed from Google Drive, into Drive's trash. They stop syncing. You can put
        them back any time with Restore to Drive.
      </p>
      {(preview.isIdle || preview.isPending) && <p className="text-ink-2">Checking what can be offloaded…</p>}
      {preview.isError && <Notice tone="err">{friendlyError(preview.error)}</Notice>}
      {p?.stopped && <Notice tone="err">{p.stopped}</Notice>}
      {p && (
        <>
          <p className="font-semibold tabular">
            {files(p.total_files)}, {formatBytes(p.total_bytes)} freed on Google Drive once Drive's trash is emptied
          </p>
          <ul className="max-h-80 space-y-2 overflow-y-auto">
            {p.items.map((i) => (
              <li key={i.path} className="rounded-ctl border border-line px-3 py-2">
                <p className="break-all font-medium">{i.path}</p>
                {i.blocked ? (
                  <p className="text-table text-err">{i.blocked}</p>
                ) : (
                  <p className="text-table tabular text-ink-2">
                    {files(i.file_count)}, {formatBytes(i.total_bytes)}
                  </p>
                )}
                {i.stays_on_drive.length > 0 && (
                  <p className="mt-1 text-table text-ink-2">
                    {NATIVE_NOTE} {i.stays_on_drive.map(nameOf).join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {start.isError && <Notice tone="err">{friendlyError(start.error)}</Notice>}
    </Dialog>
  );
}
