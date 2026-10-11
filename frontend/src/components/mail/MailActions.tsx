import { useState } from "react";

import type { Account, MailMessageInfo, OffloadStart } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { Button } from "../ui";
import { OffloadMailDialog } from "./OffloadMailDialog";

const RESTORE_VIEWS: Record<string, string> = { all: "", offloaded: "is:offloaded", deleted: "is:deleted" };

/** What "Restore these to Gmail" sends for a view and search, or null when the search language
 * can't say the view (a label, Inbox, Sent…): then the button isn't offered at all, rather
 * than restoring more than the list shows. */
export function restoreQuery(view: string, label: string, q: string): string | null {
  if (label || !(view in RESTORE_VIEWS)) return null;
  return [RESTORE_VIEWS[view], q].filter(Boolean).join(" ") || null;
}

/** The page's main action (spec §9: the deep indigo button), and Restore for a whole view or search.
 * `scope` names the view or label on screen, which the offload filter doesn't carry over. */
export function MailHeaderActions({ account, q, view, label = "", scope = null }: { account: Account; q: string; view: string; label?: string; scope?: string | null }) {
  const [open, setOpen] = useState(false);
  const restore = useRestoreMail();
  const restoreQ = restoreQuery(view, label, q);
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
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
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
