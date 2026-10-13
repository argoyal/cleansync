import { useEffect, useRef, useState } from "react";

import { friendlyError } from "../../api/client";
import { type JobPreview, type RuleMatch, type RunStart, useRuleJob, useRuleMatches } from "../../api/rules";
import type { Account } from "../../api/types";
import { useRunRuleJob, useStartRuleJob, useStopRuleJob } from "../../hooks/useRules";
import { formatDay } from "../../lib/format";
import { pauseLine } from "../../lib/pause";
import { approveText, confirmLabel, heavyKinds, ruleLine, wouldText } from "../../lib/ruleRuns";
import { Bar, Button, Dialog, Field, inputClass, Notice } from "../ui";
import { PickAllBox, type PickState } from "./MailList";
import { parseTyped } from "./OffloadMailDialog";

const pct = (done: number, of: number) => (of > 0 ? (done / of) * 100 : 0);

/** The match list's picks (running rules spec §5). "all": every match except `ids`, the ones
 * left out (what the server stores). "only": just `ids`, after Pick all was unticked. Each id
 * keeps its rule, so the counts per rule are known without loading every page. */
type Picks = { mode: "all" | "only"; ids: Map<number, number | null> };
const everyMatch = (): Picks => ({ mode: "all", ids: new Map() });
const isPicked = (p: Picks, id: number) => (p.mode === "all" ? !p.ids.has(id) : p.ids.has(id));

/** How many of a rule's `count` matches are picked. */
function pickedOf(p: Picks, ruleId: number | null, count: number): number {
  let mine = 0;
  for (const r of p.ids.values()) if (r === ruleId) mine += 1;
  return p.mode === "all" ? count - mine : mine;
}

/** What the run would do: a sentence for one rule, a line per rule for several. */
function MatchHeader({ job }: { job: JobPreview }) {
  const noFit = `${job.no_rule.toLocaleString()} didn't fit.`;
  const [first] = job.per_rule;
  return (
    <div className="space-y-1">
      {job.filter_text && <p className="text-table text-ink-2">On: {job.filter_text}</p>}
      {job.per_rule.length === 1 ? (
        <p>{`${wouldText(first.rule, first.actions, first.count)} ${noFit}`}</p>
      ) : (
        <>
          <ul aria-label="What each rule would do" className="text-table">
            {job.per_rule.map((r) => (
              <li key={`${r.rule_id ?? ""}-${r.rule}`}>{ruleLine(r.rule, r.actions, r.count)}</li>
            ))}
          </ul>
          <p className="text-table text-ink-2">{noFit}</p>
        </>
      )}
    </div>
  );
}

