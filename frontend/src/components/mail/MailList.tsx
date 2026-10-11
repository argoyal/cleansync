import { Link } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { useMailThreads } from "../../api/queries";
import type { ThreadRow } from "../../api/types";
import { formatWhen } from "../../lib/format";
import { mailHref } from "../../lib/mail";
import { Icon } from "../Icon";
import { Button, Notice } from "../ui";

function sender(from: string): string {
  const m = from.match(/^\s*"?([^"<]+?)"?\s*</);
  return (m ? m[1] : from).trim() || "(no sender)";
}

function Mark({ children }: { children: string }) {
  return <span className="ml-1.5 inline-block rounded-full bg-sec-mail-soft px-2 text-meta text-sec-mail-soft-ink">{children}</span>;
}

function Row({ t, href, open }: { t: ThreadRow; href: string; open: boolean }) {
  const name = sender(t.from_addr);
  const parts = [name, t.subject || "(no subject)"];
  if (t.count > 1) parts.push(`${t.count} mails`);
  if (t.unread) parts.push("unread");
  if (t.has_attachments) parts.push("has attachments");
  if (t.offloaded) parts.push("offloaded");
  if (t.deleted) parts.push("deleted in Gmail");
  parts.push(formatWhen(t.last_date));
  const label = parts.join(", ");
  return (
    <li>
      <Link
        to={href}
        aria-label={label}
        aria-current={open ? "true" : undefined}
        className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 border-b border-line px-4 py-3 hover:bg-sunken sm:grid-cols-[180px_minmax(0,1fr)_auto] ${open ? "bg-sec-mail-soft/60" : ""} ${t.unread ? "font-semibold" : ""} ${t.offloaded || t.deleted ? "text-ink-2" : ""}`}
      >
        <span className="truncate">
          {name}
          {t.count > 1 && <span className="ml-1 text-meta text-ink-2">{t.count}</span>}
        </span>
        <span className="col-span-2 row-start-2 min-w-0 truncate sm:col-span-1 sm:row-start-1">
          {t.subject || "(no subject)"}
          <span className="font-normal text-ink-2"> · {t.snippet}</span>
          {t.offloaded && <Mark>Offloaded</Mark>}
          {t.deleted && <Mark>Deleted in Gmail</Mark>}
        </span>
        <span className="flex items-center gap-1 justify-self-end text-meta font-normal text-ink-2 tabular">
          {t.has_attachments && <Icon name="attach_file" label="Has attachments" className="text-[16px]" />}
          {formatWhen(t.last_date)}
        </span>
      </Link>
    </li>
  );
}

export function MailList({ accountId, view, label, q, thread }: { accountId: number; view: string; label: string; q: string; thread: string }) {
  const list = useMailThreads(accountId, { view, label, q });
  const rows = list.data?.pages.flatMap((p) => p.threads) ?? [];
  if (list.isError && rows.length === 0) return <div className="p-4"><Notice tone="err">{friendlyError(list.error)}</Notice></div>;
  if (list.isPending) return <p className="p-6 text-ink-2">Loading…</p>;
  if (list.isSuccess && rows.length === 0) return <p className="p-6 text-ink-2">No mail here.</p>;
  return (
    <div>
      <ul aria-label="Mail">
        {rows.map((t) => (
          <Row key={t.thread_id} t={t} open={t.thread_id === thread} href={mailHref({ view, label, q, thread: t.thread_id })} />
        ))}
      </ul>
      {list.isError && (
        <div className="p-3">
          <Notice tone="err">{friendlyError(list.error)}</Notice>
        </div>
      )}
      {list.hasNextPage && (
        <div className="p-3 text-center">
          <Button disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            Show more
          </Button>
        </div>
      )}
    </div>
  );
}
