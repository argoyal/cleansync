import { useRef, useState } from "react";

import type { DecisionChip } from "../../api/types";
import { useDismiss } from "../../hooks/useDismiss";
import { useApprove, useUndo } from "../../hooks/useRules";
import { Button } from "../ui";
import { CorrectRuleMenu } from "./CorrectRuleMenu";

const POPOVER_WIDTH = 288;

/** "Newsletters · suggested": what a rule decided for this mail. Clicking it shows why, with
 * Approve, Correct… and (once the rule has acted, and Gmail can still take it back) Undo. */
export function RuleChip({
  accountId,
  chip,
  onChanged,
}: {
  accountId: number;
  chip: DecisionChip;
  onChanged?: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const wrap = useRef<HTMLSpanElement>(null); // the chip and its popover: a click on either isn't "outside"
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const [correctAt, setCorrectAt] = useState<{ x: number; y: number } | null>(
    null,
  );
  const approve = useApprove();
  const undo = useUndo();
  useDismiss(wrap, () => setAt(null), at !== null, button);
  const place = () => {
    const r = button.current?.getBoundingClientRect();
    const x = Math.max(8, Math.min(r?.left ?? 8, window.innerWidth - POPOVER_WIDTH - 8));
    return { x, y: (r?.bottom ?? 0) + 4 };
  };
  const done = { onSuccess: () => onChanged?.() };
  const label = `${chip.rule_name} · ${chip.forever && chip.status === "applied" ? "deleted forever" : chip.status}`;
  return (
    <>
      <span ref={wrap} className="inline-flex min-w-0">
        <button
          ref={button}
          type="button"
          aria-expanded={at !== null}
          aria-haspopup="true"
          title={label}
          onClick={() => setAt(at ? null : place())}
          className="inline-flex max-w-[10rem] items-center truncate rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-meta font-medium text-accent hover:bg-accent/20 sm:max-w-[14rem]"
        >
          <span className="truncate">{label}</span>
        </button>
        {at && (
          <div
            role="group"
            aria-label={`Rule: ${chip.rule_name}`}
            style={{ left: at.x, top: at.y, width: POPOVER_WIDTH }}
            className="fixed z-50 max-w-[calc(100vw-16px)] space-y-3 rounded-menu border border-line bg-surface p-3 shadow-float"
          >
            {chip.why && <p className="text-table text-ink-2">{chip.why}</p>}
            <div className="flex flex-wrap gap-2">
              {chip.status === "suggested" && (
                <Button
                  tone="primary"
                  disabled={approve.isPending}
                  onClick={() => {
                    setAt(null);
                    approve.mutate({ accountId, ids: [chip.id] }, done);
                  }}
                >
                  Approve
                </Button>
              )}
              <Button
                onClick={() => {
                  setCorrectAt(at);
                  setAt(null);
                }}
              >
                Correct…
              </Button>
              {chip.status === "applied" && chip.undoable && (
                <Button
                  disabled={undo.isPending}
                  onClick={() => {
                    setAt(null);
                    undo.mutate({ accountId, ids: [chip.id] }, done);
                  }}
                >
                  Undo
                </Button>
              )}
            </div>
          </div>
        )}
      </span>
      {correctAt && (
        <CorrectRuleMenu
          accountId={accountId}
          decisionId={chip.id}
          x={correctAt.x}
          y={correctAt.y}
          opener={button.current}
          onDone={() => setCorrectAt(null)}
        />
      )}
    </>
  );
}
