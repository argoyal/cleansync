import { friendlyError } from "../api/client";
import { useMailSetupInfo } from "../api/queries";
import type { Account, MailSetupInfo } from "../api/types";
import { useStartMail } from "../hooks/useMail";
import { formatBytes } from "../lib/format";
import { Button, Notice } from "./ui";

export function startText(info: MailSetupInfo): string {
  const size = info.size_bytes !== null ? `, about ${formatBytes(info.size_bytes)}` : "";
  return `About ${info.total.toLocaleString()} mails${size}. The first backup takes a few hours. Nothing in Gmail changes.`;
}

/** Spec §4.2 step 2: the count, then Start. */
export function MailStartCard({ account }: { account: Account }) {
  const info = useMailSetupInfo(account.id, true);
  const start = useStartMail();
  return (
    <section className="max-w-xl rounded-card border border-line bg-surface p-6">
      <h1 className="mb-2 text-title font-semibold">Start mail backup</h1>
      {info.isPending && <p className="mb-4 text-ink-2">Counting the mail in {account.email}…</p>}
      {info.isError && (
        <div className="mb-4">
          <Notice tone="err">
            <p>{friendlyError(info.error)}</p>
            <Button className="mt-2" icon="refresh" disabled={info.isFetching} onClick={() => info.refetch()}>
              Retry
            </Button>
          </Notice>
        </div>
      )}
      {info.data && (
        <div className="mb-4">
          <p>{startText(info.data)}</p>
          {info.data.size_bytes !== null && (
            <p className="mt-1 text-meta text-ink-2">The size is what Gmail and Google Photos use together; Google doesn't report Gmail on its own.</p>
          )}
        </div>
      )}
      <Button tone="main" disabled={!info.data || start.isPending} onClick={() => start.mutate(account.id)}>
        Start
      </Button>
    </section>
  );
}
