import { type ReactNode, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import { useMailCounts, useMailThreads } from "../../api/queries";
import type { Account } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { viewTitle } from "../../lib/mail";
import { MailList } from "./MailList";
import { isNarrow, MailThread } from "./MailThread";

/** Spec §6.1: the list beside the open mail; on phones one column at a time. */
export function MailReader({ account, actions }: { account: Account; actions?: ReactNode }) {
  const [params] = useSearchParams();
  const view = params.get("view") ?? "all";
  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const thread = params.get("thread") ?? "";
  const labelName = useMailCounts(account.id, label !== "").data?.labels.find((l) => l.id === label)?.name;
  const first = useMailThreads(account.id, { view, label, q }).data?.pages[0];
  const list = useRef<HTMLElement>(null);
  const backFrom = useRef<string | null>(null); // the thread "Back to the list" left
  useEffect(() => {
    const from = backFrom.current;
    if (thread || from === null) return;
    backFrom.current = null;
    if (isNarrow()) list.current?.querySelector<HTMLElement>(`[data-thread="${CSS.escape(from)}"]`)?.focus();
  }, [thread]);
  const title = q ? "Search results" : viewTitle(view, label ? (labelName ?? label) : null);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-4 sm:px-6">
        <h1 className="text-title font-semibold">{title}</h1>
        {q && <span className="text-table text-ink-2">for {q}</span>}
        {first?.total != null && (
          <span className="text-table text-ink-2 tabular">
            {first.total.toLocaleString()} {first.total === 1 ? "mail" : "mails"} · {formatBytes(first.total_bytes ?? 0)}
          </span>
        )}
        <div className="ml-auto flex gap-2">{actions}</div>
      </div>
      <div className="flex min-h-0 flex-1 border-t border-line">
        <section ref={list} aria-label="Mail list" className={`min-w-0 overflow-y-auto xl:w-[440px] xl:shrink-0 xl:border-r xl:border-line ${thread ? "hidden xl:block" : "flex-1 xl:flex-none"}`}>
          <MailList accountId={account.id} view={view} label={label} q={q} thread={thread} />
        </section>
        <section aria-label="Open mail" className={`min-w-0 flex-1 overflow-y-auto ${thread ? "" : "hidden xl:block"}`}>
          {thread ? <MailThread account={account} threadId={thread} onBack={() => (backFrom.current = thread)} /> : <p className="p-6 text-ink-2">Pick a mail to read it.</p>}
        </section>
      </div>
    </div>
  );
}
