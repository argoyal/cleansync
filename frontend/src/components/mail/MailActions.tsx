import { useState } from "react";

import type { Account, MailMessageInfo } from "../../api/types";
import { useOffloadOne, useRestoreMail } from "../../hooks/useMailOffload";
import { runRulesStart, viewKind } from "../../lib/mail";
import { Button, Dialog } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";
import { RuleChip } from "./RuleChip";
import { RunRulesDialog, type RunRulesStart } from "./RunRulesDialog";

/** The page's main action (spec §9: the deep indigo button), in the Gmail views only. Picked
 * mail has its own actions in the bar (mail panel spec §6). `scope` names the view or label
 * on screen, which the offload filter doesn't carry over. */
export function MailHeaderActions({
  account,
  q,
  view,
  label = "",
  scope = null,
}: {
  account: Account;
  q: string;
  view: string;
  label?: string;
  scope?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState<RunRulesStart | null>(null);
  if (viewKind(view) !== "gmail") return null;
  const rulesStart = runRulesStart(view, label, scope, q);
  return (
    <>
      {rulesStart && <Button onClick={() => setRules(rulesStart)}>Run rules on…</Button>}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
      {rules && <RunRulesDialog account={account} start={rules} onClose={() => setRules(null)} />}
    </>
  );
}

/** Offload one mail open in the reader (mail panel spec §8): checked, moved to Gmail's trash,
 * kept here in Offloaded. A plain confirm, as Delete had. */
export function OffloadOneDialog({ account, messageId, onClose }: { account: Account; messageId: number; onClose: () => void }) {
  const offload = useOffloadOne();
  return (
    <Dialog
      title="Offload this mail?"
      onClose={onClose}
      footer={
        <>
          <Button data-autofocus onClick={onClose}>
            Cancel
          </Button>
          <Button tone="main" disabled={offload.isPending} onClick={() => offload.mutate({ accountId: account.id, messageId }, { onSuccess: onClose })}>
            Offload
          </Button>
        </>
      }
    >
      <p>CleanSync checks its copy, moves the mail to Gmail's trash and keeps the copy here, in Offloaded. You can restore it to Gmail at any time.</p>
    </Dialog>
  );
}

type Open = "offload" | "rule" | "forever" | null;

/** Mail panel spec §8: a mail in Gmail offers Offload and Make a rule…; one out of Gmail
 * (offloaded, or deleted in Gmail) is read-only, with Restore to Gmail and Delete forever…. */
export function MessageMailActions({ account, message }: { account: Account; message: MailMessageInfo }) {
  const [open, setOpen] = useState<Open>(null);
  const restore = useRestoreMail();
  if (message.file_missing) return null; // nothing here to offload from or restore with
  const ids = [message.id];
  const close = () => setOpen(null);
  const view = message.state === "deleted_in_provider" ? "deleted" : "offloaded";
  return (
    <>
      {message.decision && <RuleChip accountId={account.id} chip={message.decision} />}
      {message.state === "in_mail" ? (
        <>
          <Button onClick={() => setOpen("offload")}>Offload</Button>
          <Button onClick={() => setOpen("rule")}>Make a rule…</Button>
        </>
      ) : (
        <>
          <Button disabled={restore.isPending} onClick={() => restore.mutate({ accountId: account.id, start: { message_ids: ids } })}>
            Restore to Gmail
          </Button>
          <Button onClick={() => setOpen("forever")}>Delete forever…</Button>
        </>
      )}
      {open === "offload" && <OffloadOneDialog account={account} messageId={message.id} onClose={close} />}
      {open === "rule" && <MakeRuleDialog account={account} messageIds={ids} onClose={close} />}
      {open === "forever" && <DeleteForeverDialog account={account} start={{ view, message_ids: ids }} count={1} onClose={close} />}
    </>
  );
}
