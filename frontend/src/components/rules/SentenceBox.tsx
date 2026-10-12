import { useState } from "react";

import { friendlyError } from "../../api/client";
import { useDraftRule } from "../../hooks/useRules";
import { useToast } from "../Toasts";
import { Button, Field, inputClass, Notice } from "../ui";
import { RuleForm } from "./RuleForm";
import { TryIt } from "./TryIt";

/** Write a rule in your own words: the AI drafts it, the form shows it, and nothing is saved
 * until Save rule. */
export function SentenceBox({ accountId }: { accountId: number }) {
  const [sentence, setSentence] = useState("");
  const draft = useDraftRule();
  const toast = useToast();
  const d = draft.data;
  const write = () => sentence.trim() && draft.mutate({ accountId, sentence: sentence.trim() });
  return (
    <section aria-label="Write a rule" className="space-y-3 rounded-card border border-line bg-surface p-4">
      <Field label="Describe mail to sort, in your own words">
        <textarea
          className={`${inputClass} h-auto min-h-[4.5rem] py-2`}
          placeholder="Archive newsletters I haven't opened in a month, except from Substack"
          value={sentence}
          onChange={(e) => setSentence(e.target.value)}
        />
      </Field>
      <div className="flex justify-end">
        <Button tone="primary" disabled={draft.isPending || !sentence.trim()} onClick={write}>
          Write the rule
        </Button>
      </div>
      {draft.isPending && <p className="text-table text-ink-2">Writing the rule…</p>}
      {draft.isError && <Notice tone="err">{friendlyError(draft.error)}</Notice>}
      {d && (
        <div className="space-y-3 border-t border-line pt-3">
          {d.notes.length > 0 && (
            <ul aria-label="Notes on the draft" className="list-disc space-y-1 pl-5 text-table text-ink-2">
              {d.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
          <RuleForm
            key={draft.submittedAt}
            accountId={accountId}
            initial={d}
            onSaved={(r) => {
              toast(`Rule “${r.name}” saved.`);
              setSentence("");
              draft.reset();
            }}
            onCancel={() => draft.reset()}
            tryPanel={(rule) => <TryIt accountId={accountId} rule={rule} />}
          />
        </div>
      )}
    </section>
  );
}
