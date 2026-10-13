import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { mailThreadQuery, useMailCounts, useMailThreads, useMailTotals } from "../../api/queries";
import type { Account, MailPickAll } from "../../api/types";
import { useRestoreMail } from "../../hooks/useMailOffload";
import { useApprove } from "../../hooks/useRules";
import { formatBytes } from "../../lib/format";
import { hasSearch, mailHref, mailView, viewKind, viewTitle } from "../../lib/mail";
import { runOnText } from "../../lib/ruleRuns";
import { OffloadBatches } from "../../pages/MailOffloadPage";
import { useToast } from "../Toasts";
import { Button } from "../ui";
import { ApproveForeverDialog } from "./ApproveForeverDialog";
import { DeleteForeverDialog, ForeverButton } from "./DeleteForeverDialog";
import { MailHeaderActions } from "./MailActions";
import { MailList, type PickState } from "./MailList";
import { isNarrow, MailThread } from "./MailThread";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { OffloadMailDialog } from "./OffloadMailDialog";
import { RulePickerDialog } from "./RulePickerDialog";

/** "Make a rule…" drafts from at most this many picked mails (mail panel spec §6). */
export const RULE_FROM_AT_MOST = 10;
type PickedAction = "offload" | "rule" | "rules" | null;
type Forever = { message_ids: number[] } | "all";

const WHOLE_MAILBOX_MSG = "Search or pick a label first: Offload never takes the whole mailbox.";

/** What the bar says after Pick all (mail panel spec §6). */
export function allPickedText(total: number | undefined, searching: boolean): string {
  const where = searching ? "this search" : "this view";
  if (total === undefined) return `All mails in ${where} are picked.`;
  if (total === 1) return `The 1 mail in ${where} is picked.`;
  return `All ${total.toLocaleString()} mails in ${where} are picked.`;
}

/** Spec §6.1: the list beside the open mail; on phones one column at a time. Mail panel
 * spec §6: Pick all, and the actions of the view on picked mail. */
