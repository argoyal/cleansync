import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError, friendlyError } from "../api/client";
import { useAccounts, usePair, usePairJobs, usePreview, useSetup } from "../api/queries";
import type { Job, Pair, Preview } from "../api/types";
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

/** The step's heading; takes focus when the step appears so screen readers and keyboards follow the change. */
function StepHeading({ children }: { children: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <h1 ref={ref} tabIndex={-1} className="text-hero font-semibold tracking-tight outline-none">
      {children}
    </h1>
  );
}

const FAILED_JOB = ["FAILED", "BLOCKED", "INTERRUPTED", "CANCELLED"];
const ACTIVE_JOB = ["QUEUED", "RUNNING"];
const latest = (jobs: Job[] | undefined, keep: (j: Job) => boolean) =>
  (jobs ?? []).filter(keep).reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);

/**
 * First run: password (already done to get here) → Google account → folders → preview →
 * typed confirmation → start. The step lives in the address (?step=&pair=), so coming back
 * from Google's sign-in page lands on the right step.
 */
export const SETUP_SKIPPED_KEY = "drivesync.setupSkipped";

/** Remember for this browser session that the person chose to look around first. */
function skipSetup() {
  try {
    sessionStorage.setItem(SETUP_SKIPPED_KEY, "1");
  } catch {
    // a private window: Home may offer the wizard again
  }
}

/** Whether the person skipped the wizard earlier in this browser session. */
export function setupSkipped(): boolean {
  try {
    return sessionStorage.getItem(SETUP_SKIPPED_KEY) === "1";
  } catch {
    return false;
  }
}

