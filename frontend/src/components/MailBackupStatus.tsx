import { useMailStatus } from "../api/queries";
import type { Account, MailFailure, MailStatus } from "../api/types";
import { useCheckMail } from "../hooks/useMail";
import { formatBytes, formatDuration, formatRelative, percent } from "../lib/format";
import { Bar, Button, Notice } from "./ui";

export function summaryLine(s: MailStatus): string {
  const checked = s.last_run_at ? `checked ${formatRelative(s.last_run_at)}` : "not checked yet";
  return `${s.stored.toLocaleString()} mails · ${formatBytes(s.stored_bytes)} · ${checked}`;
}

function progressLine(s: MailStatus): string {
  const counted = `${s.stored.toLocaleString()} of about ${(s.total ?? s.stored).toLocaleString()} mails`;
  if (s.running) return s.time_left_s !== null ? `${counted} · ${formatDuration(s.time_left_s)} left` : counted;
  return `${counted} so far. It goes on at the next check.`;
}

function FailedMails({ failed }: { failed: MailFailure[] }) {
  const n = failed.length;
  return (
    <Notice tone="warn">
      <p className="mb-2">
        {n} {n === 1 ? "mail" : "mails"} couldn't be backed up after 3 tries. CleanSync tries again once a day.
      </p>
      <ul className="space-y-1 text-table">
        {failed.map((f) => (
          <li key={f.provider_id} className="flex flex-wrap gap-x-3">
            <span className="font-mono">{f.provider_id}</span>
            <span className="text-ink-2">{f.last_error}</span>
          </li>
        ))}
      </ul>
    </Notice>
  );
}

/** The backup's state at the top of the Mail page and on Sync activity (spec §4.3–4.5). */
export function MailBackupStatus({ account, title }: { account: Pick<Account, "id" | "email">; title?: string }) {
  const status = useMailStatus(account.id).data;
  const check = useCheckMail();
  if (!status) return null;
  const first = status.phase === "initial";
  const ended = status.job?.state;
  const busy = status.running || status.waiting;
  return (
    <section aria-label={title ?? "Mail backup"} className="space-y-3 rounded-card border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-section font-semibold">{title ?? (first ? "First backup" : "Mail backup")}</h2>
        <span className="text-table text-ink-2 tabular">{summaryLine(status)}</span>
        <Button className="ml-auto" disabled={busy || check.isPending} onClick={() => check.mutate(account.id)}>
          {busy ? "Checking…" : "Check now"}
        </Button>
      </div>
      {first && (
        <>
          <Bar label="Mails backed up" value={percent(status.stored, status.total)} tone="run" />
          <p className="text-table text-ink-2 tabular">{progressLine(status)}</p>
        </>
      )}
      {(ended === "PAUSED" || ended === "FAILED") && status.last_error && <Notice tone={ended === "PAUSED" ? "warn" : "err"}>{status.last_error}</Notice>}
      {status.failed.length > 0 && <FailedMails failed={status.failed} />}
    </section>
  );
}
