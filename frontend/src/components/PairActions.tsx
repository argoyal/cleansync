import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import type { Pair } from "../api/types";
import { useToast } from "./Toasts";
import { Button } from "./ui";

export function usePairAction(pairId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ action }: { action: string; done: string }) => api(`/api/sync-pairs/${pairId}/${action}`, { method: "POST" }),
    onSuccess: (_d, { done }) => {
      ["pairs", "status", "history", "planned"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(done);
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

/** Sync now / Pause / Resume, plus the one action the pair's current problem calls for. */
export function PairActions({ pair, details = true }: { pair: Pair; details?: boolean }) {
  const act = usePairAction(pair.id);
  const busy = act.isPending || pair.status === "RUNNING";
  return (
    <div className="flex flex-wrap gap-2">
      {!pair.initialized ? (
        <Link to={`/setup?step=preview&pair=${pair.id}`} className="inline-flex h-9 items-center rounded-ctl bg-accent px-3.5 font-medium text-accent-ink">
          Preview and start
        </Link>
      ) : (
        <Button icon="sync" disabled={busy || pair.paused} onClick={() => act.mutate({ action: "sync", done: "Sync started." })}>
          Sync now
        </Button>
      )}
      {pair.paused ? (
        <Button icon="play_arrow" disabled={act.isPending} onClick={() => act.mutate({ action: "resume", done: "Resumed." })}>
          Resume
        </Button>
      ) : (
        <Button icon="pause" disabled={act.isPending} onClick={() => act.mutate({ action: "pause", done: "Paused. Nothing syncs until you resume." })}>
          Pause
        </Button>
      )}
      {pair.attention_reason === "BLOCKED" && (
        <Link to={`/activity/${pair.id}/blocked`} className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Review blocked sync
        </Link>
      )}
      {pair.attention_reason === "NEEDS_RESYNC" && pair.direction === "BIDIRECTIONAL" && (
        <Button
          tone="primary"
          disabled={busy}
          onClick={() => act.mutate({ action: "resync", done: "Safe resync started. It copies both ways and deletes nothing." })}
        >
          Run safe resync
        </Button>
      )}
      {pair.attention_reason === "CONFLICTS" && (
        <Link to="/conflicts" className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Resolve conflicts
        </Link>
      )}
      {pair.attention_reason === "AUTH" && (
        <Link to="/settings?tab=accounts" className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Reconnect account
        </Link>
      )}
      {details && (
        <Link to={`/activity/${pair.id}`} className="inline-flex h-9 items-center rounded-ctl px-3.5 font-medium text-accent hover:bg-sunken">
          Details
        </Link>
      )}
    </div>
  );
}
