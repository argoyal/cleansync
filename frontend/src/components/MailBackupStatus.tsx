import { Link } from "react-router-dom";

import { friendlyError } from "../api/client";
import { useMailStatus } from "../api/queries";
import type { Account, MailFailure, MailStatus } from "../api/types";
import { useSorting } from "../api/rules";
import { useCheckMail } from "../hooks/useMail";
import { formatBytes, formatDuration, formatRelative, formatWhen, percent } from "../lib/format";
import { mailHref } from "../lib/mail";
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

const SHOWN_FAILURES = 50;

function FailedMails({ failed }: { failed: MailFailure[] }) {
  const n = failed.length;
  const more = n - SHOWN_FAILURES;
  return (
    <Notice tone="warn">
      <p className="mb-2">
        {n} {n === 1 ? "mail" : "mails"} couldn't be backed up after 3 tries. CleanSync keeps trying: first after 20 minutes, then waiting twice as long each time, up to once a day.
      </p>
      <ul className="space-y-1 text-table">
        {failed.slice(0, SHOWN_FAILURES).map((f) => (
          <li key={f.provider_id} className="flex flex-wrap gap-x-3">
            <span className="font-mono">{f.provider_id}</span>
            <span className="text-ink-2">{f.last_error}</span>
          </li>
        ))}
      </ul>
      {more > 0 && <p className="mt-1 text-table text-ink-2">and {more.toLocaleString()} more</p>}
    </Notice>
  );
}

/** One line about AI sorting; nothing when it is off (the switch lives in Rules). */
function SortingStatus({ accountId }: { accountId: number }) {
  const sorting = useSorting(accountId).data;
  if (!sorting?.enabled) return null;
  if (sorting.warning) {
    return (
      <Notice tone="warn">
        Sorting has been failing since {formatWhen(sorting.failing_since)}: {sorting.last_error ?? "the model didn't answer."} New mail waits until the model answers again.{" "}
        <Link to="/settings?tab=models" className="font-semibold text-accent">
          Settings → Models
        </Link>
      </Notice>
    );
  }
  const n = sorting.suggested;
  return (
    <p className="text-table text-ink-2 tabular">
      Sorting: {sorting.sorted_today.toLocaleString()} today · {sorting.waiting.toLocaleString()} waiting ·{" "}
      <Link to={mailHref({ view: "suggested" })} className="font-semibold text-accent">
        {n.toLocaleString()} {n === 1 ? "suggestion" : "suggestions"}
      </Link>
    </p>
  );
}

/** The backup's state at the top of the Mail page and on Sync activity (spec §4.3–4.5). */
export function MailBackupStatus({ account, title }: { account: Pick<Account, "id" | "email">; title?: string }) {
  const query = useMailStatus(account.id);
  const status = query.data;
  const check = useCheckMail();
  if (!status) {
    if (query.isError) {
      return (
        <Notice tone="err">
          <p>Could not load the mail backup: {friendlyError(query.error)}</p>
          <Button className="mt-2" icon="refresh" onClick={() => query.refetch()}>
            Retry
          </Button>
        </Notice>
      );
    }
    return <p className="text-ink-2">Loading the mail backup…</p>;
  }
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
          <p className="text-table text-ink-2 tabular" aria-live="polite">
            {progressLine(status)}
          </p>
        </>
      )}
      {(ended === "PAUSED" || ended === "FAILED") && status.last_error && <Notice tone={ended === "PAUSED" ? "warn" : "err"}>{status.last_error}</Notice>}
      <SortingStatus accountId={account.id} />
      {status.failed.length > 0 && <FailedMails failed={status.failed} />}
    </section>
  );
}
