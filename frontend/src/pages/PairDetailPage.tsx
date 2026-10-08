import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { useActivity, useCatalog, useHistory, usePair, usePairJobs, usePlanned } from "../api/queries";
import type { Pair } from "../api/types";
import { PairActions, usePairAction } from "../components/PairActions";
import { PairStatusPill } from "../components/StatusBadge";
import { useToast } from "../components/Toasts";
import { Button, Dialog, Field, inputClass, Notice } from "../components/ui";
import { formatBytes, formatInterval, formatRelative, formatWhen } from "../lib/format";
import { ActivityFeed, LogTail } from "./ActivityPage";
import { ConflictList } from "./ConflictsPage";

const TABS = ["overview", "activity", "files", "conflicts", "history", "logs", "settings"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview",
  activity: "Activity",
  files: "Files",
  conflicts: "Conflicts",
  history: "History",
  logs: "Logs",
  settings: "Settings",
};

export function PairDetailPage() {
  const id = Number(useParams().pairId);
  const [params, setParams] = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as Tab) : "overview";
  const pair = usePair(id);
  if (pair.isError) return <div className="p-6"><Notice tone="err">{friendlyError(pair.error)}</Notice></div>;
  if (!pair.data) return <p className="p-6 text-ink-2">Loading</p>;
  const p = pair.data;
  return (
    <div className="p-4 sm:p-6">
      <Link to="/activity" className="text-table text-accent">
        Sync activity
      </Link>
      <div className="mb-4 mt-1 flex flex-wrap items-center gap-3">
        <h1 className="text-title font-semibold">{p.name}</h1>
        <PairStatusPill status={p.status} />
      </div>
      <div role="tablist" aria-label="Pair details" className="mb-6 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            type="button"
            aria-selected={tab === t}
            className={`-mb-px border-b-2 px-3 py-2 text-table font-medium ${tab === t ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}
            onClick={() => setParams({ tab: t })}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "overview" && <Overview pair={p} />}
        {tab === "activity" && <PairActivity pairId={p.id} />}
        {tab === "files" && (
          <p>
            <Link className="font-semibold text-accent" to={`/drive?path=${encodeURIComponent(p.local_subpath)}`}>
              Browse the files of {p.name}
            </Link>
          </p>
        )}
        {tab === "conflicts" && <ConflictList pair={p} />}
        {tab === "history" && <History pairId={p.id} />}
        {tab === "logs" && <LogTail pairId={p.id} live={p.status === "RUNNING"} />}
        {tab === "settings" && <PairSettings pair={p} />}
      </div>
    </div>
  );
}

function Overview({ pair }: { pair: Pair }) {
  const catalog = useCatalog(pair.id);
  const act = usePairAction(pair.id);
  const c = catalog.data;
  return (
    <div className="space-y-6">
      {pair.last_error && <Notice tone={pair.status === "ERROR" ? "err" : "warn"}>{pair.last_error}</Notice>}
      <PairActions pair={pair} details={false} />
      <dl className="grid max-w-2xl grid-cols-[auto,1fr] gap-x-6 gap-y-2 text-table">
        <dt className="text-ink-2">On this server</dt>
        <dd className="break-all">{pair.local_path}</dd>
        <dt className="text-ink-2">On Google Drive</dt>
        <dd>{pair.remote_path || "My Drive"}</dd>
        <dt className="text-ink-2">Direction</dt>
        <dd>{pair.direction === "BIDIRECTIONAL" ? "Both ways" : pair.direction === "REMOTE_TO_LOCAL" ? "Drive to server only" : "Server to Drive only"}</dd>
        <dt className="text-ink-2">Schedule</dt>
        <dd>{formatInterval(pair.schedule_interval_s)}</dd>
        <dt className="text-ink-2">Last success</dt>
        <dd>{formatRelative(pair.last_success_at)}</dd>
        <dt className="text-ink-2">Deletion limit</dt>
        <dd>{pair.max_deletions} files per sync</dd>
        <dt className="text-ink-2">Drive listing</dt>
        <dd>
          {c?.refreshed_at
            ? `${c.files.toLocaleString()} files, ${formatBytes(c.bytes)}, ${c.native_docs} Google files, listed ${formatRelative(c.refreshed_at)}`
            : "Not listed yet"}
          {c?.error && <span className="block text-err">{c.error}</span>}
        </dd>
      </dl>
      <Button
        icon="refresh"
        disabled={catalog.data?.refreshing || act.isPending}
        onClick={() => act.mutate({ action: "catalog/refresh", done: "Listing Google Drive again." })}
      >
        Refresh Drive listing
      </Button>
    </div>
  );
}

function PairActivity({ pairId }: { pairId: number }) {
  const { data = [] } = useActivity(pairId);
  return <ActivityFeed events={data} />;
}

const STATE_COLOR: Record<string, string> = {
  SUCCESS: "text-ok",
  FAILED: "text-err",
  BLOCKED: "text-warn",
  RUNNING: "text-run",
  INTERRUPTED: "text-warn",
};

function History({ pairId }: { pairId: number }) {
  const { data = [] } = useHistory(pairId);
  if (data.length === 0) return <p className="text-ink-2">No syncs yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-table">
        <thead className="text-left text-meta text-ink-2">
          <tr className="border-b border-line">
            <th className="py-2 font-medium">Started</th>
            <th className="font-medium">Result</th>
            <th className="font-medium">Up</th>
            <th className="font-medium">Down</th>
            <th className="font-medium">Deleted</th>
            <th className="font-medium">Conflicts</th>
            <th className="font-medium">Took</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {data.map((j) => (
            <tr key={j.id} className="border-b border-line/60">
              <td className="py-2">{formatWhen(j.started_at)}</td>
              <td className={STATE_COLOR[j.state] ?? ""} title={j.error_message ?? undefined}>
                {j.state.charAt(0) + j.state.slice(1).toLowerCase()}
                {j.override_used ? " (approved)" : ""}
              </td>
              <td>{j.files_uploaded}</td>
              <td>{j.files_downloaded}</td>
              <td>{j.files_deleted_local + j.files_deleted_remote}</td>
              <td>{j.conflicts}</td>
              <td>{j.duration_s !== null ? `${Math.round(j.duration_s)} s` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const INTERVALS: [number | null, string][] = [
  [60, "Every minute"],
  [300, "Every 5 minutes"],
  [900, "Every 15 minutes"],
  [1800, "Every 30 minutes"],
  [3600, "Every hour"],
  [null, "Manual only"],
];
const RETENTION: [number | null, string][] = [
  [7, "7 days"],
  [30, "30 days"],
  [90, "90 days"],
  [null, "Keep forever"],
];

function PairSettings({ pair }: { pair: Pair }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState(pair.name);
  const [interval, setInterval] = useState<number | null>(pair.schedule_interval_s);
  const [limit, setLimit] = useState(pair.max_deletions);
  const [backups, setBackups] = useState(pair.backups_enabled);
  const [retention, setRetention] = useState<number | null>(pair.backup_retention_days);
  const [removing, setRemoving] = useState(false);
  const save = useMutation({
    mutationFn: () =>
      api(`/api/sync-pairs/${pair.id}`, {
        method: "PUT",
        body: { name, schedule_interval_s: interval, max_deletions: limit, backups_enabled: backups, backup_retention_days: retention },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pairs"] });
      toast("Saved.");
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/sync-pairs/${pair.id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pairs"] });
      toast("Sync removed. No files were deleted.");
      navigate("/activity");
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const pick = (v: string) => (v === "" ? null : Number(v));
  return (
    <form onSubmit={submit} className="max-w-lg space-y-4">
      <Field label="Name">
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Sync">
        <select className={inputClass} value={interval ?? ""} onChange={(e) => setInterval(pick(e.target.value))}>
          {INTERVALS.map(([v, l]) => (
            <option key={l} value={v ?? ""}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Deletion limit" hint="A sync that would delete more files than this stops and asks you first.">
        <input className={inputClass} type="number" min={0} value={limit} onChange={(e) => setLimit(Math.max(0, Number(e.target.value)))} />
      </Field>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={backups} onChange={(e) => setBackups(e.target.checked)} />
        Keep a copy of local files before a sync changes or deletes them
      </label>
      <Field label="Keep those copies for">
        <select className={inputClass} value={retention ?? ""} onChange={(e) => setRetention(pick(e.target.value))}>
          {RETENTION.map(([v, l]) => (
            <option key={l} value={v ?? ""}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      {save.isError && <Notice tone="err">{friendlyError(save.error)}</Notice>}
      <div className="flex flex-wrap gap-2">
        <Button tone="primary" type="submit" disabled={save.isPending || !name.trim()}>
          Save
        </Button>
        <Button tone="quiet" className="text-err" onClick={() => setRemoving(true)}>
          Remove this sync
        </Button>
      </div>
      {removing && (
        <Dialog
          title={`Remove "${pair.name}"?`}
          onClose={() => setRemoving(false)}
          footer={
            <>
              <Button onClick={() => setRemoving(false)} data-autofocus>
                Cancel
              </Button>
              <Button tone="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Remove
              </Button>
            </>
          }
        >
          <p>Syncing stops. Files stay where they are, on this server and on Google Drive.</p>
        </Dialog>
      )}
    </form>
  );
}

export function BlockedPage() {
  const id = Number(useParams().pairId);
  const pair = usePair(id);
  const planned = usePlanned(id);
  const act = usePairAction(id);
  const jobs = usePairJobs(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [limit, setLimit] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const changeLimit = useMutation({
    mutationFn: (max: number) => api(`/api/sync-pairs/${id}`, { method: "PUT", body: { max_deletions: max } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pairs"] }),
  });
  const p = pair.data;
  const changes = planned.data?.changes ?? [];
  const deletes = changes.filter((c) => c.action === "delete");
  const local = deletes.filter((c) => c.side === "local");
  const remote = deletes.filter((c) => c.side === "remote");
  // The list is capped (deletes first); the blocked job's own count is the true total when we have it.
  const blockedJob = (jobs.data ?? []).find((j) => j.id === planned.data?.job_id);
  const total = Math.max(deletes.length, blockedJob?.planned_deletions ?? 0);
  const cutShort = total > deletes.length;
  if (!p) return <p className="p-6 text-ink-2">Loading</p>;
  return (
    <div className="max-w-4xl space-y-6 p-4 sm:p-6">
      <Link to={`/activity/${id}`} className="text-table text-accent">
        {p.name}
      </Link>
      <h1 className="text-hero font-semibold tracking-tight">Synchronization blocked</h1>
      {p.attention_reason !== "BLOCKED" ? (
        <Notice tone="ok">Nothing is blocked for this pair right now.</Notice>
      ) : (
        <Notice tone="warn">
          This sync would delete {total.toLocaleString()} files; your limit is {p.max_deletions}. Nothing has been deleted yet,
          and nothing has changed on either side.
        </Notice>
      )}
      {cutShort && (
        <Notice tone="warn">
          The lists below are cut short: they show {deletes.length.toLocaleString()} of the {total.toLocaleString()} files that would be deleted.
        </Notice>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {[
          { title: "Would be deleted on this server", list: local },
          { title: "Would be deleted on Google Drive (to Drive's trash)", list: remote },
        ].map(({ title, list }) => (
          <section key={title} className="rounded-card border border-line p-4">
            <h2 className="mb-2 font-semibold">
              {title} ({list.length})
            </h2>
            <ul className="max-h-72 overflow-y-auto font-mono text-meta">
              {list.map((c) => (
                <li key={c.path} className="truncate py-0.5">
                  {c.path}
                </li>
              ))}
              {list.length === 0 && <li className="font-sans text-ink-2">None</li>}
            </ul>
          </section>
        ))}
      </div>
      {p.attention_reason === "BLOCKED" && (
        <div className="flex flex-wrap items-end gap-4">
          <Button tone="primary" disabled={act.isPending} onClick={() => setConfirming(true)}>
            Override once
          </Button>
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (limit !== null) changeLimit.mutate(limit);
            }}
          >
            <Field label="New deletion limit">
              <input className={`${inputClass} w-32`} type="number" min={0} value={limit ?? p.max_deletions} onChange={(e) => setLimit(Number(e.target.value))} />
            </Field>
            <Button type="submit" disabled={limit === null || changeLimit.isPending}>
              Change limit
            </Button>
          </form>
        </div>
      )}
      {changeLimit.isSuccess && <Notice tone="ok">Limit changed. Use Sync now on the pair to try again.</Notice>}
      {confirming && (
        <Dialog
          title="Allow these deletions once?"
          onClose={() => setConfirming(false)}
          footer={
            <>
              <Button onClick={() => setConfirming(false)}>Cancel</Button>
              <Button
                tone="danger"
                onClick={() => {
                  setConfirming(false);
                  act.mutate(
                    { action: "override-once", done: "Running this sync once with the deletions you reviewed." },
                    { onSuccess: () => navigate(`/activity/${id}`) },
                  );
                }}
              >
                Allow once
              </Button>
            </>
          }
        >
          <p>
            The next sync may delete up to {total.toLocaleString()} files, the number in the plan you reviewed. This approval is not a new limit: if the plan has grown since, it stops again. Files
            deleted on Google Drive go to Drive's trash; files deleted here are kept in backups when backups are on for this pair.
          </p>
        </Dialog>
      )}
    </div>
  );
}
