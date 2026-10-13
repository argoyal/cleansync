import { useEffect, useRef, useState } from "react";

import { describeAction, type JobPreview, useRuleJob } from "../../api/rules";
import type { Account } from "../../api/types";
import { useRunRuleJob, useStartRuleJob, useStopRuleJob } from "../../hooks/useRules";
import { pauseLine } from "../../lib/pause";
import { Bar, Button, Dialog, Field, inputClass, Notice } from "../ui";
import { parseTyped } from "./OffloadMailDialog";

export type RunRulesStart = { q: string; view: string; message_ids?: number[] };

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;
const pct = (done: number, of: number) => (of > 0 ? (done / of) * 100 : 0);

function Preview({ job }: { job: JobPreview }) {
  return (
    <div className="space-y-3">
      <p className="text-ink-2">Looked at {mails(job.total)}. Nothing has changed yet.</p>
      <ul aria-label="What the rules would do" className="space-y-3">
        {job.per_rule.map((r) => (
          <li key={r.rule} className="rounded-card border border-line p-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="font-semibold">{r.rule}</span>
              <span className="text-table text-ink-2 tabular">{mails(r.count)}</span>
            </div>
            <p className="text-table">{r.actions.map(describeAction).join(", ")}</p>
            {r.examples.length > 0 && (
              <ul aria-label={`Examples for ${r.rule}`} className="mt-2 space-y-1 text-table">
                {r.examples.slice(0, 5).map((e, i) => (
                  <li key={i} className="min-w-0">
                    <span className="block truncate">
                      {e.subject || "(no subject)"} <span className="text-ink-2">· {e.sender}</span>
                    </span>
                    {e.why && <span className="block text-meta text-ink-2">{e.why}</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      <p className="text-table text-ink-2">No rule: {job.no_rule.toLocaleString()}</p>
    </div>
  );
}

type Props = { account: Account; onClose: () => void } & ({ start: RunRulesStart; jobId?: undefined } | { jobId: number; start?: undefined });

/** "Run rules on…" (AI rules spec §6): the rules decide for each mail first (a preview that
 * changes nothing), then the person types the count and the changes are made.
 * With `jobId` it shows a run that is already there instead of starting one. */
export function RunRulesDialog({ account, start, jobId: existing, onClose }: Props) {
  const begin = useStartRuleJob();
  const runJob = useRunRuleJob();
  const stop = useStopRuleJob();
  const [jobId, setJobId] = useState<number | null>(existing ?? null);
  const [typed, setTyped] = useState("");
  const asked = useRef(false); // one job per opening, even when an effect runs twice
  const closed = useRef(false); // closed before the job had started: stop it once it has an id
  useEffect(() => {
    if (asked.current || !start) return;
    asked.current = true;
    begin
      .mutateAsync({ accountId: account.id, q: start.q, view: start.view, messageIds: start.message_ids })
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
  const ok = planned > 0 && parseTyped(typed) === planned;
  const finished = state === "DONE" || state === "STOPPED";
  const changed = job ? job.applied + job.skipped + job.failed : 0;
  const stopJob = () => jobId !== null && stop.mutate({ accountId: account.id, jobId });
  const cancel = () => {
    closed.current = true;
    // Only a run that is making changes keeps going: a preview left open would block the next run.
    if (state === "PREVIEWING" || state === "PREVIEWED" || (jobId !== null && !job && existing === undefined)) stopJob();
    onClose();
  };
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
          <Button onClick={cancel}>{finished || state === "RUNNING" || state === "FAILED" ? "Close" : "Cancel"}</Button>
          {state === "PREVIEWED" && planned > 0 && (
            <Button
              tone="danger"
              disabled={!ok || runJob.isPending}
              onClick={() => runJob.mutate({ accountId: account.id, jobId: job!.job_id, confirm: planned }, { onError: () => setTyped("") })}
            >
              {`Change ${planned.toLocaleString()} ${planned === 1 ? "mail" : "mails"}`}
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
      {job && state === "PREVIEWED" && (
        <>
          <Preview job={job} />
          {planned > 0 ? (
            <Field label={`Type ${planned.toLocaleString()} to make these changes`}>
              <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          ) : (
            <p>No mail matches a rule. Nothing was changed.</p>
          )}
        </>
      )}
      {job && state === "RUNNING" && (
        <div className="space-y-2">
          <p>
            Changing… {changed.toLocaleString()} of {planned.toLocaleString()}
          </p>
          <Bar value={pct(changed, planned)} label="Changing" tone="run" />
          <p className="text-table text-ink-2">It keeps going if you close this.</p>
        </div>
      )}
      {job && finished && (
        <div className="space-y-2">
          <p>
            {state === "STOPPED" ? "Stopped. " : "Done. "}
            Applied {job.applied.toLocaleString()}, skipped {job.skipped.toLocaleString()}, failed {job.failed.toLocaleString()}.
          </p>
          {job.reason && <p className="text-table text-ink-2">{job.reason}</p>}
          {job.skipped > 0 && (
            <p className="text-table text-ink-2">
              {job.skipped === 1 ? "1 mail was left as it was" : `${mails(job.skipped)} were left as they were`}
              {state === "DONE" ? ` because ${job.skipped === 1 ? "it" : "they"} changed since the preview.` : "."}
            </p>
          )}
        </div>
      )}
      {job && state === "FAILED" && <Notice tone="err">{job.reason ?? "The rules stopped. Try again."}</Notice>}
    </Dialog>
  );
}
