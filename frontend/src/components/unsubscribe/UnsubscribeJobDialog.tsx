import { friendlyError } from "../../api/client";
import { itemLink, JOB_TITLES, jobRunning, OUTCOME_TEXT, type UnsubscribeJobItem, useUnsubscribeJob } from "../../api/unsubscribe";
import { useMarkSenders, useStopUnsubscribeJob } from "../../hooks/useUnsubscribe";
import { percent } from "../../lib/format";
import { Bar, Button, Dialog, Notice } from "../ui";

/** For a sender CleanSync couldn't finish (spec §4.3): open the page or write to the address,
 * then say it's done with Mark unsubscribed. Nothing for a sender that is done. */
export function ItemActions({ accountId, item }: { accountId: number; item: UnsubscribeJobItem }) {
  const mark = useMarkSenders();
  const link = itemLink(item);
  if (!link) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <a
        href={link.href}
        target={link.mail ? undefined : "_blank"}
        rel="noopener noreferrer"
        className="text-table font-semibold text-accent hover:underline"
      >
        {link.mail ? "Write to it" : "Open page"}
      </a>
      <Button
        tone="quiet"
        disabled={mark.isPending}
        onClick={() => mark.mutate({ accountId, addresses: [item.address], status: "unsubscribed" })}
      >
        Mark unsubscribed
      </Button>
    </span>
  );
}

const itemText = (item: UnsubscribeJobItem) => {
  const what = item.outcome ? OUTCOME_TEXT[item.outcome] : "Waiting";
  return `${item.address}: ${what}${item.note ? ` (${item.note})` : ""}`;
};

/** A job's window (spec §4.1): progress, and a result per sender as it comes in. Closing it
 * leaves the job running; Stop ends it after the current sender. */
export function UnsubscribeJobDialog({ accountId, jobId, onClose }: { accountId: number; jobId: number; onClose: () => void }) {
  const status = useUnsubscribeJob(jobId);
  const job = status.data;
  const stop = useStopUnsubscribeJob();
  const running = !job || jobRunning(job);
  const title = job ? JOB_TITLES[job.kind] : "Unsubscribe";
  return (
    <Dialog
      title={title}
      wide
      onClose={onClose}
      footer={
        <>
          {running && (
            <Button disabled={stop.isPending || job?.stop_asked} onClick={() => stop.mutate({ jobId })}>
              Stop
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {status.isError && !job && <Notice tone="err">{friendlyError(status.error)}</Notice>}
      {job && (
        <>
          <p className="tabular">
            {job.done.toLocaleString()} of {job.total.toLocaleString()} done
          </p>
          <Bar label="Senders done" value={percent(job.done, job.total)} tone="run" />
          {running && <p className="text-ink-2">{job.stop_asked ? "Stopping after this sender…" : "You can close this. It goes on."}</p>}
          {job.reason && <p>{job.reason}</p>}
          <ul aria-label="Results" className="max-h-[50vh] space-y-1.5 overflow-y-auto text-table">
            {job.items.map((item) => (
              <li key={item.address} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 break-words">{itemText(item)}</span>
                <ItemActions accountId={accountId} item={item} />
              </li>
            ))}
          </ul>
        </>
      )}
    </Dialog>
  );
}
