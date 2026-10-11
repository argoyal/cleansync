import { useState } from "react";

import type { Account, MailMessageInfo, OffloadStart } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { Button } from "../ui";
import { OffloadMailDialog } from "./OffloadMailDialog";

/** The page's main action (spec §9: the deep indigo button), and Restore for a whole view or search. */
export function MailHeaderActions({ account, q, view }: { account: Account; q: string; view: string }) {
  const [open, setOpen] = useState(false);
  const restore = useRestoreMail();
  const restoreQ = q || (view === "offloaded" ? "is:offloaded" : view === "deleted" ? "is:deleted" : "");
  return (
    <>
      {restoreQ && (
        <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { q: restoreQ } })}>
          Restore these to Gmail
        </Button>
      )}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Spec §6.2: Restore to Gmail (offloaded, or deleted in Gmail), Offload (still in Gmail). */
export function MessageMailActions({ account, message }: { account: Account; message: MailMessageInfo }) {
  const [start, setStart] = useState<OffloadStart | null>(null);
  const restore = useRestoreMail();
  if (message.file_missing) return null; // nothing here to offload from or restore with
  return (
    <>
      {message.state === "in_mail" ? (
        <Button onClick={() => setStart({ message_ids: [message.id] })}>Offload</Button>
      ) : (
        <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { message_ids: [message.id] } })}>
          Restore to Gmail
        </Button>
      )}
      {start && <OffloadMailDialog account={account} start={start} onClose={() => setStart(null)} />}
    </>
  );
}
