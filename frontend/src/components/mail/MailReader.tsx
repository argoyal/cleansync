import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { mailThreadQuery, useMailCounts, useMailThreads } from "../../api/queries";
import type { Account } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { useApprove } from "../../hooks/useRules";
import { formatBytes } from "../../lib/format";
import { viewTitle } from "../../lib/mail";
import { useToast } from "../Toasts";
import { Button } from "../ui";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { DeleteMailDialog, MailHeaderActions } from "./MailActions";
import { MailList } from "./MailList";
import { isNarrow, MailThread } from "./MailThread";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";

/** "Make a rule…" drafts from at most this many mails: the newest of each picked thread. */
const RULE_FROM_AT_MOST = 50;
type PickedAction = "offload" | "delete" | "rule" | null;

/** Spec §6.1: the list beside the open mail; on phones one column at a time. Spec §5.1:
 * "Offload mail…", and offload or restore for picked threads. */
export function MailReader({ account }: { account: Account }) {
  const [params] = useSearchParams();
  const view = params.get("view") ?? "all";
  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const thread = params.get("thread") ?? "";
  const labelName = useMailCounts(account.id, label !== "").data?.labels.find((l) => l.id === label)?.name;
  const pages = useMailThreads(account.id, { view, label, q }).data?.pages;
  const first = pages?.[0];
  const rows = pages?.flatMap((p) => p.threads) ?? [];
  const archive = view === "archive";
  const list = useRef<HTMLElement>(null);
  const backFrom = useRef<string | null>(null); // the thread "Back to the list" left
  useEffect(() => {
    const from = backFrom.current;
    if (thread || from === null) return;
    backFrom.current = null;
    if (isNarrow()) list.current?.querySelector<HTMLElement>(`[data-thread="${CSS.escape(from)}"]`)?.focus();
  }, [thread]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pickedAction, setPickedAction] = useState<PickedAction>(null);
  const [forever, setForever] = useState<number[] | null>(null); // the archived mails of the picked threads
  const restore = useRestoreMail();
  const approve = useApprove();
  const qc = useQueryClient();
  const toast = useToast();
  const clearPicks = () => setPicked(new Set());
  const suggestedIds = rows.flatMap((t) => (t.decision?.status === "suggested" ? [t.decision.id] : []));
  const ruleFrom = rows.filter((t) => picked.has(t.thread_id)).map((t) => t.message_id).slice(0, RULE_FROM_AT_MOST);
  // Delete forever takes mails, not threads: read which archived mails the picked threads hold.
  const pickForever = () =>
    Promise.all([...picked].map((id) => qc.fetchQuery(mailThreadQuery(account.id, id, true))))
      .then((threads) => setForever(threads.flat().map((m) => m.id)))
      .catch((e: unknown) => toast(friendlyError(e), { tone: "error" }));
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
        {first?.total != null && (
          <span className="text-table text-ink-2 tabular">
            {first.total.toLocaleString()} {first.total === 1 ? "mail" : "mails"} · {formatBytes(first.total_bytes ?? 0)}
          </span>
        )}
        {view === "suggested" && (
          <Button tone="primary" disabled={suggestedIds.length === 0 || approve.isPending} onClick={() => approve.mutate({ accountId: account.id, ids: suggestedIds })}>
            Approve all
          </Button>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <MailHeaderActions account={account} q={q} view={view} label={label} scope={scope} total={first?.total} />
        </div>
      </div>
      {picked.size > 0 && (
        <section aria-label="Picked mail" className="flex flex-wrap items-center gap-3 border-t border-line bg-sunken px-4 py-2 sm:px-6">
          <span className="text-table">
            {picked.size.toLocaleString()} {picked.size === 1 ? "thread" : "threads"} picked
          </span>
          {!archive && <Button onClick={() => setPickedAction("offload")}>Offload</Button>}
          <Button
            disabled={restore.isPending}
            onClick={() => restore.mutate({ accountId: account.id, start: { thread_ids: [...picked] }, archive }, { onSuccess: clearPicks })}
          >
            Restore to Gmail
          </Button>
          {archive ? (
            <Button onClick={() => void pickForever()}>Delete forever…</Button>
          ) : (
            <>
              <Button onClick={() => setPickedAction("delete")}>Delete</Button>
              <Button onClick={() => setPickedAction("rule")}>Make a rule…</Button>
            </>
          )}
          <Button tone="quiet" onClick={clearPicks}>
            Clear
          </Button>
        </section>
      )}
      {pickedAction === "offload" && <OffloadMailDialog account={account} start={{ thread_ids: [...picked] }} onClose={() => setPickedAction(null)} />}
      {pickedAction === "delete" && (
        <DeleteMailDialog account={account} start={{ thread_ids: [...picked] }} onDone={clearPicks} onClose={() => setPickedAction(null)} />
      )}
      {pickedAction === "rule" && <MakeRuleDialog account={account} messageIds={ruleFrom} onClose={() => setPickedAction(null)} />}
      {forever && (
        <DeleteForeverDialog
          account={account}
          start={{ message_ids: forever }}
          count={forever.length}
          onClose={() => {
            setForever(null);
            clearPicks();
          }}
        />
      )}
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
