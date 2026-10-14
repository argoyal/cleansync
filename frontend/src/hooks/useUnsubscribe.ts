import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, friendlyError } from "../api/client";
import type { JobKind, SenderTab, UnsubscribeJob } from "../api/unsubscribe";
import { useToast } from "../components/Toasts";

export { useLatestUnsubscribeJob, useSenders, useUnsubscribeJob } from "../api/unsubscribe";

const KEYS = ["unsubscribe"];

function useChange<A, R>(fn: (a: A) => Promise<R>) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => KEYS.forEach((k) => void qc.invalidateQueries({ queryKey: [k] })),
    // A refusal (one already running, too many picked) says why in the server's own words.
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

/** Starts an unsubscribe, auto-archive or keep job: picked senders, or Pick all as the tab and search. */
export const useStartUnsubscribeJob = () =>
  useChange((b: { accountId: number; kind: JobKind } & ({ addresses: string[] } | { all: { tab: SenderTab; q: string } })) =>
    api<{ job_id: number }>("/api/mail/unsubscribe/jobs", {
      method: "POST",
      body: { account_id: b.accountId, kind: b.kind, ...("all" in b ? { all: b.all } : { addresses: b.addresses }) },
    }),
  );

/** Marks senders unsubscribed by hand (done outside CleanSync), or clears their status. */
export const useMarkSenders = () =>
  useChange((b: { accountId: number; addresses: string[]; status: "unsubscribed" | null }) =>
    api<{ changed: number }>("/api/mail/unsubscribe/mark", {
      method: "POST",
      body: { account_id: b.accountId, addresses: b.addresses, status: b.status },
    }),
  );

export const useStopUnsubscribeJob = () =>
  useChange((b: { jobId: number }) => api<UnsubscribeJob>(`/api/mail/unsubscribe/jobs/${b.jobId}/stop`, { method: "POST" }));