export function MailReader({ account }: { account: Account }) {
  const [params] = useSearchParams();
  const view = mailView(params.get("view"));
  const batches = view === "offloaded" && params.get("tab") === "batches";
  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const thread = params.get("thread") ?? "";
  const kind = viewKind(view);
  const labelName = useMailCounts(account.id, label !== "").data?.labels.find((l) => l.id === label)?.name;
  const pages = useMailThreads(account.id, { view, label, q }).data?.pages;
  const rows = pages?.flatMap((p) => p.threads) ?? [];
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
  const [all, setAll] = useState(false);
  const [pickedAction, setPickedAction] = useState<PickedAction>(null);
  const [forever, setForever] = useState<Forever | null>(null);
  const [finding, setFinding] = useState(false); // reading which mails the picked threads hold
  const restore = useRestoreMail();
  const approve = useApprove();
  const [askForever, setAskForever] = useState<{ ids: number[]; forever: number } | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const pickAll: MailPickAll = { view, ...(label ? { label } : {}), q };
  const clearPicks = () => {
    setPicked(new Set());
    setAll(false);
  };
  // Picks belong to one list: a new view, label or search starts with none.
  // An open Offload or Delete forever dialog closes too: it would rebuild its start from the new search.
  useEffect(() => {
    setPicked(new Set());
    setAll(false);
    setPickedAction(null);
    setForever(null);
  }, [view, label, q]);
  // The list on screen, for a slow read that comes back after the list changed.
  const listKey = `${view}\n${label}\n${q}`;
  const nowList = useRef(listKey);
  useEffect(() => {
    nowList.current = listKey;
  }, [listKey]);
  const pickState: PickState = all ? "all" : picked.size > 0 ? "some" : "none";
  const isPicked = (id: string) => all || picked.has(id);
  const toggle = (id: string) => {
    // Unticking one row after Pick all leaves a pick by hand: the rows on screen but that one.
    const next = all ? new Set(rows.map((r) => r.thread_id)) : new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setAll(false);
    setPicked(next);
  };
  const togglePickAll = () => {
    if (all) return clearPicks();
    setPicked(new Set());
    setAll(true);
  };
  const pickedRows = rows.filter((t) => isPicked(t.thread_id));
  const suggestedIds = rows.flatMap((t) => (t.decision?.status === "suggested" ? [t.decision.id] : []));
  const foreverIds = new Set(rows.flatMap((t) => (t.decision?.forever ? [t.decision.id] : [])));
  // Delete forever suggestions need their count typed first (plan decision 16).
  const approveIds = (ids: number[], done?: () => void) => {
    const forever = ids.filter((id) => foreverIds.has(id)).length;
    if (forever > 0) return setAskForever({ ids, forever });
    approve.mutate({ accountId: account.id, ids }, { onSuccess: done });
  };
  const pickedSuggestions = pickedRows.flatMap((t) => (t.decision?.status === "suggested" ? [t.decision.id] : []));
  const ruleFrom = pickedRows.map((t) => t.message_id).slice(0, RULE_FROM_AT_MOST);
  // Delete forever takes mails, not threads: read which mails of this view the picked threads hold.
  const pickForever = () => {
    if (all) return setForever("all");
    if (finding) return;
    setFinding(true);
    const startedOn = listKey;
    Promise.all([...picked].map((id) => qc.fetchQuery(mailThreadQuery(account.id, id, view))))
      // The view or search changed while this was reading: its mails belong to the old list, so drop them.
      .then((threads) => nowList.current === startedOn && setForever({ message_ids: threads.flat().map((m) => m.id) }))
      .catch((e: unknown) => toast(friendlyError(e), { tone: "error" }))
      .finally(() => setFinding(false));
  };
  const restorePicked = () =>
    restore.mutate(all ? { accountId: account.id, start: pickAll } : { accountId: account.id, start: { thread_ids: [...picked] }, view }, { onSuccess: clearPicks });
  const searching = hasSearch(q);
  // Pick all of All mail with no search and no label is the whole mailbox: Offload refuses it.
  const wholeMailbox = all && view === "all" && !label && !searching;
  const title = searching ? "Search results" : viewTitle(view, label ? (labelName ?? label) : null);
  const scope = label ? (labelName ?? label) : view !== "all" ? viewTitle(view) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-4 sm:px-6">
        <h1 className="text-title font-semibold">{title}</h1>
        {searching && <span className="text-table text-ink-2">for {q}</span>}
        {totals && (
          <span className="text-table text-ink-2 tabular">
            {totals.total.toLocaleString()} {totals.total === 1 ? "mail" : "mails"} · {formatBytes(totals.total_bytes)}
          </span>
        )}
        {view === "suggested" && (
          <Button tone="primary" disabled={suggestedIds.length === 0 || approve.isPending} onClick={() => approveIds(suggestedIds)}>
            Approve all
          </Button>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <MailHeaderActions account={account} q={q} view={view} label={label} scope={scope} total={totals?.total} picking={pickState !== "none"} />
        </div>
      </div>
      {view === "offloaded" && (
        <nav aria-label="Offloaded" className="flex gap-4 px-4 pb-2 text-table font-semibold sm:px-6">
          <Link to={mailHref({ view: "offloaded" })} aria-current={batches ? undefined : "page"} className={batches ? "text-ink-2" : "text-accent"}>
            Mail
          </Link>
          <Link to={mailHref({ view: "offloaded", tab: "batches" })} aria-current={batches ? "page" : undefined} className={batches ? "text-accent" : "text-ink-2"}>
            Batches
          </Link>
        </nav>
      )}
      {pickState !== "none" && !batches && (
        <section aria-label="Picked mail" className="flex flex-wrap items-center gap-3 border-t border-line bg-sunken px-4 py-2 sm:px-6">
          <span className="text-table">
            {all ? allPickedText(totals?.total, searching) : `${picked.size.toLocaleString()} ${picked.size === 1 ? "thread" : "threads"} picked`}
          </span>
          {kind === "gmail" && (
            <>
              <Button disabled={wholeMailbox} onClick={() => setPickedAction("offload")}>
                Offload
              </Button>
              {wholeMailbox && <span className="text-table text-ink-2">{WHOLE_MAILBOX_MSG}</span>}
              <Button onClick={() => setPickedAction("rule")}>Make a rule…</Button>
              <Button onClick={() => setPickedAction("rules")}>Run rules</Button>
              <ForeverButton account={account} disabled={finding || wholeMailbox || (all && totals === undefined)} onClick={pickForever} />
            </>
          )}
          {kind === "out" && (
            <>
              <Button disabled={restore.isPending} onClick={restorePicked}>
                Restore to Gmail
              </Button>
              <Button tone="danger" disabled={finding || (all && totals === undefined)} onClick={pickForever}>
                Delete forever…
              </Button>
            </>
          )}
          {kind === "suggested" && (
            <Button
              tone="primary"
              disabled={pickedSuggestions.length === 0 || approve.isPending}
              onClick={() => approveIds(pickedSuggestions, clearPicks)}
            >
              Approve
            </Button>
          )}
          <Button tone="quiet" onClick={clearPicks}>
            Clear
          </Button>
        </section>
      )}
      {pickedAction === "offload" && (
        <OffloadMailDialog account={account} start={all ? pickAll : { thread_ids: [...picked] }} onClose={() => setPickedAction(null)} />
      )}
      {pickedAction === "rule" && <MakeRuleDialog account={account} messageIds={ruleFrom} onClose={() => setPickedAction(null)} />}
      {pickedAction === "rules" && (
        <RulePickerDialog
          account={account}
          scope={all ? { ...pickAll, pick_all: true } : { view, ...(label ? { label } : {}), thread_ids: [...picked] }}
          onText={all ? runOnText({ total: totals?.total, q, place: scope ?? "All mail" }) : runOnText({ threads: picked.size })}
          onClose={() => setPickedAction(null)}
        />
      )}
      {askForever && (
        <ApproveForeverDialog accountId={account.id} ids={askForever.ids} forever={askForever.forever} onDone={clearPicks} onClose={() => setAskForever(null)} />
      )}
      {forever && (
        <DeleteForeverDialog
          account={account}
          start={forever === "all" ? pickAll : { view, message_ids: forever.message_ids }}
          count={forever === "all" ? (totals?.total ?? 0) : forever.message_ids.length}
          onDone={clearPicks}
          onClose={() => setForever(null)}
        />
      )}
      {batches ? (
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line p-4 sm:p-6">
          <OffloadBatches accountId={account.id} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 border-t border-line">
          <section ref={list} aria-label="Mail list" className={`min-w-0 overflow-y-auto xl:w-[440px] xl:shrink-0 xl:border-r xl:border-line ${thread ? "hidden xl:block" : "flex-1 xl:flex-none"}`}>
            <MailList
              accountId={account.id}
              view={view}
              label={label}
              q={q}
              thread={thread}
              isPicked={isPicked}
              onToggle={toggle}
              pickState={pickState}
              onPickAll={kind === "suggested" ? undefined : togglePickAll}
            />
          </section>
          <section aria-label="Open mail" className={`min-w-0 flex-1 overflow-y-auto ${thread ? "" : "hidden xl:block"}`}>
            {thread ? <MailThread account={account} threadId={thread} onBack={() => (backFrom.current = thread)} /> : <p className="p-6 text-ink-2">Pick a mail to read it.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
