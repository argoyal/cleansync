import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api, friendlyError } from "../../api/client";
import type { Account } from "../../api/types";
import { OAUTH_RETURN_KEY } from "../../hooks/useConnect";
import { useToast } from "../Toasts";
import { Button, Notice } from "../ui";
import { PERMANENT_REVOKE_TEXT } from "./PermanentDelete";

export const FILTERS_OFF_TEXT = "Turn this on to let CleanSync keep a sender's new mail out of your Inbox when it can't unsubscribe for you.";
export const FILTERS_ON_TEXT = "CleanSync can make a Gmail filter that keeps a sender's new mail out of your Inbox.";
export const FILTERS_ON_TOAST = "Gmail filters are on for this mailbox.";
export const FILTERS_DENIED_TOAST = "Google didn't allow Gmail filters, so they stay off. Try again and tick the box for it.";

/** Settings → Accounts → Allow Gmail filters (0.5.2 spec §5), built like Allow permanent
 * delete. Turn on may need Google's sign-in for the filter permission; Turn off never
 * revokes it at Google. */
export function GmailFilters({ account }: { account: Account }) {
  const qc = useQueryClient();
  const toast = useToast();
  const location = useLocation();
  const on = account.gmail_filters;
  const change = useMutation({
    mutationFn: (next: boolean) =>
      api<{ auth_url: string | null }>(`/api/accounts/${account.id}/gmail-filters`, { method: "POST", body: { on: next } }),
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
    <div role="group" aria-label="Allow Gmail filters" className="basis-full space-y-1 rounded-card border border-line p-3">
      <p className="font-semibold">
        Allow Gmail filters: <span className={on ? "text-accent" : "text-ink-2"}>{on ? "On" : "Off"}</span>
      </p>
      {account.gmail_filters_note && <Notice tone="warn">{account.gmail_filters_note}</Notice>}
      <p className="text-table text-ink-2">{on ? FILTERS_ON_TEXT : FILTERS_OFF_TEXT}</p>
      {on && <p className="text-meta text-ink-2">{PERMANENT_REVOKE_TEXT}</p>}
      <Button disabled={change.isPending} onClick={() => change.mutate(!on)}>
        {on ? "Turn off" : "Turn on"}
      </Button>
    </div>
  );
}
