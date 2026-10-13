import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { type Rule, type RunStart, useSorting } from "../../api/rules";
import type { Account } from "../../api/types";
import { useDraftFromMail } from "../../hooks/useRules";
import { mailView } from "../../lib/mail";
import { olderMailScope, runBlock } from "../../lib/ruleRuns";
import { RuleForm } from "../rules/RuleForm";
import { Button, Dialog, Notice } from "../ui";
import { RunRulesDialog } from "./RunRulesDialog";

/** "Make a rule…" from picked mail: the AI writes a draft from them, the person edits and saves
 * it, then may run it on older mail. Nothing is saved until Save rule. */
export function MakeRuleDialog({ account, messageIds, onClose }: { account: Account; messageIds: number[]; onClose: () => void }) {
  const [params] = useSearchParams();
  const label = params.get("label") ?? "";
  const draft = useDraftFromMail();
  const modelSet = useSorting(account.id).data?.model_set ?? true;
  const [saved, setSaved] = useState<Rule | null>(null);
  const [run, setRun] = useState<RunStart | null>(null);
  const asked = useRef(false);
  const write = () => draft.mutate({ accountId: account.id, messageIds });
  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    write();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the dialog opens
  }, []);

  if (run) return <RunRulesDialog account={account} start={run} onClose={onClose} />;

  if (saved) {
    const scope = olderMailScope(draft.data?.conditions.senders_in ?? [], mailView(params.get("view")), label, params.get("q") ?? "");
    const blocked = runBlock(saved, modelSet);
    return (
      <Dialog
        title="Rule saved"
        onClose={onClose}
        footer={
          <>
            <Button onClick={onClose}>Done</Button>
            {!blocked && (
              <Button tone="primary" data-autofocus onClick={() => setRun({ rule_ids: [saved.id], scope })}>
                Run it on older mail…
              </Button>
            )}
          </>
        }
      >
        <p>“{saved.name}” now looks at new mail. It can also look at mail you already have.</p>
        {blocked && <p className="mt-2 text-ink-2">{blocked}</p>}
      </Dialog>
    );
  }

  const d = draft.data;
  return (
    <Dialog title="Make a rule" wide onClose={onClose}>
      {draft.isPending && <p className="text-ink-2">Writing a rule from {messageIds.length === 1 ? "this mail" : `these ${messageIds.length} mails`}…</p>}
      {draft.isError && (
        <>
          <Notice tone="err">{friendlyError(draft.error)}</Notice>
          <div className="flex flex-wrap justify-end gap-2">
            <Button onClick={onClose}>Cancel</Button>
            <Button tone="primary" onClick={write}>
              Try again
            </Button>
          </div>
        </>
      )}
      {d && (
        <div className="max-h-[70vh] space-y-3 overflow-y-auto">
          {d.notes.length > 0 && (
            <ul aria-label="Notes on the draft" className="list-disc space-y-1 pl-5 text-table text-ink-2">
              {d.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
          <RuleForm accountId={account.id} initial={d} onSaved={setSaved} onCancel={onClose} />
        </div>
      )}
    </Dialog>
  );
}
