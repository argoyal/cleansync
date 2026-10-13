import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { friendlyError } from "../api/client";
import { describeAction, type Rule, rulesExportUrl, type Sorting, useOpenRuleJob, useRules, useSorting, useStarters } from "../api/rules";
import type { Account } from "../api/types";
import { RunRulesDialog } from "../components/mail/RunRulesDialog";
import { RuleList } from "../components/rules/RuleList";
import { SentenceBox } from "../components/rules/SentenceBox";
import { useToast } from "../components/Toasts";
import { Button, Dialog, Empty, Field, inputClass, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { useAddStarters, useCopyRules, useImportRules, useSetSorting } from "../hooks/useRules";

const rulesWord = (n: number) => `${n} ${n === 1 ? "rule" : "rules"}`;

function SortingSwitch({ accountId, sorting }: { accountId: number; sorting: Sorting | undefined }) {
  const set = useSetSorting();
  const on = sorting?.enabled ?? false;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label="Sort new inbox mail"
      disabled={!sorting || set.isPending}
      onClick={() => set.mutate({ accountId, enabled: !on })}
      className="inline-flex h-9 items-center gap-2 rounded-ctl px-2 text-body font-medium hover:bg-sunken disabled:opacity-50"
    >
      <span aria-hidden="true" className={`relative h-5 w-9 rounded-full transition-colors ${on ? "bg-accent" : "bg-line"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow-float transition-[left] ${on ? "left-[18px]" : "left-0.5"}`} />
      </span>
      Sort new inbox mail
    </button>
  );
}

