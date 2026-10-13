import { useState } from "react";

import type { Account, MailMessageInfo } from "../../api/types";
import { useOffloadOne, useRestoreMail } from "../../hooks/useMailOffload";
import { hasSearch, viewKind } from "../../lib/mail";
import { runOnText } from "../../lib/ruleRuns";
import { Button, Dialog } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";
import { RuleChip } from "./RuleChip";
import { RulePickerDialog } from "./RulePickerDialog";

/** The page's main action (spec §9: the deep indigo button), in the Gmail views only, and Run
 * rules above a search when nothing is picked (running rules spec §4; picked mail has Run rules
 * on its bar). `scope` names the view or label on screen; `total` is the search's mail count. */
export function MailHeaderActions({
  account,
  q,
  view,
  label = "",
  scope = null,
  total,
  picking = false,
}: {
  account: Account;
  q: string;
  view: string;
  label?: string;
  scope?: string | null;
  total?: number;
  picking?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [rules, setRules] = useState(false);
  if (viewKind(view) !== "gmail") return null;
  return (
    <>
      {hasSearch(q) && !picking && <Button onClick={() => setRules(true)}>Run rules</Button>}
      <Button tone="main" onClick={() => setOpen(true)}>
        Offload mail…
      </Button>
      {open && <OffloadMailDialog account={account} start={{ q }} scope={scope} onClose={() => setOpen(false)} />}
      {rules && (
        <RulePickerDialog
          account={account}
          scope={{ view, ...(label ? { label } : {}), q }}
          onText={runOnText({ total, q, place: scope ?? "All mail" })}
          onClose={() => setRules(false)}
        />
      )}
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
