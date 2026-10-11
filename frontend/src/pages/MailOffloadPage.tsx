import { Link, useParams } from "react-router-dom";

import { friendlyError } from "../api/client";
import { useMailOffload, useMailOffloads } from "../api/queries";
import { Bar, Button, Empty, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { useRestoreMail } from "../hooks/useMailOffload";
import { formatBytes, formatWhen, percent } from "../lib/format";
import { OFFLOAD_STATE_TEXT, trashLine } from "../lib/mail";

export { OFFLOAD_STATE_TEXT };

export const TRASH_NOTE = "Offloaded mail sits in Gmail's trash. Gmail empties its trash on its own after 30 days; that's when the space comes back.";

/** Spec §5.3–5.5: one offload — its state, what was skipped and why, when Gmail frees the space, restore. */
export function MailOffloadPage() {
  const { id } = useParams();
  const { accountId } = useCurrentAccount();
  const off = useMailOffload(Number(id));
  const restore = useRestoreMail();
  if (off.isError)
    return (
      <div className="p-4 sm:p-6">
        <Notice tone="err">{friendlyError(off.error)}</Notice>
      </div>
    );
  const o = off.data;
  if (!o) return null;
  const busy = o.state === "WAITING" || o.state === "RUNNING";
  return (
    <div className="space-y-4 p-4 sm:p-6">
      <Link to="/mail/offloads" className="text-table text-accent">
        All mail offloads
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-title font-semibold">Mail offload</h1>
        <span className="rounded-full bg-sec-mail-soft px-2.5 text-table text-sec-mail-soft-ink">{OFFLOAD_STATE_TEXT[o.state] ?? o.state}</span>
      </div>
      <p className="text-ink-2">
        {o.filter_text} · started {formatWhen(o.created_at)}
      </p>
      {busy && <Bar label="Mails offloaded" value={percent(o.done + o.skipped, o.total)} tone="run" />}
      <p className="tabular">
        Offloaded {o.done.toLocaleString()} of {o.total.toLocaleString()} mails · {formatBytes(o.bytes)}
        {o.skipped ? ` · ${o.skipped.toLocaleString()} skipped` : ""}
      </p>
      {o.reason && <Notice tone={o.state === "WAITING" ? "warn" : "err"}>{o.reason}</Notice>}
      {o.state === "FAILED" && o.not_done > 0 && <p>{o.not_done.toLocaleString()} weren't reached and stayed in Gmail.</p>}
      {o.done > 0 && (
        <div className="space-y-1">
          <p className="text-ink-2">{trashLine(o)}</p>
          <p className="max-w-3xl text-ink-2">{TRASH_NOTE}</p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy || o.done === 0 || accountId === null || restore.isPending} onClick={() => accountId !== null && restore.mutate({ accountId, offloadId: o.id })}>
          Restore all to Gmail
        </Button>
      </div>
      {o.skipped_items.length > 0 && (
        <section>
          <h2 className="mb-2 text-section font-semibold">Skipped (they stayed in Gmail)</h2>
          <ul aria-label="Skipped mails" className="divide-y divide-line rounded-card border border-line">
            {o.skipped_items.map((s) => (
              <li key={s.message_id} className="px-3 py-2 text-table">
                <span className="font-medium">{s.subject || "(no subject)"}</span>
                <span className="block text-ink-2">{s.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function MailOffloadsPage() {
  const { accountId } = useCurrentAccount();
  const list = useMailOffloads(accountId).data ?? [];
  return (
    <div className="space-y-4 p-4 sm:p-6">
      <h1 className="text-title font-semibold">Mail offloads</h1>
      {list.length === 0 && <Empty icon="inventory_2" title="No mail offloaded yet" />}
      <ul className="divide-y divide-line rounded-card border border-line">
        {list.map((o) => (
          <li key={o.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-table">
            <Link to={`/mail/offloads/${o.id}`} className="font-semibold text-accent">
              {o.filter_text}
            </Link>
            <span className="text-ink-2">{OFFLOAD_STATE_TEXT[o.state] ?? o.state}</span>
            <span className="tabular text-ink-2">
              {o.done.toLocaleString()} mails · {formatBytes(o.bytes)}
            </span>
            <span className="ml-auto text-ink-2">{formatWhen(o.created_at)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
