import { useState } from "react";

import type { Account, MailMessageInfo, OffloadStart } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { useDeleteMail } from "../../hooks/useRules";
import { runRulesStart } from "../../lib/mail";
import { useToast } from "../Toasts";
import { Button, Dialog } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";
import { RuleChip } from "./RuleChip";
import { RunRulesDialog, type RunRulesStart } from "./RunRulesDialog";

const RESTORE_VIEWS: Record<string, string> = { all: "", offloaded: "is:offloaded", deleted: "is:deleted" };

/** What "Restore these to Gmail" sends for a view and search, or null when the search language
 * can't say the view (a label, Inbox, Sent…): then the button isn't offered at all, rather
 * than restoring more than the list shows. */
export function restoreQuery(view: string, label: string, q: string): string | null {
  if (label || !(view in RESTORE_VIEWS)) return null;
  return [RESTORE_VIEWS[view], q].filter(Boolean).join(" ") || null;
}

/** The page's main action (spec §9: the deep indigo button), and Restore for a whole view or search.
 * `scope` names the view or label on screen, which the offload filter doesn't carry over.
 * In the Local archive the main action is Delete forever… for the list on screen; `total` is
 * how many mails that is. */
export function MailHeaderActions({
  account,
  q,
  view,
  label = "",
  scope = null,
  total = null,
}: {
  account: Account;
  q: string;
  view: string;
  label?: string;
  scope?: string | null;
  total?: number | null;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<RunRulesStart | null>(null);
  const [forever, setForever] = useState(false);
  const restore = useRestoreMail();
  if (view === "archive") {
    // With a label filter the count includes the label, but the request can't say so; the server would refuse.
    if (label) return null;
    return (
      <>
        <Button tone="danger" disabled={!total} onClick={() => setForever(true)}>
          Delete forever…
        </Button>
        {forever && total ? (
          // An empty search means the whole archive, which the server wants said out loud.
          <DeleteForeverDialog account={account} start={{ q: q.trim() || "is:archived" }} count={total} onClose={() => setForever(false)} />
        ) : null}
      </>
    );
  }
  const restoreQ = restoreQuery(view, label, q);
  const rulesStart = runRulesStart(view, label, scope, q);
  return (
    <>
      {restoreQ && (
        <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { q: restoreQ } })}>
          Restore these to Gmail
        </Button>
      )}
      {rulesStart && <Button onClick={() => setRules(rulesStart)}>Run rules on…</Button>}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
      {rules && <RunRulesDialog account={account} start={rules} onClose={() => setRules(null)} />}
    </>
  );
}

/** Delete by hand: to Gmail's trash, with CleanSync's copy kept in the Local archive. */
export function DeleteMailDialog({
  account,
  start,
  onClose,
  onDone,
}: {
  account: Account;
  start: { thread_ids: string[] } | { message_ids: number[] };
  onClose: () => void;
  onDone?: () => void;
}) {
  const del = useDeleteMail();
  const toast = useToast();
  const threads = "thread_ids" in start ? start.thread_ids.length : 0;
  const question =
    threads > 1
      ? `Move ${threads.toLocaleString()} threads to Gmail's trash? CleanSync keeps them in the Local archive, where you can restore them or delete them forever.`
      : `Move this ${threads === 1 ? "thread" : "mail"} to Gmail's trash? CleanSync keeps it in the Local archive, where you can restore it or delete it forever.`;
  const go = () =>
    del.mutate(
      { accountId: account.id, ...("thread_ids" in start ? { threadIds: start.thread_ids } : { messageIds: start.message_ids }) },
      {
        onSuccess: ({ deleted, failed }) => {
          const moved = `Moved ${deleted.toLocaleString()} ${deleted === 1 ? "mail" : "mails"} to Gmail's trash.`;
          const left = failed.length > 0 ? ` ${failed.length.toLocaleString()} couldn't be moved: ${failed[0].reason}` : "";
          toast(moved + left, { tone: failed.length > 0 ? "error" : "info" });
          onDone?.();
          onClose();
        },
      },
    );
  return (
    <Dialog
      title="Delete"
      onClose={onClose}
      footer={
        <>
          <Button data-autofocus onClick={onClose}>
            Cancel
          </Button>
          <Button tone="danger" disabled={del.isPending} onClick={go}>
            Move to trash
          </Button>
        </>
      }
    >
      <p>{question}</p>
    </Dialog>
  );
}

type Open = "offload" | "delete" | "rule" | "forever" | null;

/** Spec §6.2: Restore to Gmail (offloaded, or deleted in Gmail), Offload (still in Gmail).
 * AI rules spec §7: Delete and Make a rule… for mail in Gmail, the rule chip, and Restore or
 * Delete forever… for mail in the Local archive. */
export function MessageMailActions({ account, message }: { account: Account; message: MailMessageInfo }) {
  const [open, setOpen] = useState<Open>(null);
  const restore = useRestoreMail();
  if (message.file_missing) return null; // nothing here to offload from or restore with
  const ids = [message.id];
  const close = () => setOpen(null);
  const archived = message.state === "local_archive";
  return (
    <>
      {/* Not in the archive: Undo or Correct… there would change Gmail for mail already deleted. */}
      {message.decision && !archived && <RuleChip accountId={account.id} chip={message.decision} />}
      {message.state === "in_mail" ? (
        <>
          <Button onClick={() => setOpen("offload")}>Offload</Button>
          <Button onClick={() => setOpen("delete")}>Delete</Button>
          <Button onClick={() => setOpen("rule")}>Make a rule…</Button>
        </>
      ) : (
        <>
          <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { message_ids: ids }, archive: archived })}>
            Restore to Gmail
          </Button>
          {archived && <Button onClick={() => setOpen("forever")}>Delete forever…</Button>}
        </>
      )}
      {open === "offload" && <OffloadMailDialog account={account} start={{ message_ids: ids } satisfies OffloadStart} onClose={close} />}
      {open === "delete" && <DeleteMailDialog account={account} start={{ message_ids: ids }} onClose={close} />}
      {open === "rule" && <MakeRuleDialog account={account} messageIds={ids} onClose={close} />}
      {open === "forever" && <DeleteForeverDialog account={account} start={{ message_ids: ids }} count={1} onClose={close} />}
    </>
  );
}
