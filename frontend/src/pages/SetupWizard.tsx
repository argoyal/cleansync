import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { useAccounts, usePair, usePreview, useSetup } from "../api/queries";
import type { Pair, Preview } from "../api/types";
import { FolderPicker } from "../components/FolderPicker";
import { Icon } from "../components/Icon";
import { Button, Field, inputClass, Notice } from "../components/ui";
import { CONFIRM_PHRASE } from "../config";
import { useLiveEvents } from "../hooks/live";
import { useConnectGoogle } from "../hooks/useConnect";
import { Brand } from "./LoginPage";

const STEPS = ["Password", "Google account", "Folders", "Preview", "Start"] as const;

function Steps({ current }: { current: number }) {
  return (
    <ol className="mb-8 flex flex-wrap gap-x-6 gap-y-2 text-table" aria-label="Setup steps">
      {STEPS.map((s, i) => (
        <li key={s} aria-current={i === current ? "step" : undefined} className={`flex items-center gap-2 ${i === current ? "font-semibold text-ink" : "text-ink-2"}`}>
          <span
            className={`grid h-6 w-6 place-items-center rounded-full text-meta ${
              i < current ? "bg-ok text-white" : i === current ? "bg-accent text-accent-ink" : "bg-sunken"
            }`}
          >
            {i < current ? <Icon name="check_circle" className="text-[14px]" /> : i + 1}
          </span>
          {s}
        </li>
      ))}
    </ol>
  );
}

/**
 * First run: password (already done to get here) → Google account → folders → preview →
 * typed confirmation → start. The step lives in the address (?step=&pair=), so coming back
 * from Google's sign-in page lands on the right step.
 */
export function SetupWizard() {
  useLiveEvents();
  const [params, setParams] = useSearchParams();
  const pairId = params.get("pair") ? Number(params.get("pair")) : null;
  const accounts = useAccounts();
  const usable = (accounts.data ?? []).filter((a) => a.status !== "DISCONNECTED");
  const step = params.get("step") ?? (usable.length ? "folders" : "account");
  const go = (next: string, extra: Record<string, string> = {}) => setParams({ step: next, ...extra });
  const index = { account: 1, folders: 2, preview: 3, start: 4 }[step] ?? 1;
  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
      <div className="mb-8 flex items-center justify-between">
        <Brand big />
        <Link to="/" className="text-table text-ink-2 hover:text-ink">
          Skip for now
        </Link>
      </div>
      <Steps current={index} />
      {step === "account" && <AccountStep onNext={() => go("folders")} />}
      {step === "folders" && <FoldersStep onCreated={(p) => go("preview", { pair: String(p.id) })} />}
      {step === "preview" && pairId !== null && <PreviewStep pairId={pairId} onNext={() => go("start", { pair: String(pairId) })} />}
      {step === "start" && pairId !== null && <StartStep pairId={pairId} />}
    </main>
  );
}

function AccountStep({ onNext }: { onNext: () => void }) {
  const setup = useSetup();
  const accounts = useAccounts();
  const connect = useConnectGoogle();
  const usable = (accounts.data ?? []).filter((a) => a.status !== "DISCONNECTED");
  return (
    <section className="space-y-5">
      <h1 className="text-hero font-semibold tracking-tight">Connect a Google account</h1>
      <p className="max-w-prose text-ink-2">
        DriveSync uses your own Google sign-in client. In the Google Cloud console, add this address to the client's
        allowed redirect addresses:
      </p>
      {setup.data && (
        <div className="flex items-center gap-2">
          <code className="block min-w-0 flex-1 select-all break-all rounded-ctl bg-sunken px-3 py-2 text-table">{setup.data.redirect_uri}</code>
          <Button
            icon="content_copy"
            onClick={() => {
              void navigator.clipboard?.writeText(setup.data.redirect_uri).catch(() => undefined);
            }}
          >
            Copy
          </Button>
        </div>
      )}
      {setup.data && !setup.data.oauth_configured && (
        <Notice tone="warn">
          No Google client is set yet. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and restart DriveSync (see
          docs/google-oauth-setup.md).
        </Notice>
      )}
      {setup.data?.insecure_http && (
        <Notice tone="warn">This address uses plain http. Use https before connecting from outside your home network.</Notice>
      )}
      {usable.length > 0 && (
        <Notice tone="ok">
          Connected: {usable.map((a) => a.email).join(", ")}.{" "}
          <button type="button" className="font-semibold text-accent" onClick={onNext}>
            Continue
          </button>
        </Notice>
      )}
      {connect.isError && <Notice tone="err">{friendlyError(connect.error)}</Notice>}
      <Button tone="primary" icon="person_add" disabled={connect.isPending || setup.data?.oauth_configured === false} onClick={() => connect.mutate(undefined)}>
        {usable.length ? "Add another Google account" : "Sign in with Google"}
      </Button>
    </section>
  );
}

