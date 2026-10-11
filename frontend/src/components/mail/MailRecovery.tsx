import { useState } from "react";
import { Link } from "react-router-dom";

import { useMailRecovery } from "../../api/queries";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { formatBytes, formatWhen } from "../../lib/format";
import { OFFLOAD_STATE_TEXT, trashLine } from "../../lib/mail";
import { Button, Dialog } from "../ui";

/** Spec §3.4: Recovery also holds mail offloads and mail deleted in Gmail. */
export function MailRecovery({ accountId }: { accountId: number }) {
  const q = useMailRecovery(accountId);
  const restore = useRestoreMail();
  const [asking, setAsking] = useState(false);
  const pages = q.data?.pages;
  if (!pages) return null;
  const offloads = pages.flatMap((p) => p.offloads);
  const n = pages[0].deleted_count;
  return (
    <section aria-label="Mail in Recovery" className="mt-8 space-y-4">
      <h2 className="text-section font-semibold">Mail</h2>
      <div>
        <h3 className="mb-2 text-table font-semibold">Offloaded mail</h3>
        {offloads.length === 0 ? (
          <p className="text-table text-ink-2">No mail offloaded yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line">
            {offloads.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-table">
                <Link to={`/mail/offloads/${o.id}`} className="font-semibold text-accent">
                  {o.filter_text}
                </Link>
                <span className="tabular text-ink-2">
                  {o.done.toLocaleString()} mails · {formatBytes(o.bytes)} · {OFFLOAD_STATE_TEXT[o.state] ?? o.state} · {formatWhen(o.created_at)}
                </span>
                {o.done > 0 && <span className="basis-full text-ink-2">{trashLine(o)}</span>}
              </li>
            ))}
          </ul>
        )}
        {q.hasNextPage && (
          <Button className="mt-3" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
            Show more
          </Button>
        )}
      </div>
      <div>
        <h3 className="mb-2 text-table font-semibold">Deleted in Gmail</h3>
        {n === 0 ? (
          <p className="text-table text-ink-2">No mail was deleted in Gmail.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-table">
            <span>
              {n.toLocaleString()} {n === 1 ? "mail was" : "mails were"} deleted in Gmail and {n === 1 ? "is" : "are"} kept here.
            </span>
            <Link to="/mail?view=deleted" className="font-semibold text-accent">
              Show them
            </Link>
            <Button disabled={restore.isPending} onClick={() => setAsking(true)}>
              Restore all to Gmail
            </Button>
          </div>
        )}
        {asking && (
          <Dialog
            title={`Put back ${n.toLocaleString()} ${n === 1 ? "mail" : "mails"} you deleted in Gmail?`}
            onClose={() => setAsking(false)}
            footer={
              <>
                <Button onClick={() => setAsking(false)}>Cancel</Button>
                <Button
                  tone="main"
                  data-autofocus
                  onClick={() => {
                    setAsking(false);
                    restore.mutate({ accountId, start: { q: "is:deleted" } });
                  }}
                >
                  Put back
                </Button>
              </>
            }
          >
            <p>They go back into Gmail where they were, with their labels. Mail you deleted on purpose comes back too.</p>
          </Dialog>
        )}
      </div>
    </section>
  );
}
