import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, friendlyError } from "../api/client";
import { useToast } from "../components/Toasts";

function useMailAction(path: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (accountId: number) => api<{ queued: string }>(path, { method: "POST", body: { account_id: accountId } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["mail-status"] });
      void qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

/** Start the first backup (spec §4.2 step 2). */
export const useStartMail = () => useMailAction("/api/mail/start");
/** "Check now" (spec §4.4). */
export const useCheckMail = () => useMailAction("/api/mail/check");
