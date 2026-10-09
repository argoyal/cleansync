import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import type { Pair } from "../api/types";
import { useToast } from "./Toasts";
import { formatRelative } from "../lib/format";
import { Button, Notice } from "./ui";

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

/** Spec §6.9: one switch per pair. Off is the existing pause; on is resume. */
export function SyncSwitch({ pair }: { pair: Pair }) {
  const act = usePairAction(pair.id);
  const on = !pair.paused;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={`Sync, ${pair.name}`}
      disabled={act.isPending}
      className="inline-flex h-9 items-center gap-2 rounded-ctl px-3 text-body font-medium hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50"
      onClick={() =>
        act.mutate(
          on
            ? { action: "pause", done: "Sync is off. Nothing syncs until you turn it back on." }
            : { action: "resume", done: "Sync is on." },
        )
      }
    >
      <span aria-hidden="true" className={`relative h-5 w-9 rounded-full transition-colors ${on ? "bg-accent" : "bg-line"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow-float transition-[left] ${on ? "left-[18px]" : "left-0.5"}`} />
      </span>
      {on ? "Sync on" : "Sync off"}
    </button>
  );
}

/** Sync now / the Sync on-off switch, plus the one action the pair's current problem calls for. */
export function PairActions({ pair, details = true }: { pair: Pair; details?: boolean }) {
  const act = usePairAction(pair.id);
  const busy = act.isPending || pair.status === "RUNNING";
  return (
    <div className="flex flex-wrap gap-2">
      {!pair.initialized ? (
        <Link to={`/setup?step=preview&pair=${pair.id}`} aria-label={`Preview and start, ${pair.name}`} className="inline-flex h-9 items-center rounded-ctl bg-accent px-3.5 font-medium text-accent-ink">
          Preview and start
        </Link>
      ) : (
        <Button icon="sync" aria-label={`Sync now, ${pair.name}`} disabled={busy || pair.paused} onClick={() => act.mutate({ action: "sync", done: "Sync started." })}>
          Sync now
        </Button>
      )}
      <SyncSwitch pair={pair} />
      {pair.attention_reason === "BLOCKED" && (
        <Link to={`/activity/${pair.id}/blocked`} aria-label={`Review blocked sync, ${pair.name}`} className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Review blocked sync
        </Link>
      )}
      {pair.attention_reason === "NEEDS_RESYNC" && pair.direction === "BIDIRECTIONAL" && (
        <Button
          tone="primary"
          aria-label={`Run safe resync, ${pair.name}`}
          disabled={busy}
          onClick={() => act.mutate({ action: "resync", done: "Safe resync started. It copies both ways and deletes nothing." })}
        >
          Run safe resync
        </Button>
      )}
      {pair.attention_reason === "CONFLICTS" && (
        <Link to="/conflicts" aria-label={`Resolve conflicts, ${pair.name}`} className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Resolve conflicts
        </Link>
      )}
      {pair.attention_reason === "AUTH" && (
        <Link to="/settings?tab=accounts" aria-label={`Reconnect account, ${pair.name}`} className="inline-flex h-9 items-center rounded-ctl bg-warn px-3.5 font-medium text-white">
          Reconnect account
        </Link>
      )}
      {details && (
        <Link to={`/activity/${pair.id}`} aria-label={`Details, ${pair.name}`} className="inline-flex h-9 items-center rounded-ctl px-3.5 font-medium text-accent hover:bg-sunken">
          Details
        </Link>
      )}
    </div>
  );
}

/** A plain sentence for every reason a pair needs attention. The matching button is in PairActions. */
export function attentionText(pair: Pair): string | null {
  switch (pair.attention_reason) {
    case "BLOCKED":
      return "This sync would delete more files than your limit allows, so it stopped. Nothing has been deleted yet.";
    case "CONFLICTS":
      return "Some files changed on both sides. Both copies are kept until you choose one.";
    case "NEEDS_RESYNC":
      return pair.direction === "BIDIRECTIONAL"
        ? "The two sides have lost track of each other. A safe resync copies both ways and deletes nothing."
        : "The two sides have lost track of each other. Use Sync now to bring them back in line.";
    case "AUTH":
      return "Google no longer lets DriveSync in. Reconnect the account to continue.";
    case "ACCESS_CHECK":
      return "The storage folder or its marker file can't be found. Check the drive or mount is connected, then use Sync now.";
    case "PLAN_UNREADABLE":
      return "DriveSync couldn't read rclone's plan, so nothing was changed. Try Sync now; if it keeps happening, check the logs.";
    case "FAILED":
      return `The last sync failed. ${pair.next_sync_at ? `Retrying ${formatRelative(pair.next_sync_at)}.` : "Use Sync now to try again."}`;
    default:
      return null;
  }
}

export function AttentionNotice({ pair }: { pair: Pair }) {
  const text = attentionText(pair);
  if (!text && !pair.last_error) return null;
  return (
    <Notice tone={pair.status === "ERROR" ? "err" : "warn"}>
      {text && <p>{text}</p>}
      {pair.last_error && <p className={text ? "mt-1 text-ink-2" : ""}>{pair.last_error}</p>}
    </Notice>
  );
}
