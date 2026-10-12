import { friendlyError } from "../../api/client";
import { useRules } from "../../api/rules";
import { useCorrect } from "../../hooks/useRules";
import { Menu, type MenuItem } from "../ui";

/** "Correct…" on a rule chip: the mailbox's rules plus "No rule". Picking one moves the decision there. */
export function CorrectRuleMenu({
  accountId,
  decisionId,
  onDone,
  x = 16,
  y = 16,
  opener = null,
}: {
  accountId: number;
  decisionId: number;
  onDone: () => void;
  x?: number;
  y?: number;
  opener?: HTMLElement | null;
}) {
  const rules = useRules(accountId);
  const correct = useCorrect();
  const pick = (ruleId: number | null) => correct.mutate({ accountId, decisionId, ruleId });
  const items: MenuItem[] = rules.isPending
    ? [{ note: "Loading rules…" }]
    : rules.isError
      ? [{ note: friendlyError(rules.error) }]
      : [
          { note: "Which rule should this mail have matched?" },
          ...rules.data.map((r) => ({ label: r.name, onSelect: () => pick(r.id) })),
          { separator: true as const },
          { label: "No rule", onSelect: () => pick(null) },
        ];
  // A new key once the rules arrive, so the menu opens again with focus on its first item.
  return <Menu key={rules.status} label="Correct the rule" items={items} x={x} y={y} opener={opener} onClose={onDone} />;
}
