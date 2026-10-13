import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, friendlyError } from "../../api/client";
import type { Account } from "../../api/types";
import { OAUTH_RETURN_KEY } from "../../hooks/useConnect";
import { useToast } from "../Toasts";
import { Button, Notice } from "../ui";

export const PERMANENT_OFF_TEXT =
  "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once.";
export const PERMANENT_ON_TEXT = "Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.";
export const PERMANENT_REVOKE_TEXT =
  "Turn off stops CleanSync from using it. Google keeps the permission until you remove CleanSync's access at myaccount.google.com/permissions; then reconnect this account.";
export const PERMANENT_ON_TOAST = "Permanent delete is on for this mailbox.";
export const PERMANENT_DENIED_TOAST = "Google didn't give full Gmail access, so permanent delete stays off. Try again and tick the box for it.";

/** Settings → Accounts → Allow permanent delete (bulk offload spec §3). Turn on may need
 * Google's sign-in for the full Gmail permission; Turn off never revokes it at Google. */
export function PermanentDelete({ account }: { account: Account }) {
  const qc = useQueryClient();
  const toast = useToast();
  const location = useLocation();
  const on = account.permanent_delete;
  const change = useMutation({
    mutationFn: (next: boolean) =>
      api<{ auth_url: string | null }>(`/api/accounts/${account.id}/permanent-delete`, { method: "POST", body: { on: next } }),
    onSuccess: ({ auth_url }) => {
      if (auth_url === null) {
        void qc.invalidateQueries({ queryKey: ["accounts"] });
        return;
      }
      try {
        sessionStorage.setItem(OAUTH_RETURN_KEY, location.pathname + location.search);
      } catch {
        // without storage the person lands on Home afterwards, which is fine
      }
      window.location.assign(auth_url);
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  return (
    <div role="group" aria-label="Allow permanent delete" className="basis-full space-y-1 rounded-card border border-line p-3">
      <p className="font-semibold">
        Allow permanent delete: <span className={on ? "text-accent" : "text-ink-2"}>{on ? "On" : "Off"}</span>
      </p>
      {account.permanent_delete_note && <Notice tone="warn">{account.permanent_delete_note}</Notice>}
      <p className="text-table text-ink-2">{on ? PERMANENT_ON_TEXT : PERMANENT_OFF_TEXT}</p>
      {on && <p className="text-meta text-ink-2">{PERMANENT_REVOKE_TEXT}</p>}
      <Button disabled={change.isPending} onClick={() => change.mutate(!on)}>
        {on ? "Turn off" : "Turn on"}
      </Button>
    </div>
  );
}