function StartersDialog({ accountId, onClose }: { accountId: number; onClose: () => void }) {
  const starters = useStarters();
  const add = useAddStarters();
  const toast = useToast();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const toggle = (name: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(name);
    else next.delete(name);
    setPicked(next);
  };
  return (
    <Dialog
      title="Starter rules"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            disabled={picked.size === 0 || add.isPending}
            onClick={() =>
              add.mutate(
                { accountId, names: [...picked] },
                {
                  onSuccess: (rules) => {
                    toast(`Added ${rulesWord(rules.length)}.`);
                    onClose();
                  },
                },
              )
            }
          >
            Add
          </Button>
        </>
      }
    >
      {starters.isPending && <p className="text-ink-2">Loading…</p>}
      {starters.isError && <Notice tone="err">{friendlyError(starters.error)}</Notice>}
      <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
        {starters.data?.map((s) => (
          <li key={s.name}>
            <label className="flex gap-2">
              <input type="checkbox" className="mt-1" checked={picked.has(s.name)} onChange={(e) => toggle(s.name, e.target.checked)} />
              <span>
                <span className="block font-semibold">{s.name}</span>
                <span className="block text-table text-ink-2">{s.description}</span>
                <span className="block text-table">{s.actions.map(describeAction).join(", ")}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

function CopyDialog({ account, others, onClose }: { account: Account; others: Account[]; onClose: () => void }) {
  const copy = useCopyRules();
  const toast = useToast();
  const [to, setTo] = useState(others[0]?.id ?? 0);
  return (
    <Dialog
      title="Copy rules to another mailbox"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            tone="primary"
            disabled={!to || copy.isPending}
            onClick={() =>
              copy.mutate(
                { accountId: account.id, toAccountId: to },
                {
                  onSuccess: ({ copied }) => {
                    toast(`Copied ${rulesWord(copied)}.`);
                    onClose();
                  },
                },
              )
            }
          >
            Copy
          </Button>
        </>
      }
    >
      <p>The rules from {account.email} are added to the other mailbox. Its own rules stay.</p>
      <Field label="Copy to">
        <select className={inputClass} value={to} onChange={(e) => setTo(Number(e.target.value))}>
          {others.map((a) => (
            <option key={a.id} value={a.id}>
              {a.email}
            </option>
          ))}
        </select>
      </Field>
    </Dialog>
  );
}

function Footer({ account }: { account: Account }) {
  const { accounts } = useCurrentAccount();
  const others = accounts.filter((a) => a.id !== account.id);
  const importRules = useImportRules();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState<"starters" | "copy" | null>(null);
  const readFile = async (f: File | undefined) => {
    if (!f) return;
    const yaml = await f.text();
    importRules.mutate({ accountId: account.id, yaml }, { onSuccess: (rules) => toast(`Imported ${rulesWord(rules.length)}.`) });
    if (file.current) file.current.value = "";
  };
  return (
    <div className="flex flex-wrap gap-2 border-t border-line pt-4">
      <Button onClick={() => setOpen("starters")}>Starter rules…</Button>
      <a
        href={rulesExportUrl(account.id)}
        download
        className="inline-flex h-9 items-center rounded-ctl border border-line bg-surface px-3.5 text-body font-medium text-ink hover:bg-sunken"
      >
        Export
      </a>
      <Button disabled={importRules.isPending} onClick={() => file.current?.click()}>
        Import…
      </Button>
      <input ref={file} type="file" accept=".yaml,.yml,text/yaml" aria-label="Rules file to import" className="hidden" onChange={(e) => void readFile(e.target.files?.[0])} />
      {others.length > 0 && <Button onClick={() => setOpen("copy")}>Copy to another mailbox…</Button>}
      {open === "starters" && <StartersDialog accountId={account.id} onClose={() => setOpen(null)} />}
      {open === "copy" && <CopyDialog account={account} others={others} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** A run of rules whose dialog was closed: say so, and open it again. */
function OpenRun({ account }: { account: Account }) {
  const job = useOpenRuleJob(account.id).data;
  const [open, setOpen] = useState(false);
  if (!job) return null;
  const changed = job.applied + job.skipped + job.failed;
  const text =
    job.state === "PREVIEWED"
      ? "A run of rules is waiting for you to confirm."
      : job.state === "RUNNING"
        ? `A run of rules is going on (${changed.toLocaleString()} of ${(changed + job.planned).toLocaleString()} changed).`
        : `A run of rules is going on (${job.decided.toLocaleString()} of ${job.total.toLocaleString()} decided).`;
  return (
    <>
      <Notice tone="run">
        <span className="flex flex-wrap items-center justify-between gap-2">
          {text}
          <Button onClick={() => setOpen(true)}>Open</Button>
        </span>
      </Notice>
      {open && <RunRulesDialog account={account} jobId={job.job_id} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Mail → Rules (AI rules spec §8): rules for the mailbox picked in the account switcher. */
export function MailRulesPage() {
  const { account } = useCurrentAccount();
  const accountId = account?.id ?? null;
  const rules = useRules(accountId);
  const sorting = useSorting(accountId).data;
  const [running, setRunning] = useState<Rule | null>(null);
  if (!account) {
    return (
      <Empty icon="mail" title="No Google account yet">
        Connect a Google account to start.
      </Empty>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="min-w-0 break-words text-title font-bold">Rules for {account.email}</h1>
        <SortingSwitch accountId={account.id} sorting={sorting} />
      </div>
      {sorting && !sorting.model_set && (
        <Notice tone="warn">
          No model is set up yet. Rules with only fixed conditions still run.{" "}
          <Link to="/settings?tab=models" className="font-semibold text-accent">
            Set one up in Settings → Models
          </Link>
        </Notice>
      )}
      <OpenRun key={`run-${account.id}`} account={account} />
      <SentenceBox key={`sentence-${account.id}`} accountId={account.id} />
      {rules.isPending && <p className="text-ink-2">Loading rules…</p>}
      {rules.isError && <Notice tone="err">{friendlyError(rules.error)}</Notice>}
      {rules.data && <RuleList key={`list-${account.id}`} rules={rules.data} accountId={account.id} modelSet={sorting?.model_set ?? true} onRun={setRunning} />}
      {running && <RunRulesDialog key={running.id} account={account} start={{ rule_ids: [running.id], scope: { inbox: true } }} onClose={() => setRunning(null)} />}
      <Footer account={account} />
    </div>
  );
}
