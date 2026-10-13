import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { useMailCounts } from "../../api/queries";
import type { Account, MailBrief, OffloadStart } from "../../api/types";
import { usePreviewOffload, useStartOffload } from "../../hooks/useMailOffload";
import { formatBytes, formatWhen } from "../../lib/format";
import { mailHref, quoted } from "../../lib/mail";
import { Button, Dialog, Field, inputClass, Notice } from "../ui";

export type FilterFields = { age: string; unit: "d" | "m" | "y"; from: string; to: string; label: string; larger: string; attachments: boolean };
export const EMPTY_FIELDS: FilterFields = { age: "", unit: "y", from: "", to: "", label: "", larger: "", attachments: false };

/** Spec §5.1: the builder writes the reader's search language (the filters combine with AND). */
export function buildQuery(f: FilterFields): string {
  const parts: string[] = [];
  if (f.age.trim()) parts.push(`older_than:${f.age.trim()}${f.unit}`);
  if (f.from.trim()) parts.push(`from:${quoted(f.from.trim())}`);
  if (f.to.trim()) parts.push(`to:${quoted(f.to.trim())}`);
  if (f.label) parts.push(`label:${quoted(f.label)}`);
  if (f.larger.trim()) parts.push(`larger:${f.larger.trim()}M`);
  if (f.attachments) parts.push("has:attachment");
  return parts.join(" ");
}

/** The typed confirmation: the number, plain or with thousands commas in their usual places. */
export function parseTyped(text: string): number | null {
  const t = text.trim();
  return /^\d{1,3}(,\d{3})*$|^\d+$/.test(t) ? Number(t.replace(/,/g, "")) : null;
}

/** The Filter box shows the hand-typed text (or the search it started from) AND the builder's
 * terms. The builder only ever adds to the text: it never drops a term, so it can't widen.
 * The text shows exactly as typed (spaces too); one space goes before the builder's terms
 * only when the text doesn't already end in one. Trim it before sending. */
export function combine(text: string, built: string): string {
  if (!built) return text;
  if (!text.trim()) return built;
  return /\s$/.test(text) ? `${text}${built}` : `${text} ${built}`;
}

const FIELD_NAMES: [keyof FilterFields, string, string][] = [
  ["age", "older_than", "Older than"],
  ["from", "from", "From"],
  ["to", "to", "To"],
  ["label", "label", "Label"],
  ["larger", "larger", "Larger than"],
  ["attachments", "has", "Has attachments"],
];

/** Builder fields whose search word is also typed in the text, like older_than: in both. */
export function twice(text: string, f: FilterFields): string[] {
  const typed = new Set(Array.from(text.matchAll(/(?:^|\s)([A-Za-z_]+):/g), (m) => m[1].toLowerCase()));
  return FIELD_NAMES.filter(([key, op]) => (typeof f[key] === "string" ? (f[key] as string).trim() !== "" : f[key]) && typed.has(op)).map(([, , name]) => name);
}

