import { useState } from "react";

import type { Account } from "../../api/types";
import { useDeleteForever } from "../../hooks/useRules";
import { useToast } from "../Toasts";
import { Button, Dialog, Field, inputClass } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

const PLACES: Record<string, string> = { offloaded: "Offloaded", deleted: "Deleted in Gmail" };

/** Mail panel spec §7: removes CleanSync's copy of mail already out of Gmail. Picked mails
 * (`message_ids`), or a pick-all as the view, label and search. `count` is how many that is;
 * the person types it, and the server counts again. `onDone` runs only when mail was deleted;
 * `onClose` runs on any close. */
export function DeleteForeverDialog({
  account,
  start,
  count,
  onClose,
  onDone,
}: {
  account: Account;
  start: { view: string; message_ids?: number[]; label?: string; q?: string };
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
      {
        accountId: account.id,
        view: start.view,
        messageIds: start.message_ids,
        label: start.label,
        q: start.message_ids ? undefined : (start.q ?? ""),
        confirm: count,
      },
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
      <p>
        {mails(count)} from {PLACES[start.view] ?? start.view}.
      </p>
      <p className="text-ink-2">This removes CleanSync's own copy. The mail is already gone from Gmail, so afterwards it exists nowhere. This can't be undone.</p>
      {count > 0 && (
        <Field label={`Type ${count.toLocaleString()} to delete forever`}>
          <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
      )}
    </Dialog>
  );
}
