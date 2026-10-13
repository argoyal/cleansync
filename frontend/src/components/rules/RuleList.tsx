import { useState } from "react";

import { friendlyError } from "../../api/client";
import { describeAction, type Rule, ruleFields } from "../../api/rules";
import { useDeleteRule, useForgetSender, useReorderRules, useSaveRule } from "../../hooks/useRules";
import { runBlock } from "../../lib/ruleRuns";
import { useToast } from "../Toasts";
import { Button, Dialog, Notice } from "../ui";
import { RuleForm } from "./RuleForm";
import { TryIt } from "./TryIt";

const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;

/** `ids` with the item at `from` moved to `to`. */
function moved(ids: number[], from: number, to: number): number[] {
  const next = [...ids];
  const [id] = next.splice(from, 1);
  next.splice(to, 0, id);
  return next;
}

function OnOff({ rule, accountId }: { rule: Rule; accountId: number }) {
  const save = useSaveRule();
  const toast = useToast();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={rule.enabled}
      aria-label={`On, ${rule.name}`}
      disabled={save.isPending}
      onClick={() =>
        save.mutate(
          { accountId, id: rule.id, rule: { ...ruleFields(rule), enabled: !rule.enabled } },
          { onError: (e) => toast(friendlyError(e), { tone: "error" }) },
        )
      }
      className="inline-flex h-9 items-center gap-2 rounded-ctl px-2 text-table hover:bg-sunken disabled:opacity-50"
    >
      <span aria-hidden="true" className={`relative h-5 w-9 rounded-full transition-colors ${rule.enabled ? "bg-accent" : "bg-line"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow-float transition-[left] ${rule.enabled ? "left-[18px]" : "left-0.5"}`} />
      </span>
      {rule.enabled ? "On" : "Off"}
    </button>
  );
}

function Senders({ rule, accountId }: { rule: Rule; accountId: number }) {
  const [open, setOpen] = useState(false);
  const forget = useForgetSender();
  const remembered = rule.senders.filter((s) => s.remembered);
  if (remembered.length === 0) return null;
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="text-table font-semibold text-accent">
        Remembered senders ({remembered.length})
      </button>
      {open && (
        <ul aria-label={`Remembered senders for ${rule.name}`} className="mt-1 space-y-1">
          {remembered.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2 text-table">
              <span className="min-w-0 truncate">{s.sender}</span>
              <Button
                className="h-8 shrink-0"
                disabled={forget.isPending}
                onClick={() => forget.mutate({ accountId, ruleId: rule.id, senderId: s.id })}
                aria-label={`Forget ${s.sender}`}
              >
                Forget
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Run on a rule card (running rules spec §3): this rule alone over the Inbox. Disabled, with
 * the reason beside it, when the rule is off or needs a model that isn't set up. */
function RunButton({ rule, modelSet, onRun }: { rule: Rule; modelSet: boolean; onRun: (rule: Rule) => void }) {
  const blocked = runBlock(rule, modelSet);
  const why = `run-why-${rule.id}`;
  return (
    <>
      <Button className="h-8" disabled={blocked !== null} aria-label={`Run, ${rule.name}`} aria-describedby={blocked ? why : undefined} onClick={() => onRun(rule)}>
        Run
      </Button>
      {blocked && (
        <span id={why} className="self-center text-meta text-ink-2">
          {blocked}
        </span>
      )}
    </>
  );
}

/** The rules in the order they are asked, as cards. Drag a card, or use Move up / Move down.
 * `onRun` opens a run of one rule over the Inbox; `modelSet` says whether Sorting has a model. */
export function RuleList({ rules, accountId, modelSet, onRun }: { rules: Rule[]; accountId: number; modelSet: boolean; onRun: (rule: Rule) => void }) {
  const [editing, setEditing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const [dragged, setDragged] = useState<number | null>(null);
  const reorder = useReorderRules();
  const remove = useDeleteRule();
  const toast = useToast();
  const ids = rules.map((r) => r.id);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= rules.length || from === to) return;
    reorder.mutate({ accountId, ids: moved(ids, from, to) });
  };

  if (rules.length === 0) return <p className="text-ink-2">No rules yet. Write one above, or add some starter rules.</p>;

  return (
    <>
      <ol aria-label="Rules" className="space-y-3">
        {rules.map((rule, i) => (
          <li
            key={rule.id}
            draggable={editing === null}
            onDragStart={(e) => {
              setDragged(i);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => dragged !== null && e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (dragged !== null) move(dragged, i);
              setDragged(null);
            }}
            onDragEnd={() => setDragged(null)}
            className={`rounded-card border border-line bg-surface p-4 ${dragged === i ? "opacity-50" : ""}`}
          >
            {editing === rule.id ? (
              <RuleForm
                accountId={accountId}
                initial={rule}
                ruleId={rule.id}
                onSaved={() => setEditing(null)}
                onCancel={() => setEditing(null)}
                tryPanel={(fields) => <TryIt accountId={accountId} rule={fields} ruleId={rule.id} />}
              />
            ) : (
              <div className="space-y-2">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="break-words">{rule.description || rule.name}</p>
                    <p className="text-table">{rule.actions.map(describeAction).join(", ")}</p>
                    {rule.blocked && <Notice tone="warn">{rule.blocked}</Notice>}
                  </div>
                  <OnOff rule={rule} accountId={accountId} />
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-ink-2">
                  <span className="rounded-full bg-sunken px-2 py-0.5 font-semibold text-ink">{rule.mode === "automatic" ? "Automatic" : "Suggests"}</span>
                  <span>decided {mails(rule.decided_7d)} in 7 days</span>
                </div>
                <Senders rule={rule} accountId={accountId} />
                <div className="flex flex-wrap gap-2">
                  <Button className="h-8" onClick={() => setEditing(rule.id)}>
                    Edit
                  </Button>
                  <RunButton rule={rule} modelSet={modelSet} onRun={onRun} />
                  <Button className="h-8" onClick={() => setDeleting(rule)}>
                    Delete
                  </Button>
                  <Button className="h-8" tone="quiet" disabled={i === 0 || reorder.isPending} onClick={() => move(i, i - 1)} aria-label={`Move up, ${rule.name}`}>
                    Move up
                  </Button>
                  <Button
                    className="h-8"
                    tone="quiet"
                    disabled={i === rules.length - 1 || reorder.isPending}
                    onClick={() => move(i, i + 1)}
                    aria-label={`Move down, ${rule.name}`}
                  >
                    Move down
                  </Button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ol>
      {deleting && (
        <Dialog
          title="Delete rule"
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button onClick={() => setDeleting(null)}>Cancel</Button>
              <Button
                tone="danger"
                data-autofocus
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(
                    { accountId, id: deleting.id },
                    {
                      onSuccess: () => {
                        toast(`Rule “${deleting.name}” deleted.`);
                        setDeleting(null);
                      },
                    },
                  )
                }
              >
                Delete
              </Button>
            </>
          }
        >
          <p>Delete this rule? Its waiting suggestions are withdrawn.</p>
        </Dialog>
      )}
    </>
  );
}
