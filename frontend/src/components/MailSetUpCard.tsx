import { friendlyError } from "../api/client";
import type { Account } from "../api/types";
import { useSetUpMail } from "../hooks/useSetUpMail";
import { useToast } from "./Toasts";
import { Button } from "./ui";

/** The Mail page before mail is set up (spec §4.2 step 1). */
export function MailSetUpCard({ account, reconnect = false }: { account: Account; reconnect?: boolean }) {
  const setUp = useSetUpMail();
  const toast = useToast();
  const go = () => setUp.mutate(account.id, { onError: (e) => toast(friendlyError(e), { tone: "error" }) });
  return (
    <section className="max-w-xl rounded-card border border-line bg-surface p-6">
      <h1 className="mb-2 text-title font-semibold">{reconnect ? "Reconnect mail" : "Set up mail backup"}</h1>
      {reconnect ? (
        <p className="mb-4 text-ink-2">
          Google no longer lets CleanSync read this account's mail, so mail backup has stopped. The mail already backed up stays here. Sign in again and tick
          the box for Gmail.
        </p>
      ) : (
        <p className="mb-4 text-ink-2">
          CleanSync keeps a copy of every Gmail message of {account.email} on this server (all mail except Spam and Trash). Google asks you once more, to let
          CleanSync read and manage your mail. Nothing in Gmail changes until you choose to offload mail.
        </p>
      )}
      <Button tone="main" disabled={setUp.isPending} onClick={go}>
        {reconnect ? "Reconnect mail" : "Set up mail backup"}
      </Button>
    </section>
  );
}
