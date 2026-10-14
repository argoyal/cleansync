import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { useAccounts, useAppSettings, useGoogleUsage, useSystem } from "../api/queries";
import type { Account, AppSettings } from "../api/types";
import { Avatar } from "../components/AccountSwitcher";
import { ACCOUNT_DOT } from "../components/StatusBadge";
import { ModelsSettings } from "../components/settings/ModelsSettings";
import { ThemeSwitch } from "../components/ThemeSwitch";
import { GmailFilters } from "../components/settings/GmailFilters";
import { PermanentDelete } from "../components/settings/PermanentDelete";
import { useToast } from "../components/Toasts";
import { Bar, Button, Dialog, Field, inputClass, Notice } from "../components/ui";
import { useConnectGoogle } from "../hooks/useConnect";
import { formatBytes, formatRelative, percent } from "../lib/format";
import { PasswordForm } from "./ChangePasswordPage";

/** The Settings tabs; the section panel lists the same ones. */
export const TABS = { general: "General", accounts: "Accounts", sync: "Sync defaults", mail: "Mail", models: "Models", google: "Google API", security: "Security", system: "System" } as const;
type Tab = keyof typeof TABS;

export function SettingsPage() {
  const [params] = useSearchParams();
  const asked = params.get("tab") ?? "";
  const tab: Tab = Object.hasOwn(TABS, asked) ? (asked as Tab) : "general";
  return (
    <div className="p-4 sm:p-6">
      <h1 className="mb-4 text-title font-semibold">Settings</h1>
      <nav aria-label="Settings sections" className="mb-6 flex gap-1 md:hidden overflow-x-auto border-b border-line">
        {(Object.keys(TABS) as Tab[]).map((t) => (
          <Link
            key={t}
            to={`?tab=${t}`}
            aria-current={tab === t ? "page" : undefined}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-table font-medium ${tab === t ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}
          >
            {TABS[t]}
          </Link>
        ))}
      </nav>
      <section aria-label={TABS[tab]} className="max-w-2xl">
        {tab === "general" && <General />}
        {tab === "accounts" && <Accounts />}
        {tab === "sync" && <SyncDefaults />}
        {tab === "mail" && <MailSettings />}
        {tab === "models" && <ModelsSettings />}
        {tab === "google" && <GoogleApiSettings />}
        {tab === "security" && <Security />}
        {tab === "system" && <System />}
      </section>
    </div>
  );
}

function useSaveSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (values: Partial<AppSettings>) => api<AppSettings>("/api/settings", { method: "PUT", body: values }),
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      toast("Saved.");
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

function NumberSetting({ label, hint, value, min, max, onSave }: { label: string; hint?: string; value: number; min: number; max: number; onSave: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const trimmed = text.trim();
  const whole = /^\d+$/.test(trimmed);
  const v = whole ? Number(trimmed) : NaN;
  const valid = whole && v >= min && v <= max;
  const problem = trimmed !== "" && !valid ? `Use a whole number from ${min} to ${max}.` : null;
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        if (valid && v !== value) onSave(v);
      }}
    >
      <Field label={label} hint={problem ?? hint}>
        <input className={`${inputClass} w-36`} type="text" inputMode="numeric" aria-invalid={problem !== null} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Button type="submit" disabled={!valid || v === value}>
        Save
      </Button>
    </form>
  );
}

function General() {
  const system = useSystem().data;
  const settings = useAppSettings().data;
  const save = useSaveSettings();
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-[auto,1fr] gap-x-6 gap-y-2 text-table">
        <dt className="text-ink-2">Name</dt>
        <dd>{system?.app_name}</dd>
        <dt className="text-ink-2">Time zone</dt>
        <dd>{Intl.DateTimeFormat().resolvedOptions().timeZone} (this browser; the server logs use its TZ setting)</dd>
      </dl>
      <p className="text-table text-ink-2">The name and time zone come from the APP_NAME and TZ settings of the server.</p>
      <div>
        <p className="mb-2 text-table font-medium text-ink-2">Theme</p>
        <ThemeSwitch />
        <p className="mt-1 text-meta text-ink-2">Remembered in this browser. System follows your computer's setting.</p>
      </div>
      {settings && (
        <NumberSetting
          label="Keep sync logs for (days)"
          value={settings.log_retention_days}
          min={1}
          max={3650}
          onSave={(v) => save.mutate({ log_retention_days: v })}
        />
      )}
    </div>
  );
}

