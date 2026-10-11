import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, ApiError, friendlyError } from "../api/client";
import { recoveryFileUrl, recoveryZipUrl, usePairs, useRecovery, useStatus } from "../api/queries";
import type { RecoveryEntry, RecoveryKind, RestoreResult } from "../api/types";
import { MailRecovery } from "../components/mail/MailRecovery";
import { FILE_QUERY_KEYS, startDownload } from "../components/FileActions";
import { Button, Empty, Notice, inputClass } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { formatBytes, formatDay } from "../lib/format";

const KIND: Record<RecoveryKind, string> = {
  sync_backup: "Replaced or deleted by a sync",
  deleted: "Deleted in CleanSync",
  first_sync: "Saved before the first sync",
  resync: "Saved before a safe resync",
  conflict: "Conflict: the version not kept",
  offload: "Offloaded, in Drive's trash",
};
const PAGE = 200;

function cleanup(e: RecoveryEntry): string {
  if (e.kind === "offload" && e.google_emptied) return "Google has emptied this from its trash";
  if (e.kind === "offload") return e.expires_on ? `Google empties its trash around ${formatDay(e.expires_on)}` : "";
  // expires_on is the first day the clean-up may remove it.
  return e.expires_on ? `Cleaned up on or after ${formatDay(e.expires_on)}` : "No clean-up date (automatic clean-up is off)";
}

/** Only these can be picked: offloads go back from the Offloaded page; orphans are downloaded from their row. */
const pickable = (e: RecoveryEntry) => e.kind !== "offload" && e.original_path !== null;