function FoldersStep({ onCreated }: { onCreated: (pair: Pair) => void }) {
  const accounts = (useAccounts().data ?? []).filter((a) => a.status !== "DISCONNECTED");
  const [accountId, setAccountId] = useState<number | null>(null);
  const acc = accounts.find((a) => a.id === accountId) ?? accounts[0];
  const [remote, setRemote] = useState("");
  const [local, setLocal] = useState("");
  const [name, setName] = useState("My Drive");
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      api<Pair>("/api/sync-pairs", {
        method: "POST",
        body: { account_id: acc?.id, name: name.trim(), remote_path: remote, local_subpath: local },
      }),
    onSuccess: (pair) => {
      qc.invalidateQueries({ queryKey: ["pairs"] });
      onCreated(pair);
    },
  });
  if (!acc) return <Notice tone="warn">Connect a Google account first.</Notice>;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form onSubmit={submit} className="space-y-6">
      <h1 className="text-hero font-semibold tracking-tight">Choose what to sync</h1>
      {accounts.length > 1 && (
        <Field label="Google account">
          <select className={inputClass} value={acc.id} onChange={(e) => setAccountId(Number(e.target.value))}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.email}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Name for this sync">
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <p className="mb-2 text-table font-medium text-ink-2">Folder on Google Drive</p>
          <FolderPicker source="remote" accountId={acc.id} value={remote} onChange={setRemote} rootLabel="My Drive" />
        </div>
        <div>
          <p className="mb-2 text-table font-medium text-ink-2">Folder on this server</p>
          <FolderPicker source="local" accountId={acc.id} value={local} onChange={setLocal} rootLabel={acc.email} />
        </div>
      </div>
      <p className="text-ink-2">
        Google Drive <strong className="text-ink">{remote || "My Drive"}</strong> will stay in step with{" "}
        <strong className="text-ink">{local ? `${acc.email}/${local}` : acc.email}</strong> on this server. Nothing moves until you
        have seen the preview.
      </p>
      {create.isError && <Notice tone="err">{friendlyError(create.error)}</Notice>}
      <Button tone="primary" type="submit" disabled={!name.trim() || create.isPending}>
        Create and preview
      </Button>
    </form>
  );
}

function Count({ n, label }: { n: number; label: string }) {
  return (
    <div className="rounded-card border border-line p-4">
      <p className="text-hero font-semibold tabular">{n.toLocaleString()}</p>
      <p className="text-table text-ink-2">{label}</p>
    </div>
  );
}

function Samples({ title, paths, total }: { title: string; paths: string[]; total: number }) {
  if (!total) return null;
  return (
    <details className="rounded-card border border-line px-4 py-3">
      <summary className="cursor-pointer font-semibold">
        {title} ({total.toLocaleString()})
      </summary>
      <ul className="mt-2 max-h-56 overflow-y-auto font-mono text-meta">
        {paths.map((p) => (
          <li key={p} className="truncate py-0.5">
            {p}
          </li>
        ))}
      </ul>
      {total > paths.length && <p className="mt-2 text-meta text-ink-2">Showing the first {paths.length}.</p>}
    </details>
  );
}

