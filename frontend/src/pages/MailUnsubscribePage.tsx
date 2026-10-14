import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { friendlyError } from "../api/client";
import { useMailTotals } from "../api/queries";
import type { Account } from "../api/types";
import {
  type JobKind,
  jobRunning,
  SENDER_PAGE,
  type Sender,
  type SenderSort,
  sendersSearch,
  type SenderTab,
  SORT_TITLES,
  TAB_TITLES,
  useLatestUnsubscribeJob,
  useSenders,
} from "../api/unsubscribe";
import { DeleteForeverDialog } from "../components/mail/DeleteForeverDialog";
import { OffloadMailDialog } from "../components/mail/OffloadMailDialog";
import { type PickState, type RowAction, SenderTable } from "../components/unsubscribe/SenderTable";
import { UnsubscribeJobDialog } from "../components/unsubscribe/UnsubscribeJobDialog";
import { Button, Dialog, Empty, inputClass, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { useStartUnsubscribeJob } from "../hooks/useUnsubscribe";
import { mailHref } from "../lib/mail";

/** Offload and Delete forever take at most this many senders at once, so the search stays short. */
export const MOST_SENDERS = 50;
export const TOO_MANY_SENDERS = "Pick at most 50 senders for this.";

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

const sendersWord = (n: number) => `${n.toLocaleString()} ${n === 1 ? "sender" : "senders"}`;

/** Delete forever needs the typed count: count the senders' mail in Gmail first. */
function ForeverFor({ account, q, onClose }: { account: Account; q: string; onClose: () => void }) {
  const totals = useMailTotals(account.id, { view: "all", label: "", q });
  if (totals.data) return <DeleteForeverDialog account={account} start={{ view: "all", q }} count={totals.data.total} onClose={onClose} />;
  return (
    <Dialog title="Delete forever" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      {totals.isError ? <Notice tone="err">{friendlyError(totals.error)}</Notice> : <p className="text-ink-2">Counting their mail…</p>}
    </Dialog>
  );
}

type MailAction = { kind: "offload" | "forever"; q: string };

/** Mail → Unsubscribe (0.5.2 spec §3.2, §4, §6): senders whose mail has an unsubscribe link,
 * for the mailbox picked in the account switcher. */
export function MailUnsubscribePage() {
  const { account } = useCurrentAccount();
  if (!account) {
    return (
      <Empty icon="mail" title="No Google account yet">
        Connect a Google account to start.
      </Empty>
    );
  }
  return <SendersView key={account.id} account={account} />;
}

function SendersView({ account }: { account: Account }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<SenderTab>("suggested");
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim(), 300);
  const [sort, setSort] = useState<SenderSort>("mails");
  const [page, setPage] = useState(1);
  const senders = useSenders(account.id, { tab, q, sort, page });
  const data = senders.data;
  const rows = useMemo(() => data?.items ?? [], [data]);
  const latestJob = useLatestUnsubscribeJob(account.id).data;
  const latest = useMemo(() => new Map((latestJob?.items ?? []).map((i) => [i.address, i])), [latestJob]);
  const start = useStartUnsubscribeJob();
  const [jobId, setJobId] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [all, setAll] = useState(false);
  const [tooMany, setTooMany] = useState(false);
  const [mailAction, setMailAction] = useState<MailAction | null>(null);

  const clearPicks = () => {
    setPicked(new Set());
    setAll(false);
    setTooMany(false);
  };
  // Picks belong to one list: a new tab or search starts with none, on the first page.
  useEffect(() => {
    setPicked(new Set());
    setAll(false);
    setTooMany(false);
    setPage(1);
  }, [tab, q]);
  useEffect(() => setPage(1), [sort]);

  const pickState: PickState = all ? "all" : picked.size > 0 ? "some" : "none";
  const isPicked = (address: string) => all || picked.has(address);
  const toggle = (address: string) => {
    // Unticking one row after Pick all leaves a pick by hand: the rows on screen but that one.
    const next = all ? new Set(rows.map((r) => r.address)) : new Set(picked);
    if (next.has(address)) next.delete(address);
    else next.add(address);
    setAll(false);
    setTooMany(false);
    setPicked(next);
  };
  const togglePickAll = () => {
    if (all) return clearPicks();
    setPicked(new Set());
    setTooMany(false);
    setAll(true);
  };

  const startJob = (kind: JobKind, pick: { addresses: string[] } | { all: { tab: SenderTab; q: string } }) =>
    start.mutate(
      { accountId: account.id, kind, ...pick },
      {
        onSuccess: ({ job_id }) => {
          setJobId(job_id);
          clearPicks();
        },
      },
    );
  const startPicked = (kind: JobKind) => startJob(kind, all ? { all: { tab, q } } : { addresses: [...picked] });

  const openMailAction = (kind: MailAction["kind"], addresses: string[]) => setMailAction({ kind, q: sendersSearch(addresses) });
  // The picked senders' mail: by hand, or Pick all when every one of them is on this page.
  const mailForPicked = (kind: MailAction["kind"]) => {
    const total = all ? (data?.total ?? 0) : picked.size;
    if (total > MOST_SENDERS || (all && rows.length < total)) return setTooMany(true);
    const addresses = all ? rows.map((r) => r.address) : [...picked];
    if (addresses.length > 0) openMailAction(kind, addresses);
  };

  const onRow = (action: RowAction, s: Sender) => {
    if (action === "unsubscribe" || action === "auto_archive" || action === "keep") return startJob(action, { addresses: [s.address] });
    if (action === "open") return navigate(mailHref({ q: `from:${s.address}` }));
    openMailAction(action, [s.address]);
  };

  const running = jobRunning(latestJob) ? latestJob : null;
  const pages = data ? Math.max(1, Math.ceil(data.total / SENDER_PAGE)) : 1;
  const scanning = data && data.scan.scanned < data.scan.total;
  const pickedCount = all ? (data?.total ?? 0) : picked.size;
  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h1 className="text-title font-bold">Unsubscribe</h1>
          <p className="text-ink-2">Senders whose mail has an unsubscribe link.</p>
        </div>
        {running && jobId === null && (
          <Button tone="primary" onClick={() => setJobId(running.id)}>
            Running: {running.done.toLocaleString()} of {running.total.toLocaleString()}
          </Button>
        )}
      </div>
      {scanning && (
        <Notice tone="run">
          Scanning old mail: {data.scan.scanned.toLocaleString()} of {data.scan.total.toLocaleString()}…
        </Notice>
      )}
      <div role="tablist" aria-label="Senders" className="flex flex-wrap gap-1 border-b border-line">
        {TAB_TITLES.map(([id, title]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-3 py-2 text-table font-semibold ${tab === id ? "border-accent text-accent" : "border-transparent text-ink-2 hover:text-ink"}`}
          >
            {title}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          aria-label="Search senders"
          placeholder="Search senders"
          className={`${inputClass} max-w-xs`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
        <label className="flex items-center gap-2 text-table text-ink-2">
          Sort by
          <select className={`${inputClass} w-auto`} value={sort} onChange={(e) => setSort(e.target.value as SenderSort)}>
            {SORT_TITLES.map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </div>
      {pickState !== "none" && (
        <section aria-label="Picked senders" className="flex flex-wrap items-center gap-2 rounded-card border border-line bg-sunken px-4 py-2">
          <span className="text-table">{all ? `All ${pickedCount.toLocaleString()} senders picked` : `${sendersWord(pickedCount)} picked`}</span>
          <Button tone="primary" disabled={start.isPending} onClick={() => startPicked("unsubscribe")}>
            Unsubscribe
          </Button>
          <Button disabled={start.isPending} onClick={() => startPicked("auto_archive")}>
            Auto-archive
          </Button>
          <Button disabled={start.isPending} onClick={() => startPicked("keep")}>
            Keep
          </Button>
          <Button onClick={() => mailForPicked("offload")}>Offload their mail</Button>
          {account.permanent_delete && (
            <Button tone="danger" onClick={() => mailForPicked("forever")}>
              Delete forever their mail
            </Button>
          )}
          <Button tone="quiet" onClick={clearPicks}>
            Clear
          </Button>
          {tooMany && (
            <p role="status" className="basis-full text-table text-err">
              {TOO_MANY_SENDERS}
            </p>
          )}
        </section>
      )}
      {senders.isError && <Notice tone="err">{friendlyError(senders.error)}</Notice>}
      {senders.isPending && <p className="text-ink-2">Loading senders…</p>}
      {data && rows.length === 0 && (
        <p className="py-10 text-center text-ink-2">{tab === "suggested" && !q ? "Nothing to suggest. Senders you rarely read show up here." : "No senders here."}</p>
      )}
      {rows.length > 0 && (
        <SenderTable
          accountId={account.id}
          senders={rows}
          latest={latest}
          forever={account.permanent_delete}
          pickState={pickState}
          isPicked={isPicked}
          onToggle={toggle}
          onPickAll={togglePickAll}
          onAction={onRow}
          busy={start.isPending}
        />
      )}
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 text-table">
          <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <span className="tabular text-ink-2">
            Page {page} of {pages}
          </span>
          <Button disabled={page >= pages} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      )}
      {jobId !== null && <UnsubscribeJobDialog accountId={account.id} jobId={jobId} onClose={() => setJobId(null)} />}
      {mailAction?.kind === "offload" && <OffloadMailDialog account={account} start={{ view: "all", q: mailAction.q }} onClose={() => setMailAction(null)} />}
      {mailAction?.kind === "forever" && <ForeverFor account={account} q={mailAction.q} onClose={() => setMailAction(null)} />}
    </div>
  );
}
