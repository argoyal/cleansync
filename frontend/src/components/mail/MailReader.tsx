import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useMailCounts, useMailTotals } from "../../api/queries";
import type { Account } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { formatBytes } from "../../lib/format";
import { viewTitle } from "../../lib/mail";
import { Button } from "../ui";
import { MailHeaderActions } from "./MailActions";
import { MailList } from "./MailList";
import { isNarrow, MailThread } from "./MailThread";
import { OffloadMailDialog } from "./OffloadMailDialog";

/** Spec §6.1: the list beside the open mail; on phones one column at a time. Spec §5.1:
 * "Offload mail…", and offload or restore for picked threads. */
export function MailReader({ account }: { account: Account }) {
  const [params] = useSearchParams();
  const view = params.get("view") ?? "all";
  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const thread = params.get("thread") ?? "";
  const labelName = useMailCounts(account.id, label !== "").data?.labels.find((l) => l.id === label)?.name;
  const totals = useMailTotals(account.id, { view, label, q }).data;
  const list = useRef<HTMLElement>(null);
  const backFrom = useRef<string | null>(null); // the thread "Back to the list" left
  useEffect(() => {
    const from = backFrom.current;
    if (thread || from === null) return;
    backFrom.current = null;
    if (isNarrow()) list.current?.querySelector<HTMLElement>(`[data-thread="${CSS.escape(from)}"]`)?.focus();
  }, [thread]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [offloadPicked, setOffloadPicked] = useState(false);
  const restore = useRestoreMail();
  // Picks belong to one list: a new view, label or search starts with none.
  useEffect(() => setPicked(new Set()), [view, label, q]);
  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };
  const title = q ? "Search results" : viewTitle(view, label ? (labelName ?? label) : null);
  const scope = label ? (labelName ?? label) : view !== "all" ? viewTitle(view) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-4 sm:px-6">
        <h1 className="text-title font-semibold">{title}</h1>
        {q && <span className="text-table text-ink-2">for {q}</span>}
        {totals && (
          <span className="text-table text-ink-2 tabular">
            {totals.total.toLocaleString()} {totals.total === 1 ? "mail" : "mails"} · {formatBytes(totals.total_bytes)}
          </span>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <MailHeaderActions account={account} q={q} view={view} label={label} scope={scope} />
        </div>
      </div>
      {picked.size > 0 && (
        <section aria-label="Picked mail" className="flex flex-wrap items-center gap-3 border-t border-line bg-sunken px-4 py-2 sm:px-6">
          <span className="text-table">
            {picked.size.toLocaleString()} {picked.size === 1 ? "thread" : "threads"} picked
          </span>
          <Button onClick={() => setOffloadPicked(true)}>Offload</Button>
          <Button
            disabled={restore.isPending}
            onClick={() => restore.mutate({ accountId: account.id, start: { thread_ids: [...picked] } }, { onSuccess: () => setPicked(new Set()) })}
          >
            Restore to Gmail
          </Button>
          <Button tone="quiet" onClick={() => setPicked(new Set())}>
            Clear
          </Button>
        </section>
      )}
      {offloadPicked && <OffloadMailDialog account={account} start={{ thread_ids: [...picked] }} onClose={() => setOffloadPicked(false)} />}
      <div className="flex min-h-0 flex-1 border-t border-line">
        <section ref={list} aria-label="Mail list" className={`min-w-0 overflow-y-auto xl:w-[440px] xl:shrink-0 xl:border-r xl:border-line ${thread ? "hidden xl:block" : "flex-1 xl:flex-none"}`}>
          <MailList accountId={account.id} view={view} label={label} q={q} thread={thread} selected={picked} onToggle={toggle} />
        </section>
        <section aria-label="Open mail" className={`min-w-0 flex-1 overflow-y-auto ${thread ? "" : "hidden xl:block"}`}>
          {thread ? <MailThread account={account} threadId={thread} onBack={() => (backFrom.current = thread)} /> : <p className="p-6 text-ink-2">Pick a mail to read it.</p>}
        </section>
      </div>
    </div>
  );
}
