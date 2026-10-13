import { type ReactNode, useId, useState } from "react";

import { useAccounts } from "../../api/queries";
import { friendlyError } from "../../api/client";
import { actionKind, type Combine, type Rule, type RuleAction, type RuleConditions, type RuleDraft, type RuleFields } from "../../api/rules";
import { useSaveRule } from "../../hooks/useRules";
import { FOREVER_OFF } from "../mail/DeleteForeverDialog";
import { Button, Field, inputClass, Notice } from "../ui";

export const FOREVER_SUGGEST_ONLY = "A Delete forever rule always suggests first.";

type Initial = RuleFields | (RuleDraft & { mode?: RuleFields["mode"]; enabled?: boolean });
type Simple = "archive" | "mark_read" | "star";
type Flag = "list_unsubscribe" | "calendar_invite" | "has_attachments";
type Text = { senders_in: string; senders_not_in: string; subject_contains: string };

const SIMPLE: [Simple, string][] = [
  ["archive", "Archive"],
  ["mark_read", "Mark read"],
  ["star", "Star"],
];
const FLAGS: [Flag, string][] = [
  ["list_unsubscribe", "Has an unsubscribe link"],
  ["calendar_invite", "Is a calendar invite"],
  ["has_attachments", "Has attachments"],
];
const TEXTS: [keyof Text, string, string][] = [
  ["senders_in", "From", "Addresses or domains, separated by commas."],
  ["senders_not_in", "Not from", "Addresses or domains, separated by commas."],
  ["subject_contains", "Subject contains", "Words or phrases, separated by commas."],
];
const COMBINES: [Combine, string][] = [
  ["fixed_only", "Only the fixed conditions"],
  ["fixed_decides", "Fixed conditions decide, else ask the AI"],
  ["fixed_then_ai", "Fixed conditions narrow it, the AI confirms"],
  ["ai_only", "Only the AI"],
];

/** "a@example.com, ,b.example.com " -> ["a@example.com", "b.example.com"] */
export const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

function conditionsFrom(text: Text, flags: Partial<Record<Flag, boolean>>, addressed: string): RuleConditions {
  const c: RuleConditions = {};
  for (const [key] of TEXTS) {
    const list = splitList(text[key]);
    if (list.length > 0) c[key] = list;
  }
  for (const [key] of FLAGS) if (flags[key] !== undefined) c[key] = flags[key];
  if (addressed) c.addressed = addressed as RuleConditions["addressed"];
  return c;
}

/**
 * The rule editor (AI rules spec §8): what the rule is for in words, what it does, optional
 * fixed conditions, and whether it suggests or acts. Nothing is saved until Save rule.
 * `tryPanel` is where a Try it panel goes; it gets the rule as it stands in the form.
 */
