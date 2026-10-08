import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, friendlyError } from "../api/client";
import { fileUrl, useConflicts, usePairs } from "../api/queries";
import type { Conflict, Pair } from "../api/types";
import { useToast } from "../components/Toasts";
import { Button, Dialog, Empty, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { formatBytes, formatWhen } from "../lib/format";
import { accountPath } from "../lib/files";

type Action = "keep_local" | "keep_remote" | "keep_both" | "skip";
const DONE: Record<Action, string> = {
  keep_local: "Kept the version from this server. The other one is in Recovery.",
  keep_remote: "Kept the version from Google Drive. The other one is in Recovery.",
  keep_both: "Kept both copies, with different names.",
  skip: "Skipped. Both copies stay as they are.",
};

function Version({ title, label, pair, path, size, mtime }: { title: string; label: string; pair: Pair; path: string; size: number | null; mtime: string | null }) {
  const full = accountPath(pair.local_subpath, path);
  return (
    <div className="rounded-ctl border border-line p-3">
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-table tabular text-ink-2">
        {formatBytes(size)}, changed {formatWhen(mtime)}
      </p>
      <div className="mt-2 flex gap-4 text-table">
        <a aria-label={`Open ${title.toLowerCase()} version of ${label}`} className="font-semibold text-accent" href={fileUrl(pair.account_id, full)} target="_blank" rel="noopener noreferrer">
          Open
        </a>
        <a aria-label={`Download ${title.toLowerCase()} version of ${label}`} className="font-semibold text-accent" href={fileUrl(pair.account_id, full, true)}>
          Download
        </a>
      </div>
    </div>
  );
}

export function ConflictCard({ conflict, pair }: { conflict: Conflict; pair: Pair }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState<Action | null>(null);
  const resolve = useMutation({
    mutationFn: (action: Action) => api(`/api/conflicts/${conflict.id}/resolve`, { method: "POST", body: { action } }),
    onSuccess: (_d, action) => {
      ["conflicts", "status", "pairs", "browse"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(DONE[action]);
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  const run = (a: Action) => (a === "keep_local" || a === "keep_remote" ? setConfirming(a) : resolve.mutate(a));
  return (
    <article className="rounded-card border border-line p-4">
      <h2 className="mb-3 break-all font-semibold">{conflict.path}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Version title="On this server" label={conflict.path} pair={pair} path={conflict.local_variant} size={conflict.local_size} mtime={conflict.local_mtime} />
        <Version title="On Google Drive" label={conflict.path} pair={pair} path={conflict.remote_variant} size={conflict.remote_size} mtime={conflict.remote_mtime} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button aria-label={`Keep local version of ${conflict.path}`} disabled={resolve.isPending} onClick={() => run("keep_local")}>
          Keep local
        </Button>
        <Button aria-label={`Keep Drive version of ${conflict.path}`} disabled={resolve.isPending} onClick={() => run("keep_remote")}>
          Keep Drive
        </Button>
        <Button aria-label={`Keep both versions of ${conflict.path}`} disabled={resolve.isPending} onClick={() => run("keep_both")}>
          Keep both
        </Button>
        <Button tone="quiet" aria-label={`Skip ${conflict.path}`} disabled={resolve.isPending} onClick={() => run("skip")}>
          Skip
        </Button>
      </div>
      {confirming && (
        <Dialog
          title={confirming === "keep_local" ? "Keep the version from this server?" : "Keep the version from Google Drive?"}
          onClose={() => setConfirming(null)}
          footer={
            <>
              <Button data-autofocus onClick={() => setConfirming(null)}>Cancel</Button>
              <Button
                tone="primary"
                onClick={() => {
                  resolve.mutate(confirming);
                  setConfirming(null);
                }}
              >
                Keep it
              </Button>
            </>
          }
        >
          <p>The version you do not keep is moved to Recovery on this server, not deleted, and the kept one goes back to the original name.</p>
        </Dialog>
      )}
    </article>
  );
}

export function ConflictList({ pair }: { pair: Pair }) {
  const { data = [], isError, error, refetch } = useConflicts(pair.id);
  return (
    <div className="space-y-4">
      {isError && (
        <Notice tone="err">
          <p>Could not load the conflicts of {pair.name}. {friendlyError(error)}</p>
          <Button className="mt-2" onClick={() => void refetch()}>
            Try again
          </Button>
        </Notice>
      )}
      {data.map((c) => (
        <ConflictCard key={c.id} conflict={c} pair={pair} />
      ))}
    </div>
  );
}

export function ConflictsPage() {
  const { accountId } = useCurrentAccount();
  const { data: pairs = [] } = usePairs();
  const mine = pairs.filter((p) => p.account_id === accountId);
  const lists = useQueries({
    queries: mine.map((p) => ({
      queryKey: ["conflicts", p.id],
      queryFn: () => api<Conflict[]>(`/api/sync-pairs/${p.id}/conflicts`),
    })),
  });
  const total = lists.reduce((n, q) => n + (q.data?.length ?? 0), 0);
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <h1 className="text-title font-semibold">Conflicts</h1>
      <p className="max-w-prose text-ink-2">
        These files changed on both sides between two syncs. DriveSync kept both copies; pick which one stays.
      </p>
      {lists.every((q) => q.isSuccess) && total === 0 && <Empty icon="check_circle" title="No conflicts" />}
      {mine.map((p) => (
        <ConflictList key={p.id} pair={p} />
      ))}
    </div>
  );
}
