import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { useMailThreads } from "../../api/queries";
import type { ThreadRow } from "../../api/types";
import { formatWhen } from "../../lib/format";
import { mailHref } from "../../lib/mail";
import { Icon } from "../Icon";
import { Button, Notice } from "../ui";
import { RuleChip } from "./RuleChip";

function sender(from: string): string {
  const m = from.match(/^\s*"?([^"<]+?)"?\s*</);
  return (m ? m[1] : from).trim() || "(no sender)";
}

function Mark({ children }: { children: string }) {
  return <span className="ml-1.5 inline-block rounded-full bg-sec-mail-soft px-2 text-meta text-sec-mail-soft-ink">{children}</span>;
}

function Row({
  accountId,
  t,
  href,
  open,
  picked,
  onToggle,
}: {
  accountId: number;
  t: ThreadRow;
  href: string;
  open: boolean;
  picked: boolean;
  onToggle?: (threadId: string) => void;
}) {
  const name = sender(t.from_addr);
  const parts = [name, t.subject || "(no subject)"];
  if (t.count > 1) parts.push(`${t.count} mails`);
  if (t.unread) parts.push("unread");
  if (t.has_attachments) parts.push("has attachments");
  if (t.offloaded) parts.push("offloaded");
  if (t.deleted) parts.push("deleted in Gmail");
  const chip = t.decision;
  if (chip) parts.push(`${chip.status}: ${chip.rule_name}`);
  parts.push(formatWhen(t.last_date));
  const label = parts.join(", ");
  return (
    <li className={`flex items-stretch border-b border-line ${open ? "bg-sec-mail-soft/60" : ""}`}>
      {onToggle && (
        // Beside the link, not in it: a checkbox inside a link is not valid HTML.
        <label className="flex shrink-0 items-center pl-4">
          <input type="checkbox" className="h-4 w-4 accent-accent" aria-label={`Pick ${t.subject || "(no subject)"}`} checked={picked} onChange={() => onToggle(t.thread_id)} />
        </label>
      )}
      <Link
        to={href}
        data-thread={t.thread_id}
        aria-label={label}
        aria-current={open ? "true" : undefined}
        className={`grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 px-4 py-3 hover:bg-sunken sm:grid-cols-[180px_minmax(0,1fr)_auto] ${t.unread ? "font-semibold" : ""} ${t.offloaded || t.deleted ? "text-ink-2" : ""}`}
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
      {chip && (
        // Beside the link too: the chip is a button, and a button inside a link is not valid HTML.
        <div className="flex shrink-0 items-center pr-3">
          <RuleChip accountId={accountId} chip={chip} />
        </div>
      )}
    </li>
  );
}

/** The checkbox at the top of the list (mail panel spec §6): empty, mixed (some picked by
 * hand) or ticked (every mail of the view and search). */
export type PickState = "none" | "some" | "all";

export function PickAllBox({ state, onChange }: { state: PickState; onChange: () => void }) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = state === "some";
  }, [state]);
  return (
    <label className="flex items-center gap-3 border-b border-line px-4 py-2 text-table text-ink-2">
      <input ref={box} type="checkbox" className="h-4 w-4 accent-accent" aria-label="Pick all" checked={state === "all"} onChange={onChange} />
      <span aria-hidden="true">Pick all</span>
    </label>
  );
}

/** `isPicked` and `onToggle` turn on picking threads; `onPickAll` adds the Pick all checkbox
 * (not in Suggested, where Approve all covers the list). */
export function MailList({
  accountId,
  view,
  label,
  q,
  thread,
  isPicked,
  onToggle,
  pickState = "none",
  onPickAll,
}: {
  accountId: number;
  view: string;
  label: string;
  q: string;
  thread: string;
  isPicked?: (threadId: string) => boolean;
  onToggle?: (threadId: string) => void;
  pickState?: PickState;
  onPickAll?: () => void;
}) {
  const list = useMailThreads(accountId, { view, label, q });
  const rows = list.data?.pages.flatMap((p) => p.threads) ?? [];
  if (list.isError && rows.length === 0) return <div className="p-4"><Notice tone="err">{friendlyError(list.error)}</Notice></div>;
  if (list.isPending) return <p className="p-6 text-ink-2">Loading…</p>;
  if (list.isSuccess && rows.length === 0) return <p className="p-6 text-ink-2">No mail here.</p>;
  return (
    <div>
      {onPickAll && <PickAllBox state={pickState} onChange={onPickAll} />}
      <ul aria-label="Mail">
        {rows.map((t) => (
          <Row
            key={t.thread_id}
            accountId={accountId}
            t={t}
            open={t.thread_id === thread}
            href={mailHref({ view, label, q, thread: t.thread_id })}
            picked={isPicked?.(t.thread_id) ?? false}
            onToggle={onToggle}
          />
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
