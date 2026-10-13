import { Link, useParams } from "react-router-dom";

import { ApiError, friendlyError } from "../api/client";
import { useMailOffload, useMailOffloads } from "../api/queries";
import type { MailOffload } from "../api/types";
import { Bar, Button, Empty, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { useRestoreMail, useStopOffload } from "../hooks/useMailOffload";
import { formatBytes, formatWhen, percent } from "../lib/format";
import { OFFLOAD_STATE_TEXT, spaceLines } from "../lib/mail";

export { OFFLOAD_STATE_TEXT };

const NO_SUCH_OFFLOAD = "That offload doesn't exist.";

export const TRASH_NOTE = "Offloaded mail sits in Gmail's trash. Gmail empties its trash on its own after 30 days; that's when the space comes back.";

/** What happened to the mails a failed offload didn't finish. The server's reason says it when the
 * last batch may already be out of Gmail (after a stop or a restart), and a stop has its own words:
 * then this line only counts them and doesn't say where they are. Otherwise they were never touched. */
function notDoneLine(o: MailOffload): string {
  const n = o.not_done.toLocaleString();
  const explained = o.stopped || (o.reason ?? "").includes("may already be out of Gmail");
  return explained ? `${n} weren't counted as offloaded.` : `${n} weren't reached and stayed in Gmail.`;
}

/** Spec §5.3–5.5: one offload — its state, what was skipped and why, when Gmail frees the space, restore. */
export function MailOffloadPage() {
  const { id } = useParams();
  const { accountId } = useCurrentAccount();
  const offId = /^[1-9][0-9]{0,15}$/.test(id ?? "") ? Number(id) : null;
  const off = useMailOffload(offId);
  const restore = useRestoreMail();
  const stop = useStopOffload();
  const missing = offId === null || (off.error instanceof ApiError && off.error.status === 404);
  if (missing || off.isError)
    return (
      <div className="p-4 sm:p-6">
        <Notice tone="err">{missing ? NO_SUCH_OFFLOAD : friendlyError(off.error)}</Notice>
      </div>
    );
  const o = off.data;
  if (!o) return <p className="p-4 text-ink-2 sm:p-6">Loading…</p>;
  const busy = o.state === "WAITING" || o.state === "RUNNING";
  return (
    <div className="space-y-4 p-4 sm:p-6">
      <Link to="/mail?view=offloaded&tab=batches" className="text-table text-accent">
        Offload batches
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
      {o.state === "FAILED" && o.not_done > 0 && <p>{notDoneLine(o)}</p>}
      {o.done > 0 && (
        <div className="space-y-1">
          {spaceLines(o).map((line) => (
            <p key={line} className="text-ink-2">
              {line}
            </p>
          ))}
          {o.trashed > 0 && <p className="max-w-3xl text-ink-2">{TRASH_NOTE}</p>}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {busy && (
          <Button disabled={stop.isPending} onClick={() => stop.mutate(o.id)}>
            Stop
          </Button>
        )}
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

/** The Batches tab of Offloaded (mail panel spec §4): offloads started from a filter or a
 * pick, with the space each freed. Mail offloaded one at a time or by a rule isn't listed. */
export function OffloadBatches({ accountId }: { accountId: number }) {
  const list = useMailOffloads(accountId).data ?? [];
  if (list.length === 0) return <Empty icon="inventory_2" title="No offload batches yet" />;
  return (
    <ul aria-label="Offload batches" className="divide-y divide-line rounded-card border border-line">
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
  );
}
