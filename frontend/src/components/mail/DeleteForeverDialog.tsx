import { useId, useState } from "react";

import { useMailOffload } from "../../api/queries";
import type { Account } from "../../api/types";
import { useStopOffload } from "../../hooks/useMailOffload";
import { useDeleteForever } from "../../hooks/useRules";
import { percent } from "../../lib/format";
import { viewKind } from "../../lib/mail";
import { useToast } from "../Toasts";
import { Bar, Button, Dialog, Field, Notice, inputClass } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

const PLACES: Record<string, string> = { offloaded: "Offloaded", deleted: "Deleted in Gmail" };

export const FOREVER_OFF = "Turn on Allow permanent delete in Settings → Accounts.";

/** "Delete forever…" on mail in Gmail (bulk offload spec §5): shown disabled, with the
 * reason, for a mailbox that doesn't allow permanent delete. */
export function ForeverButton({ account, disabled = false, onClick }: { account: Account; disabled?: boolean; onClick: () => void }) {
  const id = useId();
  const off = !account.permanent_delete;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button tone="danger" disabled={off || disabled} aria-describedby={off ? id : undefined} onClick={onClick}>
        Delete forever…
      </Button>
      {off && (
        <span id={id} className="text-meta text-ink-2">
          {FOREVER_OFF}
        </span>
      )}
    </span>
  );
}

/** The job's progress, then what it did (bulk offload spec §5). */
function ForeverJob({ jobId, count, onClose }: { jobId: number; count: number; onClose: () => void }) {
  const job = useMailOffload(jobId).data;
  const stop = useStopOffload();
  const busy = !job || job.state === "WAITING" || job.state === "RUNNING";
  return (
    <Dialog
      title="Delete forever"
      onClose={onClose}
      footer={
        <>
          {busy && (
            <Button disabled={stop.isPending} onClick={() => stop.mutate(jobId)}>
              Stop
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {busy || !job ? (
        <>
          <p className="tabular">
            Deleting… {(job ? job.done + job.skipped : 0).toLocaleString()} of {count.toLocaleString()}
          </p>
          <Bar label="Mails deleted" value={percent(job ? job.done + job.skipped : 0, count)} tone="run" />
          <p className="text-ink-2">You can close this. The deleting goes on.</p>
        </>
      ) : (
        <>
          <p>Deleted {mails(job.done)} forever.</p>
          {job.skipped > 0 && (
            <p>
              {mails(job.skipped)} {job.skipped === 1 ? "wasn't" : "weren't"} deleted and {job.skipped === 1 ? "stays" : "stay"} in Gmail, with CleanSync's copy.
            </p>
          )}
          {job.reason && <Notice tone="err">{job.reason}</Notice>}
        </>
      )}
    </Dialog>
  );
}

/** Delete forever (mail panel spec §7, bulk offload spec §5). In Offloaded and Deleted in Gmail it removes CleanSync's copy at once; in a Gmail view it starts a job that removes the mail from Gmail, then the copy, and shows its progress. Picked mails (`message_ids`), or a pick-all as the view, label and search. `count` is how many; the person types it, and the server counts again. `onDone` runs once the server took it. */
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
  const inGmail = viewKind(start.view) === "gmail";
  const [jobId, setJobId] = useState<number | null>(null);
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
        onSuccess: ({ deleted, job_id }) => {
          onDone?.();
          if (job_id !== null) return setJobId(job_id); // mail in Gmail: a background job
          toast(`Deleted ${mails(deleted)} forever.`);
          onClose();
        },
        onError: () => setTyped(""),
      },
    );
  if (jobId !== null) return <ForeverJob jobId={jobId} count={count} onClose={onClose} />;
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
      <p>{inGmail ? `${mails(count)} in Gmail.` : `${mails(count)} from ${PLACES[start.view] ?? start.view}.`}</p>
      <p className="text-ink-2">
        {inGmail
          ? "They are deleted from Gmail for good, and CleanSync's copy is deleted too. Afterwards they exist nowhere. This can't be undone."
          : "This removes CleanSync's own copy. The mail is already gone from Gmail, so afterwards it exists nowhere. This can't be undone."}
      </p>
      {count > 0 && (
        <Field label={`Type ${count.toLocaleString()} to delete forever`}>
          <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
      )}
    </Dialog>
  );
}