function Briefs({ title, name, items, show }: { title: string; name: string; items: MailBrief[]; show: (m: MailBrief) => string }) {
  if (items.length === 0) return null;
  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-table font-semibold">{title}</h3>
      <ul aria-label={name} className="space-y-0.5 text-table">
        {items.map((m) => (
          <li key={m.id} className="flex gap-2">
            <span className="min-w-0 flex-1 truncate">{m.subject || "(no subject)"}</span>
            <span className="shrink-0 text-ink-2 tabular">{show(m)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "Offload mail…" (spec §5.1–5.3): build or pick what goes, preview it, type the count, start. */
export function OffloadMailDialog({
  account,
  start,
  onClose,
  scope = null,
}: {
  account: Account;
  start: OffloadStart;
  onClose: () => void;
  /** The view or label the reader was showing, which a filter doesn't carry over. */
  scope?: string | null;
}) {
  const navigate = useNavigate();
  const fixed = !("q" in start);
  const labels = useMailCounts(account.id, !fixed).data?.labels ?? [];
  const [fields, setFields] = useState<FilterFields>(EMPTY_FIELDS);
  const [text, setText] = useState("q" in start ? start.q : "");
  const [typed, setTyped] = useState("");
  const preview = usePreviewOffload();
  const run = useStartOffload();
  // Any change to what goes asks for a fresh preview and a freshly typed count.
  const forget = () => {
    preview.reset();
    run.reset();
    setTyped("");
  };
  const set = (patch: Partial<FilterFields>) => {
    const next = { ...fields, ...patch };
    setFields(next);
    forget();
  };
  const built = buildQuery(fields);
  const shown = combine(text, built);
  const q = shown.trim();
  const doubled = twice(text, fields);
  // A hand edit of the Filter box: when the builder's terms are still at its end, the rest is
  // the text; otherwise the whole box becomes the text and the builder starts empty again.
  // Either way every term typed stays in the filter.
  const editFilter = (value: string) => {
    if (built && (value === built || value.endsWith(` ${built}`))) {
      setText(value.slice(0, value.length - built.length));
    } else {
      setText(value);
      setFields(EMPTY_FIELDS);
    }
    forget();
  };
  const what: OffloadStart = fixed ? start : { q };
  const p = preview.data;
  const confirmed = p !== undefined && p.count > 0 && parseTyped(typed) === p.count;
  const doPreview = () => {
    run.reset();
    preview.mutate({ accountId: account.id, start: what });
  };
  const doStart = (count: number) =>
    run.mutate(
      { accountId: account.id, start: what, confirm: count },
      {
        onSuccess: ({ offload_id }) => {
          onClose();
          navigate(`/mail/offloads/${offload_id}`);
        },
        // Refused (the count changed, a backup is checking the mailbox…): nothing started.
        // The message stays; Preview comes back to look again.
        onError: () => {
          preview.reset();
          setTyped("");
        },
      },
    );
  return (
    <Dialog
      title="Offload mail"
      wide
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          {!p && (
            <Button tone="main" data-autofocus disabled={preview.isPending || (!fixed && !q.trim())} onClick={doPreview}>
              Preview
            </Button>
          )}
          {p && p.count > 0 && (
            <Button tone="danger" disabled={!confirmed || run.isPending} onClick={() => doStart(p.count)}>
              Start offload
            </Button>
          )}
        </>
      }
    >
      <p className="text-ink-2">
        Offloaded mail moves to Gmail's trash and stays readable here. Gmail empties its trash on its own after 30 days; that's when the space comes back. You can
        restore it to Gmail at any time.
      </p>
      {fixed ? (
        <p>{"thread_ids" in start ? "The picked threads." : "The picked mails."}</p>
      ) : (
        // Locked while a start is on its way: an edit then would lose where it goes next.
        <fieldset disabled={run.isPending} className="grid gap-3 sm:grid-cols-2">
          <legend className="sr-only">What to offload</legend>
          <div className="flex items-end gap-2">
            <Field label="Older than">
              <input className={`${inputClass} w-24`} inputMode="numeric" value={fields.age} onChange={(e) => set({ age: e.target.value })} />
            </Field>
            <Field label="Unit">
              <select className={`${inputClass} w-28`} value={fields.unit} onChange={(e) => set({ unit: e.target.value as FilterFields["unit"] })}>
                <option value="d">days</option>
                <option value="m">months</option>
                <option value="y">years</option>
              </select>
            </Field>
          </div>
          <Field label="Larger than (MB)">
            <input className={`${inputClass} w-28`} inputMode="numeric" value={fields.larger} onChange={(e) => set({ larger: e.target.value })} />
          </Field>
          <Field label="From">
            <input className={inputClass} value={fields.from} onChange={(e) => set({ from: e.target.value })} />
          </Field>
          <Field label="To">
            <input className={inputClass} value={fields.to} onChange={(e) => set({ to: e.target.value })} />
          </Field>
          <Field label="Label">
            <select className={inputClass} value={fields.label} onChange={(e) => set({ label: e.target.value })}>
              <option value="">Any</option>
              {labels.map((l) => (
                <option key={l.id} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 self-end text-body">
            <input type="checkbox" checked={fields.attachments} onChange={(e) => set({ attachments: e.target.checked })} />
            Has attachments
          </label>
          <div className="sm:col-span-2">
            <Field label="Filter" hint="The same search language as the search box, like older_than:1y larger:5M.">
              <input
                className={inputClass}
                value={shown}
                onChange={(e) => editFilter(e.target.value)}
              />
            </Field>
            {doubled.length > 0 && (
              <p className="mt-1 text-meta text-ink-2">
                {doubled.join(", ")} {doubled.length === 1 ? "appears" : "appear"} twice; both apply.
              </p>
            )}
          </div>
        </fieldset>
      )}
      {!fixed && scope && (
        <p className="text-table text-ink-2">
          The filter looks at all your mail, not only {scope}. Add a filter like a label to narrow it.
        </p>
      )}
      {preview.isError && <Notice tone="err">{friendlyError(preview.error)}</Notice>}
      {run.isError && <Notice tone="err">{friendlyError(run.error)}</Notice>}
      {p && (
        <div className="space-y-3 rounded-card border border-line p-3">
          <p className="font-semibold">
            {p.count.toLocaleString()} {p.count === 1 ? "mail" : "mails"}, {formatBytes(p.bytes)}. The space is freed in Gmail once Gmail's trash is emptied.
          </p>
          {p.excluded.length > 0 && (
            <ul aria-label="Left out" className="space-y-0.5 text-table text-ink-2">
              {p.excluded.map((x) => (
                <li key={x.reason}>{x.text}</li>
              ))}
            </ul>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <Briefs title="10 largest" name="Largest" items={p.largest} show={(m) => formatBytes(m.size)} />
            <Briefs title="10 oldest" name="Oldest" items={p.oldest} show={(m) => formatWhen(m.date)} />
          </div>
          {!fixed && (
            <Link to={mailHref({ q })} onClick={onClose} className="inline-block text-table font-semibold text-accent">
              See them all in the mail list
            </Link>
          )}
          {p.count > 0 && (
            <Field label={`To start, type ${p.count.toLocaleString()}`}>
              <input className={`${inputClass} w-40`} inputMode="numeric" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          )}
        </div>
      )}
    </Dialog>
  );
}
