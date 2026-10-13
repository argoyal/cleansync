import { useState } from "react";

import { friendlyError } from "../../api/client";
import { describeAction, type RunScope, useRules, useSorting } from "../../api/rules";
import type { Account } from "../../api/types";
import { runBlock } from "../../lib/ruleRuns";
import { Button, Dialog, Notice } from "../ui";
import { RunRulesDialog } from "./RunRulesDialog";

/** Run rules on a search or a pick (running rules spec §4): tick the rules to run first. Only
 * rules that are on are listed, in their order; each mail gets the first ticked rule that fits.
 * `onText` says what they will run on. */
export function RulePickerDialog({ account, scope, onText, onClose }: { account: Account; scope: RunScope; onText: string; onClose: () => void }) {
  const rules = useRules(account.id);
  const modelSet = useSorting(account.id).data?.model_set ?? true;
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [run, setRun] = useState<number[] | null>(null);
  if (run) return <RunRulesDialog account={account} start={{ rule_ids: run, scope }} onClose={onClose} />;
  const on = (rules.data ?? []).filter((r) => r.enabled);
  const toggle = (id: number) => {
    const next = new Set(ticked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicked(next);
  };
  return (
    <Dialog
      title="Run rules"
      wide
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" disabled={ticked.size === 0} onClick={() => setRun(on.filter((r) => ticked.has(r.id)).map((r) => r.id))}>
            Run
          </Button>
        </>
      }
    >
      <p>{onText}</p>
      {rules.isPending && <p className="text-ink-2">Loading rules…</p>}
      {rules.isError && <Notice tone="err">{friendlyError(rules.error)}</Notice>}
      {rules.data && on.length === 0 && <p className="text-ink-2">This mailbox has no rules that are on. Add one in Mail → Rules.</p>}
      <ul aria-label="Rules to run" className="max-h-[50vh] space-y-2 overflow-y-auto">
        {on.map((r) => {
          const why = runBlock(r, modelSet);
          return (
            <li key={r.id}>
              <label className="flex gap-2">
                <input type="checkbox" className="mt-1" disabled={why !== null} checked={ticked.has(r.id)} onChange={() => toggle(r.id)} />
                <span>
                  <span className="block font-semibold">{r.name}</span>
                  <span className="block text-table">{r.actions.map(describeAction).join(", ")}</span>
                  {why && <span className="block text-meta text-ink-2">{why}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
