import { useState } from "react";

import { useApprove } from "../../hooks/useRules";
import { mails } from "../../lib/ruleRuns";
import { Button, Dialog, Field, inputClass } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

/** Approving suggestions that include Delete forever (plan decision 16): the count of those
 * mails is typed first, and the server checks it again. */
export function ApproveForeverDialog({
  accountId,
  ids,
  forever,
  onClose,
  onDone,
}: {
  accountId: number;
  ids: number[];
  forever: number;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [typed, setTyped] = useState("");
  const approve = useApprove();
  const ok = parseTyped(typed) === forever;
  const which = ids.length === forever ? mails(forever) : `${mails(forever)} of these`;
  return (
    <Dialog
      title="Approve"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="danger"
            disabled={!ok || approve.isPending}
            onClick={() =>
              approve.mutate(
                { accountId, ids, confirm: forever },
                {
                  onSuccess: () => {
                    onDone?.();
                    onClose();
                  },
                },
              )
            }
          >
            Approve
          </Button>
        </>
      }
    >
      <p>{which} will be deleted forever: from Gmail and from CleanSync. This can't be undone.</p>
      <Field label={`Type ${forever.toLocaleString()} to delete them forever`}>
        <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </Field>
    </Dialog>
  );
}
