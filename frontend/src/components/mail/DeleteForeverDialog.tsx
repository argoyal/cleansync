import { useState } from "react";

import type { Account } from "../../api/types";
import { useDeleteForever } from "../../hooks/useRules";
import { useToast } from "../Toasts";
import { Button, Dialog, Field, inputClass } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

/** Removes mail from the Local archive for good: picked mails, or a search in the archive.
 * `count` is how many that is; the person types it to go ahead, and the server checks it again.
 * `onDone` runs only when mail was deleted; `onClose` runs on any close. */
export function DeleteForeverDialog({
  account,
  start,
  count,
  onClose,
  onDone,
}: {
  account: Account;
  start: { message_ids?: number[]; q?: string };
  count: number;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const forever = useDeleteForever();
  const toast = useToast();
  const ok = count > 0 && parseTyped(typed) === count;
  const go = () =>
    forever.mutate(
      { accountId: account.id, messageIds: start.message_ids, q: start.q, confirm: count },
      {
        onSuccess: ({ deleted }) => {
          toast(`Deleted ${mails(deleted)} forever.`);
          onDone?.();
          onClose();
        },
        onError: () => setTyped(""),
      },
    );
  return (
    <Dialog
      title="Delete forever"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="danger" disabled={!ok || forever.isPending} onClick={go}>
            Delete forever
          </Button>
        </>
      }
    >
      <p>{mails(count)} from the Local archive.</p>
      <p className="text-ink-2">This removes CleanSync's own copy. It can't be undone: the mail will exist nowhere.</p>
      {count > 0 && (
        <Field label={`Type ${count.toLocaleString()} to delete forever`}>
          <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
      )}
    </Dialog>
  );
}
