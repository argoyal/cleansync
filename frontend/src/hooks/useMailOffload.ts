import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, friendlyError } from "../api/client";
import type { MailOffloadPreview, OffloadStart } from "../api/types";
import { useToast } from "../components/Toasts";

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

/** Spec §5.5: restore never makes a second copy in Gmail, so it needs no typed confirmation.
 * `archive` restores mail from the local archive (mail deleted by hand or by a rule). */
export function useRestoreMail() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { accountId: number; start?: OffloadStart; offloadId?: number; archive?: boolean }) =>
      api<{ queued: string; count: number }>("/api/mail/restore", {
        method: "POST",
        body: {
          account_id: b.accountId,
          ...(b.start ?? {}),
          ...(b.offloadId !== undefined ? { offload_id: b.offloadId } : {}),
          ...(b.archive ? { archive: true } : {}),
        },
      }),
    onSuccess: ({ count }) => {
      toast(`Putting ${count.toLocaleString()} ${count === 1 ? "mail" : "mails"} back in Gmail. Sync activity shows when it's done.`);
      void qc.invalidateQueries({ queryKey: ["mail-status"] });
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}