export function PreviewSummary({ preview }: { preview: Preview }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Count n={preview.same} label="Already the same" />
        <Count n={preview.only_local} label="Only on this server (upload)" />
        <Count n={preview.only_remote} label="Only on Drive (download)" />
        <Count n={preview.different} label="Different (newer one wins)" />
      </div>
      <p className="max-w-prose text-ink-2">
        Starting will merge the two folders: files only on one side are copied to the other, nothing is deleted, and where both
        sides have a different version the newer one wins. Older local versions are saved to backups first.
      </p>
      {preview.errors > 0 && (
        <Notice tone="err">
          rclone could not compare {preview.errors} files, so DriveSync will refuse to start. Fix these and compare again.
        </Notice>
      )}
      <Samples title="Upload to Google Drive" paths={preview.samples.only_local} total={preview.only_local} />
      <Samples title="Download to this server" paths={preview.samples.only_remote} total={preview.only_remote} />
      <Samples title="Different on each side" paths={preview.samples.different} total={preview.different} />
      <Samples title="Could not compare" paths={preview.samples.errors} total={preview.errors} />
    </div>
  );
}

function PreviewStep({ pairId, onNext }: { pairId: number; onNext: () => void }) {
  const pair = usePair(pairId);
  const [started, setStarted] = useState(false);
  const preview = usePreview(pairId, started);
  const start = useMutation({
    mutationFn: () => api(`/api/sync-pairs/${pairId}/preview`, { method: "POST" }),
    onSuccess: () => setStarted(true),
  });
  const [phrase, setPhrase] = useState("");
  const confirm = useMutation({
    mutationFn: () => api(`/api/sync-pairs/${pairId}/confirm-initial`, { method: "POST", body: { phrase } }),
    onSuccess: onNext,
  });
  const failed = started && !preview.data && pair.data?.last_error;
  return (
    <section className="space-y-6">
      <h1 className="text-hero font-semibold tracking-tight">Check before anything moves</h1>
      <p className="max-w-prose text-ink-2">
        DriveSync compares both folders without changing either. The first sync never deletes anything: files on one side are
        copied to the other, and for files that differ the newer one wins (the older local version is saved to backups first).
      </p>
      {!preview.data && !started && (
        <Button tone="primary" icon="compare_arrows" onClick={() => start.mutate()} disabled={start.isPending}>
          Compare the folders
        </Button>
      )}
      {start.isError && <Notice tone="err">{friendlyError(start.error)}</Notice>}
      {started && !preview.data && !failed && <Notice tone="run">Comparing. On a large Drive this can take a few minutes.</Notice>}
      {failed && <Notice tone="err">{pair.data?.last_error}</Notice>}
      {preview.data && (
        <>
          <PreviewSummary preview={preview.data} />
          <form
            className="space-y-3 rounded-card border border-line p-4"
            onSubmit={(e) => {
              e.preventDefault();
              confirm.mutate();
            }}
          >
            <Field label={`To start, type: ${CONFIRM_PHRASE}`}>
              <input className={inputClass} value={phrase} onChange={(e) => setPhrase(e.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
            {confirm.isError && <Notice tone="err">{friendlyError(confirm.error)}</Notice>}
            <Button tone="primary" type="submit" disabled={phrase !== CONFIRM_PHRASE || confirm.isPending || preview.data.errors > 0}>
              Start the first sync
            </Button>
          </form>
        </>
      )}
    </section>
  );
}

function StartStep({ pairId }: { pairId: number }) {
  const pair = usePair(pairId);
  const navigate = useNavigate();
  const p = pair.data;
  const done = p?.initialized && p.status !== "RUNNING";
  return (
    <section className="space-y-6">
      <h1 className="text-hero font-semibold tracking-tight">{done ? "Synced" : "First sync running"}</h1>
      {!done && !p?.last_error && <Notice tone="run">Copying in both directions. You can leave this page; it keeps going.</Notice>}
      {p?.last_error && <Notice tone="err">{p.last_error}</Notice>}
      {done && !p?.last_error && <Notice tone="ok">Both sides are in step. From now on DriveSync keeps them that way.</Notice>}
      <div className="flex flex-wrap gap-2">
        <Button tone="primary" icon="folder" onClick={() => navigate(`/drive?path=${encodeURIComponent(p?.local_subpath ?? "")}`)}>
          Open My Drive
        </Button>
        <Button icon="sync" onClick={() => navigate(`/activity/${pairId}`)}>
          Watch sync activity
        </Button>
      </div>
    </section>
  );
}