function AccountRow({ account }: { account: Account }) {
  const qc = useQueryClient();
  const toast = useToast();
  const connect = useConnectGoogle();
  const [confirm, setConfirm] = useState(false);
  const disconnect = useMutation({
    mutationFn: () => api(`/api/accounts/${account.id}/disconnect`, { method: "POST" }),
    onSuccess: () => {
      ["accounts", "pairs", "status"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast(`Disconnected ${account.email}. Its files stay on this server.`);
      setConfirm(false);
    },
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  const quota = useMutation({
    mutationFn: () => api(`/api/accounts/${account.id}/quota`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
    onError: (e) => toast(friendlyError(e), { tone: "error" }),
  });
  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <Avatar email={account.email} accountId={account.id} hasPhoto={account.has_photo} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold">{account.email}</p>
        <p className="flex items-center gap-1.5 text-meta text-ink-2">
          <span className={`h-2 w-2 rounded-full ${ACCOUNT_DOT[account.status]}`} />
          {account.status === "CONNECTED" ? "Connected" : account.status === "NEEDS_REAUTH" ? "Needs reconnecting" : "Disconnected"}
          {account.quota_total ? `, ${formatBytes(account.quota_used)} of ${formatBytes(account.quota_total)} used` : ""}
          {account.last_checked_at ? `, checked ${formatRelative(account.last_checked_at)}` : ""}
        </p>
      </div>
      <div className="flex gap-2">
        {account.status === "CONNECTED" && (
          <Button tone="quiet" icon="refresh" disabled={quota.isPending} onClick={() => quota.mutate()}>
            Check storage
          </Button>
        )}
        <Button onClick={() => connect.mutate(account.id, { onError: (e) => toast(friendlyError(e), { tone: "error" }) })}>
          Reconnect
        </Button>
        {account.status !== "DISCONNECTED" && (
          <Button tone="quiet" className="text-err" onClick={() => setConfirm(true)}>
            Disconnect
          </Button>
        )}
      </div>
      {account.status === "CONNECTED" && account.services.mail !== "not_set_up" && (
        <>
          <PermanentDelete account={account} />
          <GmailFilters account={account} />
        </>
      )}
      {confirm && (
        <Dialog
          title={`Disconnect ${account.email}?`}
          onClose={() => setConfirm(false)}
          footer={
            <>
              <Button onClick={() => setConfirm(false)} data-autofocus>
                Cancel
              </Button>
              <Button tone="danger" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
                Disconnect
              </Button>
            </>
          }
        >
          <p>CleanSync forgets this Google sign-in and stops syncing it. No files are deleted, here or on Google Drive. The files already on this server are kept.</p>
        </Dialog>
      )}
    </li>
  );
}

function Accounts() {
  const { data = [] } = useAccounts();
  const connect = useConnectGoogle();
  return (
    <div className="space-y-4">
      <ul className="divide-y divide-line">
        {data.map((a) => (
          <AccountRow key={a.id} account={a} />
        ))}
      </ul>
      {connect.isError && <Notice tone="err">{friendlyError(connect.error)}</Notice>}
      <Button tone="primary" icon="person_add" disabled={connect.isPending} onClick={() => connect.mutate(undefined)}>
        Add Google account
      </Button>
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

function SyncDefaults() {
  const settings = useAppSettings().data;
  const save = useSaveSettings();
  if (!settings) return null;
  const pick = (v: string) => (v === "" ? null : Number(v));
  return (
    <div className="space-y-5">
      <p className="text-ink-2">These apply to syncs you set up from now on.</p>
      <Field label="How often to sync">
        <select className={inputClass} value={settings.default_interval_s ?? ""} onChange={(e) => save.mutate({ default_interval_s: pick(e.target.value) })}>
          {INTERVALS.map(([v, l]) => (
            <option key={l} value={v ?? ""}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <NumberSetting
        label="Deletion limit per sync"
        hint="A sync that would delete more files stops and asks you."
        value={settings.default_max_deletions}
        min={0}
        max={1_000_000}
        onSave={(v) => save.mutate({ default_max_deletions: v })}
      />
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={settings.default_backups_enabled} onChange={(e) => save.mutate({ default_backups_enabled: e.target.checked })} />
        Keep a copy of local files before a sync changes or deletes them
      </label>
      <Field label="Keep those copies for">
        <select
          className={inputClass}
          value={settings.default_backup_retention_days ?? ""}
          onChange={(e) => save.mutate({ default_backup_retention_days: pick(e.target.value) })}
        >
          {RETENTION.map(([v, l]) => (
            <option key={l} value={v ?? ""}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <NumberSetting
        label="Wait after a local change (seconds)"
        hint="Several quick changes then count as one."
        value={settings.watcher_debounce_s}
        min={1}
        max={300}
        onSave={(v) => save.mutate({ watcher_debounce_s: v })}
      />
      <NumberSetting
        label="Syncs running at the same time"
        hint="Takes effect after a restart."
        value={settings.max_parallel_pairs}
        min={1}
        max={8}
        onSave={(v) => save.mutate({ max_parallel_pairs: v })}
      />
    </div>
  );
}

const MAIL_INTERVALS: [number, string][] = [
  [60, "Every minute"],
  [300, "Every 5 minutes"],
  [900, "Every 15 minutes"],
  [1800, "Every 30 minutes"],
  [3600, "Every hour"],
];

/** Spec §8: the Mail settings. */
function MailSettings() {
  const settings = useAppSettings().data;
  const save = useSaveSettings();
  if (!settings) return null;
  return (
    <div className="space-y-6">
      <Field label="Check for new mail" hint="How often CleanSync asks Gmail what changed.">
        <select className={`${inputClass} w-56`} value={settings.mail_check_interval_s} onChange={(e) => save.mutate({ mail_check_interval_s: Number(e.target.value) })}>
          {MAIL_INTERVALS.map(([s, label]) => (
            <option key={s} value={s}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <NumberSetting
        label="Never offload mail newer than (days)"
        hint="Recent mail always stays in Gmail. The default is 30 days."
        value={settings.mail_min_age_days}
        min={1}
        max={3650}
        onSave={(v) => save.mutate({ mail_min_age_days: v })}
      />
      <NumberSetting
        label="Pause the backup when free space drops below (GB)"
        value={settings.mail_disk_floor_gb}
        min={1}
        max={10000}
        onSave={(v) => save.mutate({ mail_disk_floor_gb: v })}
      />
    </div>
  );
}

/** Spec §7: units used today and the budget. */
function GoogleApiSettings() {
  const usage = useGoogleUsage().data;
  const settings = useAppSettings().data;
  const save = useSaveSettings();
  return (
    <div className="space-y-6">
      {usage && (
        <div className="space-y-1.5">
          <Bar label="Google API units used today" value={percent(usage.used, usage.budget)} tone={usage.reached ? "err" : "accent"} />
          <p className="text-table tabular">
            {usage.used.toLocaleString()} of {usage.budget.toLocaleString()} units used today (UTC).
            {usage.reached ? " Mail jobs are paused until midnight UTC." : ""}
          </p>
          <p className="text-table tabular text-ink-2">
            Drive syncs, estimated: about {usage.drive_estimate.toLocaleString()} units today. They count toward the budget but are never held back.
          </p>
        </div>
      )}
      <p className="text-table text-ink-2">
        Google plans to charge above 80,000,000 units a day per Google Cloud project. CleanSync keeps to its own daily budget, far below that, so it never costs
        money.
      </p>
      {settings && (
        <NumberSetting
          label="Daily budget (units)"
          value={settings.google_daily_budget_units}
          min={1}
          max={40_000_000}
          onSave={(v) => save.mutate({ google_daily_budget_units: v })}
        />
      )}
    </div>
  );
}

function Security() {
  const settings = useAppSettings().data;
  const save = useSaveSettings();
  const toast = useToast();
  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-3 text-section font-semibold">Change password</h2>
        <PasswordForm onDone={() => toast("Password changed. Other sessions were signed out.")} />
      </section>
      {settings && (
        <NumberSetting
          label="Sign out after this many hours without use"
          value={settings.session_idle_hours}
          min={1}
          max={720}
          onSave={(v) => save.mutate({ session_idle_hours: v })}
        />
      )}
    </div>
  );
}

function System() {
  const { data: s, isError, error, refetch } = useSystem();
  if (isError)
    return (
      <Notice tone="err">
        {friendlyError(error)}{" "}
        <button type="button" className="font-semibold underline" onClick={() => refetch()}>
          Retry
        </button>
      </Notice>
    );
  if (!s) return <p className="text-ink-2">Loading system details…</p>;
  return (
    <div className="space-y-4">
      {!s.db_ok && <Notice tone="err">The database check failed, so syncing is off. See docs/backup-recovery.md.</Notice>}
      {s.insecure_http && <Notice tone="warn">CleanSync is served over plain http ({s.public_url}). Use https outside your home network.</Notice>}
      <dl className="grid grid-cols-[auto,1fr] gap-x-6 gap-y-2 text-table">
        <dt className="text-ink-2">{s.app_name}</dt>
        <dd>{s.app_version}</dd>
        <dt className="text-ink-2">rclone</dt>
        <dd>{s.rclone_version}</dd>
        <dt className="text-ink-2">Python</dt>
        <dd>{s.python_version}</dd>
        <dt className="text-ink-2">Files</dt>
        <dd className="break-all">{s.storage_root}</dd>
        <dt className="text-ink-2">App data</dt>
        <dd className="break-all">{s.data_dir}</dd>
        <dt className="text-ink-2">Change watcher</dt>
        <dd>
          {s.watcher === "active"
            ? "Active: local changes are noticed right away"
            : s.watcher === "limited"
              ? "Limited: changes are found by the schedule"
              : s.watcher}
          {s.watcher === "limited" && (
            <span className="mt-1 block text-meta text-ink-2">
              The server could not watch folders for changes, so local edits wait for the next scheduled sync.
            </span>
          )}
        </dd>
        <dt className="text-ink-2">Address</dt>
        <dd>{s.public_url}</dd>
      </dl>
    </div>
  );
}