export function SetupWizard() {
  useLiveEvents();
  const [params, setParams] = useSearchParams();
  const rawPair = Number(params.get("pair"));
  const pairId = Number.isInteger(rawPair) && rawPair > 0 ? rawPair : null;
  const accounts = useAccounts();
  const usable = (accounts.data ?? []).filter((a) => a.status !== "DISCONNECTED");
  const step = params.get("step") ?? (usable.length ? "folders" : "account");
  const go = (next: string, extra: Record<string, string> = {}) => setParams({ step: next, ...extra });
  const index = { account: 1, folders: 2, preview: 3, start: 4 }[step] ?? 1;
  // Preview and start need a real pair; a missing or garbled ?pair= goes back to choosing folders.
  if ((step === "preview" || step === "start") && pairId === null) return <Navigate to="/setup?step=folders" replace />;
  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
      <div className="mb-8 flex items-center justify-between">
        <Brand big />
        <Link to="/drive" onClick={skipSetup} className="text-table text-ink-2 hover:text-ink">
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
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const copyUri = (text: string) => {
    const done = navigator.clipboard?.writeText(text);
    if (!done) return setCopy("failed");
    done.then(
      () => setCopy("copied"),
      () => setCopy("failed"),
    );
  };
  const usable = (accounts.data ?? []).filter((a) => a.status !== "DISCONNECTED");
  return (
    <section className="space-y-5">
      <StepHeading>Connect a Google account</StepHeading>
      <p className="max-w-prose text-ink-2">
        DriveSync uses your own Google sign-in client. In the Google Cloud console, add this address to the client's
        allowed redirect addresses:
      </p>
      {setup.data && (
        <div className="flex items-center gap-2">
          <code className="block min-w-0 flex-1 select-all break-all rounded-ctl bg-sunken px-3 py-2 text-table">{setup.data.redirect_uri}</code>
          <Button icon="content_copy" onClick={() => copyUri(setup.data.redirect_uri)}>
            Copy
          </Button>
          <span role="status" className="text-table text-ink-2">
            {copy === "copied" ? "Copied" : copy === "failed" ? "Copy failed" : ""}
          </span>
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
  // Spec §6.10: the local folder follows the Drive folder until it is picked by hand.
  const [localPicked, setLocalPicked] = useState<string | null>(null);
  const local = localPicked ?? remote;
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
      <StepHeading>Choose what to sync</StepHeading>
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
          <FolderPicker source="local" accountId={acc.id} value={local} onChange={setLocalPicked} rootLabel={acc.email} />
          <p className="mt-2 text-meta text-ink-2">
            {localPicked === null ? "Follows the Drive folder until you pick one here. " : ""}A folder that doesn't exist yet is created.
          </p>
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
  const qc = useQueryClient();
  // Set after asking for a comparison, until the server shows a newer preview job than this id.
  const [asked, setAsked] = useState<number | null>(null);
  const jobs = usePairJobs(pairId, asked !== null);
  const lastPreview = latest(jobs.data, (j) => j.kind === "PREVIEW");
  const running = !!lastPreview && ACTIVE_JOB.includes(lastPreview.state);
  const waiting = running || asked !== null;
  const preview = usePreview(pairId, waiting);
  const failedJob = !waiting && lastPreview && FAILED_JOB.includes(lastPreview.state) ? lastPreview : undefined;

  useEffect(() => {
    if (asked !== null && (lastPreview?.id ?? 0) > asked) setAsked(null);
  }, [asked, lastPreview?.id]);
  useEffect(() => {
    // Give up waiting for a job that never shows up (for example the pair was busy with something else).
    if (asked === null) return;
    const t = window.setTimeout(() => setAsked(null), 20_000);
    return () => window.clearTimeout(t);
  }, [asked]);
  const wasWaiting = useRef(false);
  useEffect(() => {
    if (wasWaiting.current && !waiting) qc.invalidateQueries({ queryKey: ["preview", pairId] });
    wasWaiting.current = waiting;
  }, [waiting, qc, pairId]);

  const compare = useMutation({
    mutationFn: () => api(`/api/sync-pairs/${pairId}/preview`, { method: "POST" }),
    onSuccess: () => {
      setAsked(lastPreview?.id ?? 0);
      qc.invalidateQueries({ queryKey: ["history", pairId] });
      qc.invalidateQueries({ queryKey: ["preview", pairId] });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && /busy/i.test(e.detail)) {
        setAsked(lastPreview?.id ?? 0);
        qc.invalidateQueries({ queryKey: ["history", pairId] });
      }
    },
  });
  const alreadyComparing = compare.error instanceof ApiError && compare.error.status === 409 && /busy/i.test(compare.error.detail);

  const [phrase, setPhrase] = useState("");
  const confirm = useMutation({
    mutationFn: () => api(`/api/sync-pairs/${pairId}/confirm-initial`, { method: "POST", body: { phrase } }),
    onSuccess: onNext,
  });

  if (pair.error instanceof ApiError && pair.error.status === 404) return <Navigate to="/setup?step=folders" replace />;
  if (pair.data?.initialized) return <Navigate to={`/setup?step=start&pair=${pairId}`} replace />;
  const shown = !waiting && !failedJob && preview.data ? preview.data : undefined;
  const previewError = preview.error && !(preview.error instanceof ApiError && preview.error.status === 404) ? preview.error : null;
  return (
    <section className="space-y-6">
      <StepHeading>Check before anything moves</StepHeading>
      <p className="max-w-prose text-ink-2">
        DriveSync compares both folders without changing either. The first sync never deletes anything: files on one side are
        copied to the other, and for files that differ the newer one wins (the older local version is saved to backups first).
      </p>
      {!waiting && (
        <Button tone="primary" icon="compare_arrows" onClick={() => compare.mutate()} disabled={compare.isPending}>
          {shown || failedJob ? "Compare again" : "Compare the folders"}
        </Button>
      )}
      {compare.isError && !alreadyComparing && <Notice tone="err">{friendlyError(compare.error)}</Notice>}
      {waiting && (
        <Notice tone="run">
          {alreadyComparing ? "Already comparing. " : ""}Comparing. On a large Drive this can take a few minutes.
        </Notice>
      )}
      {failedJob && <Notice tone="err">{failedJob.error_message ?? "The comparison did not finish. Try again."}</Notice>}
      {previewError && <Notice tone="err">{friendlyError(previewError)}</Notice>}
      {shown && (
        <>
          <PreviewSummary preview={shown} />
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
            <Button tone="primary" type="submit" disabled={phrase !== CONFIRM_PHRASE || confirm.isPending || shown.errors > 0}>
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
  const jobs = usePairJobs(pairId);
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.focus(), []);
  if (pair.error instanceof ApiError && pair.error.status === 404) return <Navigate to="/setup?step=folders" replace />;
  const p = pair.data;
  // The server sets "initialized" only when the first sync finished; a failed job means it did not.
  const done = !!p?.initialized;
  const job = latest(jobs.data, (j) => j.kind !== "PREVIEW");
  const failed = !done && job && FAILED_JOB.includes(job.state) ? (job.error_message ?? p?.last_error ?? "The first sync did not finish.") : null;
  return (
    <section className="space-y-6">
      <div ref={box} role="group" aria-label="First sync status" tabIndex={-1} className="space-y-6 outline-none">
        <h1 className="text-hero font-semibold tracking-tight">{done ? "Synced" : failed ? "The first sync did not finish" : "First sync running"}</h1>
        {!done && !failed && <Notice tone="run">Copying in both directions. You can leave this page; it keeps going.</Notice>}
        {failed && <Notice tone="err">{failed}</Notice>}
        {done && <Notice tone="ok">Both sides are in step. From now on DriveSync keeps them that way.</Notice>}
      </div>
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
