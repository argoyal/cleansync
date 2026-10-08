import { Link } from "react-router-dom";

import { useActivity, useCatalog, useLogs, usePairs } from "../api/queries";
import type { ActivityEvent, Pair } from "../api/types";
import { PairActions } from "../components/PairActions";
import { PairStatusPill } from "../components/StatusBadge";
import { Empty, Notice } from "../components/ui";
import { formatInterval, formatRelative, formatWhen } from "../lib/format";

function PairCard({ pair }: { pair: Pair }) {
  const catalog = useCatalog(pair.id);
  return (
    <article className="rounded-card border border-line p-4">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-section font-semibold">{pair.name}</h2>
        <PairStatusPill status={pair.status} />
        <span className="ml-auto text-meta text-ink-2">{pair.account_email}</span>
      </div>
      <dl className="mb-3 grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-table">
        <dt className="text-ink-2">On this server</dt>
        <dd className="truncate">{pair.local_path}</dd>
        <dt className="text-ink-2">On Google Drive</dt>
        <dd className="truncate">{pair.remote_path || "My Drive"}</dd>
        <dt className="text-ink-2">Last sync</dt>
        <dd>{formatRelative(pair.last_sync_at)}</dd>
        <dt className="text-ink-2">Next sync</dt>
        <dd>{pair.paused ? "Paused" : pair.next_sync_at ? formatRelative(pair.next_sync_at) : formatInterval(pair.schedule_interval_s)}</dd>
        <dt className="text-ink-2">On Drive</dt>
        <dd className="tabular">{catalog.data ? `${catalog.data.files.toLocaleString()} files` : ""}</dd>
      </dl>
      {pair.last_error && (
        <div className="mb-3">
          <Notice tone={pair.status === "ERROR" ? "err" : "warn"}>{pair.last_error}</Notice>
        </div>
      )}
      <PairActions pair={pair} />
    </article>
  );
}

export function LogTail({ pairId, live }: { pairId: number; live: boolean }) {
  const logs = useLogs(pairId, live);
  const lines = logs.data?.lines ?? [];
  return (
    <div>
      <div className="mb-2 flex items-center gap-3">
        <h2 className="text-section font-semibold">Log</h2>
        {logs.data?.job_id && (
          <a className="ml-auto text-table font-semibold text-accent" href={`/api/sync-pairs/${pairId}/logs/${logs.data.job_id}/download`}>
            Download log
          </a>
        )}
      </div>
      <pre className="max-h-80 overflow-auto rounded-card bg-sunken p-3 font-mono text-meta leading-5" aria-live={live ? "polite" : "off"}>
        {lines.length ? lines.join("\n") : "No log yet."}
      </pre>
    </div>
  );
}

const LEVEL_DOT: Record<string, string> = { error: "bg-err", warning: "bg-warn", info: "bg-ok" };

export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) return <p className="text-ink-2">Nothing has happened yet.</p>;
  return (
    <ul className="divide-y divide-line">
      {events.map((e) => (
        <li key={e.id} className="flex gap-3 py-2.5 text-table">
          <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${LEVEL_DOT[e.level] ?? "bg-paused"}`} aria-hidden="true" />
          <span className="flex-1">{e.message}</span>
          <time className="shrink-0 tabular text-ink-2" dateTime={e.ts}>
            {formatWhen(e.ts)}
          </time>
        </li>
      ))}
    </ul>
  );
}

export function ActivityPage() {
  const { data: pairs = [], isPending } = usePairs();
  const activity = useActivity();
  const running = pairs.find((p) => p.status === "RUNNING");
  return (
    <div className="space-y-8 p-4 sm:p-6">
      <h1 className="text-title font-semibold">Sync activity</h1>
      {!isPending && pairs.length === 0 && (
        <Empty icon="sync" title="Nothing is synced yet">
          <Link to="/setup" className="font-semibold text-accent">
            Set up the first folder
          </Link>
        </Empty>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        {pairs.map((p) => (
          <PairCard key={p.id} pair={p} />
        ))}
      </div>
      {running && (
        <section>
          <p className="mb-2 text-table text-ink-2">Live: {running.name}</p>
          <LogTail pairId={running.id} live />
        </section>
      )}
      <section>
        <h2 className="mb-2 text-section font-semibold">What happened</h2>
        <ActivityFeed events={activity.data ?? []} />
      </section>
    </div>
  );
}