function Matches({
  accountId,
  jobId,
  ruleId,
  several,
  picks,
  onToggle,
}: {
  accountId: number;
  jobId: number;
  ruleId: number | null;
  several: boolean;
  picks: Picks;
  onToggle: (m: RuleMatch) => void;
}) {
  const found = useRuleMatches(accountId, jobId, ruleId);
  const rows = found.data?.pages.flat() ?? [];
  return (
    <>
      {found.isPending && <p className="px-4 py-2 text-ink-2">Loading…</p>}
      {found.isError && <Notice tone="err">{friendlyError(found.error)}</Notice>}
      <ul aria-label="Matched mail" className="max-h-[45vh] overflow-y-auto">
        {rows.map((m) => (
          <li key={m.id} className="flex gap-3 border-b border-line px-4 py-2 last:border-b-0">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 shrink-0 accent-accent"
              aria-label={`Pick ${m.subject || "(no subject)"}`}
              checked={isPicked(picks, m.id)}
              onChange={() => onToggle(m)}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate font-semibold">{m.from_addr}</span>
                <span className="shrink-0 text-meta text-ink-2 tabular">{formatDay(m.date)}</span>
              </div>
              <span className="block truncate">{m.subject || "(no subject)"}</span>
              <span className="block text-meta text-ink-2">
                {several && <span className="mr-2 rounded-full bg-sunken px-2 py-0.5 font-semibold text-ink">{m.rule_name}</span>}
                {m.why}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {found.hasNextPage && (
        <div className="px-4 py-2">
          <Button disabled={found.isFetchingNextPage} onClick={() => void found.fetchNextPage()}>
            Show more
          </Button>
        </div>
      )}
    </>
  );
}

type Props = { account: Account; onClose: () => void } & ({ start: RunStart; jobId?: undefined } | { jobId: number; start?: undefined });

/** Running rules (running rules spec §5): the run's rules decide every mail first (nothing
 * changes in Gmail), then the match list shows what they matched, every mail picked, and the
 * approve button makes the changes for the picked ones. With `jobId` it shows a run that is
 * already there instead of starting one. */
export function RunRulesDialog({ account, start, jobId: existing, onClose }: Props) {
  const begin = useStartRuleJob();
  const runJob = useRunRuleJob();
  const stop = useStopRuleJob();
  const [jobId, setJobId] = useState<number | null>(existing ?? null);
  const [typed, setTyped] = useState("");
  const [picks, setPicks] = useState<Picks>(everyMatch);
  const [shown, setShown] = useState<number | null>(null); // the rule filter; null: every rule
  const asked = useRef(false); // one job per opening, even when an effect runs twice
  const closed = useRef(false); // closed before the job had started: stop it once it has an id
  useEffect(() => {
    if (asked.current || !start) return;
    asked.current = true;
    begin
      .mutateAsync({ accountId: account.id, start })
      .then(({ job_id }) => {
        if (closed.current) stop.mutate({ accountId: account.id, jobId: job_id });
        else setJobId(job_id);
      })
      .catch(() => undefined); // the hook shows the error
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the dialog opens
  }, []);
  const job = useRuleJob(account.id, jobId).data;
  const state = job?.state;
  const deciding = !begin.isError && (!job || state === "PREVIEWING");
  const planned = job?.planned ?? 0;
  const listing = job !== undefined && state === "PREVIEWED" && planned > 0;
  const perRule = (job?.per_rule ?? []).map((r) => ({ ...r, picked: pickedOf(picks, r.rule_id, r.count) }));
  const pickedTotal = picks.mode === "all" ? planned - picks.ids.size : picks.ids.size;
  const pickState: PickState = picks.mode === "all" && picks.ids.size === 0 ? "all" : pickedTotal > 0 ? "some" : "none";
  const heavy = heavyKinds(perRule);
  const ok = pickedTotal > 0 && (heavy.count === 0 || parseTyped(typed) === heavy.count);
  const finished = state === "DONE" || state === "STOPPED";
  const changed = job ? job.applied + job.skipped + job.failed : 0;
  const stopJob = () => jobId !== null && stop.mutate({ accountId: account.id, jobId });
  const cancel = () => {
    closed.current = true;
    // Only a run that is making changes keeps going: a list left open would block the next run.
    if (state === "PREVIEWING" || state === "PREVIEWED" || (jobId !== null && !job && existing === undefined)) stopJob();
    onClose();
  };
  const toggle = (m: RuleMatch) => {
    const ids = new Map(picks.ids);
    if (ids.has(m.id)) ids.delete(m.id);
    else ids.set(m.id, m.rule_id);
    setPicks({ mode: picks.mode, ids });
  };
  const togglePickAll = () => setPicks(pickState === "all" ? { mode: "only", ids: new Map() } : everyMatch());
  const approve = () =>
    runJob.mutate(
      {
        accountId: account.id,
        jobId: job!.job_id,
        ...(picks.mode === "all" ? { leftOut: [...picks.ids.keys()] } : { only: [...picks.ids.keys()] }),
        ...(heavy.count > 0 ? { confirm: heavy.count } : {}),
      },
      { onError: () => setTyped("") },
    );
  return (
    <Dialog
      title="Run rules"
      wide
      onClose={cancel}
      footer={
        <>
          {state === "RUNNING" && (
            <Button tone="danger" disabled={stop.isPending} onClick={stopJob}>
              Stop
            </Button>
          )}
          <Button onClick={cancel}>{deciding || listing ? "Cancel" : "Close"}</Button>
          {listing && (
            <Button tone={heavy.count > 0 ? "danger" : "primary"} disabled={!ok || runJob.isPending} onClick={approve}>
              {approveText(perRule)}
            </Button>
          )}
        </>
      }
    >
      {begin.isError && <Notice tone="err">The rules couldn't start. Nothing was changed.</Notice>}
      {deciding && (
        <div className="space-y-2">
          <p>{job ? `Deciding… ${job.decided.toLocaleString()} of ${job.total.toLocaleString()}` : "Deciding…"}</p>
          <Bar value={job ? pct(job.decided, job.total) : 0} label="Deciding" />
          {job?.paused && <p className="text-table text-ink-2">{pauseLine(job.paused)}</p>}
        </div>
      )}
      {job && state === "PREVIEWED" && planned === 0 && <p>{job.rule_ids.length === 1 ? "No mail fits this rule." : "No mail fits these rules."}</p>}
      {job && listing && (
        <>
          <MatchHeader job={job} />
          {job.per_rule.length > 1 && (
            <Field label="Show">
              <select className={inputClass} value={shown ?? ""} onChange={(e) => setShown(e.target.value === "" ? null : Number(e.target.value))}>
                <option value="">Every rule</option>
                {job.per_rule.map((r) =>
                  r.rule_id === null ? null : (
                    <option key={r.rule_id} value={r.rule_id}>
                      {r.rule}
                    </option>
                  ),
                )}
              </select>
            </Field>
          )}
          <div className="rounded-card border border-line">
            <PickAllBox state={pickState} onChange={togglePickAll} />
            <Matches accountId={account.id} jobId={job.job_id} ruleId={shown} several={job.per_rule.length > 1} picks={picks} onToggle={toggle} />
          </div>
          {heavy.count > 0 && (
            <Field label={confirmLabel(heavy.kinds, heavy.count)}>
              <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          )}
        </>
      )}
      {job && state === "RUNNING" && (
        <div className="space-y-2">
          <p>
            Changing… {changed.toLocaleString()} of {(changed + planned).toLocaleString()}
          </p>
          <Bar value={pct(changed, changed + planned)} label="Changing" tone="run" />
          <p className="text-table text-ink-2">It keeps going if you close this.</p>
        </div>
      )}
      {job && finished && (
        <div className="space-y-2">
          <p>
            {`${state === "STOPPED" ? "Stopped." : "Done."} Applied ${job.applied.toLocaleString()}, skipped ${job.skipped.toLocaleString()}, failed ${job.failed.toLocaleString()}, left out ${job.left_out.toLocaleString()}.`}
          </p>
          {job.reason && <p className="text-table text-ink-2">{job.reason}</p>}
          {job.skip_reasons.length > 0 && (
            <ul aria-label="Why mail was skipped" className="list-disc space-y-1 pl-5 text-table text-ink-2">
              {job.skip_reasons.map((r) => (
                <li key={r.reason}>{`${r.reason} (${r.count.toLocaleString()})`}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {job && state === "FAILED" && <Notice tone="err">{job.reason ?? "The rules stopped. Try again."}</Notice>}
    </Dialog>
  );
}