/** Spec §8: everything CleanSync kept, where it came from, and a way back. */
export function RecoveryPage() {
  const { accountId } = useCurrentAccount();
  const [params, setParams] = useSearchParams();
  const pair = params.get("pair") ?? "";
  const kind = params.get("kind") ?? "";
  const path = params.get("path") ?? "";
  const source = params.get("source") ?? "";
  const [limit, setLimit] = useState(PAGE);
  const list = useRecovery(source === "mail" ? null : accountId, { pair, kind, path, limit });
  const pairs = (usePairs().data ?? []).filter((p) => p.account_id === accountId);
  const stopped = useStatus().data?.sync_stopped ?? null;
  const [checked, setChecked] = useState<string[]>([]);
  const [results, setResults] = useState<RestoreResult[] | null>(null);
  const qc = useQueryClient();
  const restore = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ results: RestoreResult[]; syncs: Record<string, string> }>("/api/recovery/restore", {
        method: "POST",
        body: { account_id: accountId, ids },
      }),
    onMutate: () => setResults(null),
    onSuccess: (out) => {
      setResults(out.results);
      setChecked([]);
      [...FILE_QUERY_KEYS, "recovery"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    },
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["recovery"] });
  // Download problems: said in plain words on the page, never a raw error page.
  const [problem, setProblem] = useState<string | null>(null);
  const zip = useMutation({
    // The ids go in the body: hundreds of long paths would not fit in an address.
    mutationFn: (ids: string[]) =>
      api<{ token: string }>("/api/recovery/zip", { method: "POST", body: { account_id: accountId, ids } }),
    onMutate: () => setProblem(null),
    onSuccess: (out) => startDownload(recoveryZipUrl(out.token)),
    onError: (e) => {
      setProblem(friendlyError(e));
      if (e instanceof ApiError && e.status === 409) void refresh(); // the cleaned-up ones drop out
    },
  });
  const downloadOne = async (e: RecoveryEntry) => {
    if (accountId === null) return;
    setProblem(null);
    const href = recoveryFileUrl(accountId, e.id);
    let res: Response;
    try {
      res = await fetch(href, { method: "HEAD", credentials: "same-origin" });
    } catch {
      setProblem("Can't reach CleanSync. Check your connection and try again.");
      return;
    }
    if (res.ok) startDownload(href);
    else if (res.status === 404) {
      setProblem(`${e.name} is no longer in Recovery (it was cleaned up or removed).`);
      void refresh();
    } else setProblem(`Couldn't download ${e.name} (error ${res.status}).`);
  };
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
    setChecked([]);
    setLimit(PAGE);
  };
  const toggle = (id: string) => setChecked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  if (accountId === null) return null;
  const entries = list.data?.entries ?? [];
  const byId = new Map(entries.map((e) => [e.id, e]));
  // Only what is on screen counts: a row that went away (cleaned up, restored) drops out.
  const picked = checked.filter((id) => byId.has(id));
  const restorable = picked.filter((id) => byId.get(id)?.restorable);
  const nameOf = (id: string) => byId.get(id)?.name ?? id;
  const busy = restore.isPending || !!stopped;

  if (source === "mail") {
    return (
      <div className="p-4 sm:p-6">
        <h1 className="mb-2 text-title font-semibold">Recovery</h1>
        <MailRecovery accountId={accountId} />
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6">
      <h1 className="mb-2 text-title font-semibold">Recovery</h1>
      <p className="mb-4 max-w-3xl text-ink-2">
        Copies CleanSync kept: files replaced or deleted by a sync, items deleted here, versions not kept in a conflict,
        and files it sent to Google Drive's trash in an offload. Restore puts a copy back where it was and never
        overwrites: if that name is taken, it comes back next to it as "name (restored)".
      </p>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-table">
          <span className="mb-1 block text-ink-2">Sync folder</span>
          <select className={inputClass} value={pair} onChange={(e) => set("pair", e.target.value)}>
            <option value="">All</option>
            {pairs.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
            <option value="unsynced">Not in a sync folder</option>
          </select>
        </label>
        <label className="text-table">
          <span className="mb-1 block text-ink-2">Came from</span>
          <select className={inputClass} value={kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">Anything</option>
            {(Object.keys(KIND) as RecoveryKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND[k]}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap gap-2 sm:ml-auto">
          <Button tone="primary" icon="history" disabled={restorable.length === 0 || busy} onClick={() => restore.mutate(restorable)}>
            Restore selected ({restorable.length})
          </Button>
          {picked.length > 0 && (
            <Button icon="download" disabled={zip.isPending} onClick={() => zip.mutate(picked)}>
              Download selected ({picked.length})
            </Button>
          )}
        </div>
      </div>
      {path && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-table">
          <p className="min-w-0 break-all">
            Only items from <span className="font-medium">{path}</span>
          </p>
          <Button tone="quiet" onClick={() => set("path", "")}>
            Show everything
          </Button>
        </div>
      )}
      {stopped && (
        <div className="mb-4">
          <Notice tone="err">Restore is paused. {stopped}</Notice>
        </div>
      )}
      <div aria-live="polite">
        {problem && (
          <div className="mb-4">
            <Notice tone="warn">{problem}</Notice>
          </div>
        )}
        {restore.isError && (
          <div className="mb-4">
            <Notice tone="err">{friendlyError(restore.error)}</Notice>
          </div>
        )}
        {results && (
          <div className="mb-4">
            <Notice tone={results.some((r) => r.error) ? "warn" : "ok"}>
              <ul>
                {results.map((r) => (
                  <li key={r.id} className="break-all">
                    {r.restored_to ? `Restored to ${r.restored_to}` : `${nameOf(r.id)}: not restored. ${r.error ?? ""}`}
                  </li>
                ))}
              </ul>
            </Notice>
          </div>
        )}
      </div>
      {list.isError && <Notice tone="err">{friendlyError(list.error)}</Notice>}
      {list.data && entries.length === 0 && <Empty icon="history" title="Nothing in Recovery" />}
      {entries.length > 0 && (
        <table className="w-full table-fixed text-table">
          <thead className="text-left text-meta text-ink-2">
            <tr className="border-b border-line">
              <th className="w-8">
                <span className="sr-only">Select</span>
              </th>
              <th className="py-2 font-medium">Item</th>
              <th className="w-24 text-right font-medium max-sm:hidden">Size</th>
              <th className="w-28 text-right font-medium sm:w-40">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-line/60 align-top">
                <td className="py-2">
                  <input
                    type="checkbox"
                    aria-label={`Select ${e.name}`}
                    disabled={!pickable(e)}
                    checked={checked.includes(e.id)}
                    onChange={() => toggle(e.id)}
                  />
                </td>
                <td className="py-2 pr-2">
                  <p className="break-all font-medium">{e.original_path ?? e.name}</p>
                  {e.original_path === null && <p className="text-meta text-ink-2">Its sync folder was removed; download it instead.</p>}
                  <p className="text-meta text-ink-2">
                    <span>{e.google_emptied ? "Offloaded" : KIND[e.kind]}</span> · <span>{formatDay(e.day)}</span>
                    {e.pair_name && <span className="max-sm:hidden"> · {e.pair_name}</span>}
                  </p>
                  <p className="text-meta text-ink-2">{cleanup(e)}</p>
                </td>
                <td className="py-2 text-right tabular text-ink-2 max-sm:hidden">{formatBytes(e.size)}</td>
                <td className="py-2 text-right">
                  {e.kind === "offload" && e.google_emptied ? null : e.kind === "offload" ? (
                    <Link to="/offloaded" className="font-semibold text-accent">
                      Open Offloaded
                    </Link>
                  ) : (
                    <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
                      {e.restorable && (
                        <button
                          type="button"
                          className="font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-50"
                          aria-label={`Restore ${e.name}`}
                          disabled={busy}
                          onClick={() => restore.mutate([e.id])}
                        >
                          Restore
                        </button>
                      )}
                      <button type="button" className="font-semibold text-accent" aria-label={`Download ${e.name}`} onClick={() => void downloadOne(e)}>
                        Download
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {list.data && list.data.total > entries.length && (
        <Button className="mt-4" disabled={list.isPlaceholderData} onClick={() => setLimit((l) => l + PAGE)}>
          Show more ({list.data.total - entries.length} more)
        </Button>
      )}
      {source !== "files" && <MailRecovery accountId={accountId} />}
    </div>
  );
}