export function RuleForm({
  accountId,
  initial,
  ruleId,
  onSaved,
  onCancel,
  tryPanel,
}: {
  accountId: number;
  initial: Initial;
  ruleId?: number;
  onSaved: (rule: Rule) => void;
  onCancel: () => void;
  tryPanel?: (rule: RuleFields) => ReactNode;
}) {
  const id = useId();
  const c0 = initial.conditions;
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [simple, setSimple] = useState<Set<Simple>>(new Set(initial.actions.flatMap((a) => (SIMPLE.some(([k]) => k === actionKind(a)) ? [a.kind as Simple] : []))));
  const firstLabel = initial.actions.find((a) => a.kind === "label");
  const [labelOn, setLabelOn] = useState(firstLabel !== undefined);
  const [labelName, setLabelName] = useState(firstLabel && "label" in firstLabel ? firstLabel.label : "");
  const [off, setOff] = useState(initial.actions.some((a) => actionKind(a) === "offload"));
  const [forever, setForever] = useState(initial.actions.some((a) => a.kind === "delete_forever"));
  const allowForever = useAccounts().data?.find((a) => a.id === accountId)?.permanent_delete ?? false;
  const [text, setText] = useState<Text>({
    senders_in: (c0.senders_in ?? []).join(", "),
    senders_not_in: (c0.senders_not_in ?? []).join(", "),
    subject_contains: (c0.subject_contains ?? []).join(", "),
  });
  const [flags, setFlags] = useState<Partial<Record<Flag, boolean>>>({
    list_unsubscribe: c0.list_unsubscribe,
    calendar_invite: c0.calendar_invite,
    has_attachments: c0.has_attachments,
  });
  const [addressed, setAddressed] = useState<string>(c0.addressed ?? "");
  const [combine, setCombine] = useState<Combine>(initial.combine);
  // A Delete forever rule is never Automatic: an old one saved that way opens as Suggest first.
  const [mode, setMode] = useState<RuleFields["mode"]>(forever ? "suggest" : (initial.mode ?? "suggest"));
  const conditions = conditionsFrom(text, flags, addressed);
  const anyCondition = Object.keys(conditions).length > 0;
  const [showConditions, setShowConditions] = useState(Object.keys(c0).length > 0);
  const save = useSaveRule();
  // The first fixed condition on an "Only the AI" rule would do nothing, so it starts narrowing
  // instead; the owner can still pick another way.
  const setConditions = (t: Text, f: Partial<Record<Flag, boolean>>, a: string) => {
    if (!anyCondition && combine === "ai_only" && Object.keys(conditionsFrom(t, f, a)).length > 0) setCombine("fixed_then_ai");
    setText(t);
    setFlags(f);
    setAddressed(a);
  };

  // Offload stands alone: ticking it clears the others, and ticking another clears it.
  const pickOffload = (on: boolean) => {
    setOff(on);
    if (on) {
      setSimple(new Set());
      setLabelOn(false);
      setForever(false);
    }
  };
  // Delete forever stands alone too, and always suggests first (bulk offload spec §6).
  const pickForever = (on: boolean) => {
    setForever(on);
    if (on) {
      setSimple(new Set());
      setLabelOn(false);
      setOff(false);
      setMode("suggest");
    }
  };
  const pickSimple = (kind: Simple, on: boolean) => {
    const next = new Set(simple);
    if (on) next.add(kind);
    else next.delete(kind);
    setSimple(next);
    if (on) {
      setOff(false);
      setForever(false);
    }
  };
  const pickLabel = (on: boolean) => {
    setLabelOn(on);
    if (on) {
      setOff(false);
      setForever(false);
    }
  };

  const actions: RuleAction[] = forever
    ? [{ kind: "delete_forever" }]
    : off
      ? [{ kind: "offload" }]
      : [...SIMPLE.filter(([k]) => simple.has(k)).map(([kind]) => ({ kind })), ...(labelOn ? [{ kind: "label" as const, label: labelName.trim() }] : [])];
  const rule: RuleFields = {
    name: name.trim(),
    description: description.trim(),
    conditions,
    combine: anyCondition ? combine : "ai_only",
    actions,
    mode,
    enabled: initial.enabled ?? true,
  };
  const submit = () => save.mutate({ accountId, id: ruleId, rule }, { onSuccess: onSaved });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Field label="Name">
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Which mail is this for?">
        <textarea className={`${inputClass} h-auto min-h-[5rem] py-2`} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>

      <fieldset>
        <legend className="mb-1 text-table font-medium text-ink-2">What it does</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {/* Mail panel spec §8: Archive, Label, Mark read, Star, Offload, Delete forever. */}
          {[SIMPLE[0]].map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="checkbox" checked={simple.has(kind)} onChange={(e) => pickSimple(kind, e.target.checked)} />
              {label}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={labelOn} onChange={(e) => pickLabel(e.target.checked)} />
            Label
          </label>
          {SIMPLE.slice(1).map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="checkbox" checked={simple.has(kind)} onChange={(e) => pickSimple(kind, e.target.checked)} />
              {label}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={off} aria-describedby={`${id}-off`} onChange={(e) => pickOffload(e.target.checked)} />
            Offload
          </label>
          <label className={`flex items-center gap-2 ${allowForever || forever ? "" : "text-ink-2"}`}>
            <input
              type="checkbox"
              checked={forever}
              disabled={!allowForever && !forever}
              aria-describedby={`${id}-forever`}
              onChange={(e) => pickForever(e.target.checked)}
            />
            Delete forever
          </label>
        </div>
        {labelOn && (
          <div className="mt-2 max-w-xs">
            <Field label="Label name">
              <input className={inputClass} value={labelName} onChange={(e) => setLabelName(e.target.value)} />
            </Field>
          </div>
        )}
        <p id={`${id}-off`} className="mt-1 text-meta text-ink-2">
          Offload checks CleanSync's copy, moves the mail out of Gmail and keeps the copy in Offloaded. You can restore it later.
        </p>
        <p id={`${id}-forever`} className="mt-1 text-meta text-ink-2">
          {allowForever ? "Delete forever removes the mail from Gmail and CleanSync's copy. Nothing is left to restore." : FOREVER_OFF}
        </p>
      </fieldset>

      <div>
        <button
          type="button"
          aria-expanded={showConditions}
          aria-controls={`${id}-cond`}
          onClick={() => setShowConditions(!showConditions)}
          className="text-table font-semibold text-accent"
        >
          Fixed conditions
        </button>
        {showConditions && (
          <div id={`${id}-cond`} className="mt-2 grid gap-3 sm:grid-cols-2">
            {TEXTS.map(([key, label, hint]) => (
              <Field key={key} label={label} hint={hint}>
                <input className={inputClass} value={text[key]} onChange={(e) => setConditions({ ...text, [key]: e.target.value }, flags, addressed)} />
              </Field>
            ))}
            {FLAGS.map(([key, label]) => (
              <Field key={key} label={label}>
                <select
                  className={inputClass}
                  value={flags[key] === undefined ? "" : flags[key] ? "yes" : "no"}
                  onChange={(e) => setConditions(text, { ...flags, [key]: e.target.value === "" ? undefined : e.target.value === "yes" }, addressed)}
                >
                  <option value="">Doesn't matter</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </Field>
            ))}
            <Field label="Sent to me / CC / a list">
              <select className={inputClass} value={addressed} onChange={(e) => setConditions(text, flags, e.target.value)}>
                <option value="">Doesn't matter</option>
                <option value="direct">Sent to me</option>
                <option value="cc">CC</option>
                <option value="list">A list</option>
              </select>
            </Field>
          </div>
        )}
      </div>

      {anyCondition && (
        <Field label="How these work with the description">
          <select className={inputClass} value={combine} onChange={(e) => setCombine(e.target.value as Combine)}>
            {COMBINES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      )}

      <fieldset>
        <legend className="mb-1 text-table font-medium text-ink-2">Mode</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <label className="flex items-center gap-2">
            <input type="radio" name={`${id}-mode`} checked={mode === "suggest"} onChange={() => setMode("suggest")} />
            Suggest first (you approve)
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={`${id}-mode`}
              checked={mode === "automatic"}
              disabled={forever}
              aria-describedby={forever ? `${id}-suggest-only` : undefined}
              onChange={() => setMode("automatic")}
            />
            Automatic
          </label>
        </div>
        {forever && (
          <p id={`${id}-suggest-only`} className="mt-1 text-meta text-ink-2">
            {FOREVER_SUGGEST_ONLY}
          </p>
        )}
      </fieldset>

      {tryPanel?.(rule)}

      {save.isError && <Notice tone="err">{friendlyError(save.error)}</Notice>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={onCancel}>Cancel</Button>
        <Button type="submit" tone="primary" disabled={save.isPending}>
          Save rule
        </Button>
      </div>
    </form>
  );
}
