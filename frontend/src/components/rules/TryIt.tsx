import { useState } from "react";

import { friendlyError } from "../../api/client";
import type { RuleFields } from "../../api/rules";
import { useTryRule } from "../../hooks/useRules";
import { Button, Field, inputClass, Notice } from "../ui";

/** "Try it": the rule as it stands in the form, on some mail, without saving or changing anything. */
export function TryIt({ accountId, rule, ruleId }: { accountId: number; rule: RuleFields; ruleId?: number }) {
  const [q, setQ] = useState("in:inbox");
  const tryRule = useTryRule();
  const run = () => tryRule.mutate({ accountId, rule, ruleId, q: q.trim() });
  const results = tryRule.data;
  return (
    <section aria-label="Try it" className="space-y-2 rounded-card border border-line p-3">
      <h3 className="text-body font-semibold">Try it</h3>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Field label="Mail to try it on" hint="Empty means the latest 50 mails in the inbox. Nothing is changed.">
            <input
              className={inputClass}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  run();
                }
              }}
            />
          </Field>
        </div>
        <Button onClick={run} disabled={tryRule.isPending}>
          Try
        </Button>
      </div>
      {tryRule.isPending && <p className="text-table text-ink-2">Trying…</p>}
      {tryRule.isError && <Notice tone="err">{friendlyError(tryRule.error)}</Notice>}
      {results && results.length === 0 && <p className="text-table text-ink-2">No mail found for that search.</p>}
      {results && results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-table">
            <thead className="text-meta text-ink-2">
              <tr>
                <th className="py-1 pr-3 font-medium">Mail</th>
                <th className="py-1 pr-3 font-medium">Goes to</th>
                <th className="py-1 font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.message_id} className="border-t border-line align-top">
                  <td className="max-w-[16rem] py-1.5 pr-3">
                    <span className="block truncate">{r.subject || "(no subject)"}</span>
                    <span className="block truncate text-meta text-ink-2">{r.sender}</span>
                  </td>
                  <td className={`py-1.5 pr-3 ${r.this_rule ? "font-semibold" : ""}`}>{r.this_rule ? "This rule" : (r.rule_name ?? "No rule")}</td>
                  <td className="py-1.5 text-ink-2">{r.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
