import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, friendlyError } from "../api/client";
import type { MailOffloadPreview, OffloadStart } from "../api/types";
import { useToast } from "../components/Toasts";
import { MAIL_LISTS } from "./useRules";

type Body = { accountId: number; start: OffloadStart };
const body = ({ accountId, start }: Body) => ({ account_id: accountId, ...start });

export const usePreviewOffload = () =>
  useMutation({ mutationFn: (b: Body) => api<MailOffloadPreview>("/api/mail/offloads/preview", { method: "POST", body: body(b) }) });

/** `confirm` is the count the person typed; the server checks it against a fresh count. */
export const useStartOffload = () =>
  useMutation({
    mutationFn: (b: Body & { confirm: number }) =>
      api<{ offload_id: number }>("/api/mail/offloads", { method: "POST", body: { ...body(b), confirm: b.confirm } }),
  });

/** Stop an offload or a Delete forever job: a waiting one ends now, a running one before its next batch. */
export function useStopOffload() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: number) => api<unknown>(`/api/mail/offloads/${id}/stop`, { method: "POST" }),
    onSuccess: (_d, id) => void qc.invalidateQueries({ queryKey: ["mail-offload", id] }),
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

/** Spec §5.5: restore never makes a second copy in Gmail, so it needs no typed confirmation.
 * `view` keeps a pick of threads to the mails of the view they were picked in. */
export function useRestoreMail() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { accountId: number; start?: OffloadStart; offloadId?: number; view?: string }) =>
      api<{ queued: string; count: number }>("/api/mail/restore", {
        method: "POST",
        body: {
          account_id: b.accountId,
          ...(b.start ?? {}),
          ...(b.offloadId !== undefined ? { offload_id: b.offloadId } : {}),
          ...(b.view !== undefined ? { view: b.view } : {}),
        },
      }),
    onSuccess: ({ count }) => {
      toast(`Putting ${count.toLocaleString()} ${count === 1 ? "mail" : "mails"} back in Gmail. Sync activity shows when it's done.`);
      void qc.invalidateQueries({ queryKey: ["mail-status"] });
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

/** Offload one mail open in the reader (mail panel spec §8), through the same checks as a
 * rule's Offload. */
export function useOffloadOne() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { accountId: number; messageId: number }) =>
      api<{ offloaded: boolean; reason: string | null }>(`/api/mail/messages/${b.messageId}/offload`, { method: "POST", body: { account_id: b.accountId } }),
    onSuccess: ({ offloaded, reason }) => {
      if (offloaded) toast("Offloaded. CleanSync keeps its copy in Offloaded.");
      else toast(`It wasn't offloaded: ${reason ?? "try again."}`, { tone: "error" });
      [...MAIL_LISTS, "rule-decisions"].forEach((k) => void qc.invalidateQueries({ queryKey: [k] }));
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}
