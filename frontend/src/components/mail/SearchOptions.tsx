// frontend/src/components/mail/SearchOptions.tsx
import { type KeyboardEvent, useState } from "react";

import { type AgeUnit, type DateKind, EMPTY_SEARCH, isNumber, isWhole, readSearch, type SearchFields, type SizeUnit, writeSearch } from "../../lib/mailSearch";
import { Button, Field, inputClass } from "../ui";

const DATES: [DateKind, string][] = [
  ["any", "Any time"],
  ["older", "Older than"],
  ["newer", "Newer than"],
  ["before", "Before a date"],
  ["after", "After a date"],
];

/** Mail panel spec §5: the form below the search box. It opens with the box read into its
 * fields; Search writes the box and runs it; Clear empties the form; closing changes nothing.
 * In Deleted in Gmail labels aren't reliable, so there is no Label field there. */
export function SearchOptions({
  text,
  view,
  labels,
  onSearch,
  onClose,
}: {
  text: string;
  view: string;
  labels: string[];
  onSearch: (q: string) => void;
  onClose: () => void;
}) {
  const showLabel = view !== "deleted";
  const names = showLabel ? labels : [];
  const [f, setF] = useState<SearchFields>(() => readSearch(text, names));
  // The labels may arrive after the form opened: read the box again once they do.
  const [readWith, setReadWith] = useState(names.join("\n"));
  const aged = f.date === "older" || f.date === "newer";
  const badSize = f.size.trim() !== "" && !isNumber(f.size);
  const badAge = aged && f.age.trim() !== "" && !isWhole(f.age);
  if (readWith !== names.join("\n")) {
    setReadWith(names.join("\n"));
    // A size or age that isn't a number can't be written as a keyword, so it would drop into the
    // words: leave it out of the re-read and put it back in its own field.
    const next = readSearch(writeSearch({ ...f, size: badSize ? "" : f.size, age: badAge ? "" : f.age }), names);
    if (badSize) Object.assign(next, { size: f.size, sizeOp: f.sizeOp, sizeUnit: f.sizeUnit });
    if (badAge) Object.assign(next, { date: f.date, age: f.age, ageUnit: f.ageUnit });
    setF(next);
  }
  const set = (patch: Partial<SearchFields>) => setF({ ...f, ...patch });
  // The server reads an open quote to the end, so keywords written after the words would be swallowed.
  const openQuote = (f.words.match(/"/g) ?? []).length % 2 === 1;
  const escape = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  };
  return (
    <section aria-label="Search options" onKeyDown={escape} className="absolute left-0 right-0 top-full z-30 mt-1 rounded-card border border-line bg-surface p-4 shadow-lg">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!badSize && !badAge) onSearch(writeSearch(f));
        }}
      >
        <Field label="From">
          <input className={inputClass} value={f.from} onChange={(e) => set({ from: e.target.value })} />
        </Field>
        <Field label="To">
          <input className={inputClass} value={f.to} onChange={(e) => set({ to: e.target.value })} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Has the words">
            <input className={inputClass} value={f.words} onChange={(e) => set({ words: e.target.value })} />
          </Field>
          {openQuote && <p className="mt-1 text-meta text-ink-2">The quote mark isn't closed, so keywords after it are read as words.</p>}
        </div>
        {showLabel && (
          <Field label="Label">
            <select className={inputClass} value={f.label} onChange={(e) => set({ label: e.target.value })}>
              <option value="">Any</option>
              {labels.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </Field>
        )}
        <fieldset>
          <legend className="mb-1 text-table font-medium text-ink-2">Size</legend>
          <div className="flex gap-2">
            <select aria-label="Larger or smaller" className={`${inputClass} w-32`} value={f.sizeOp} onChange={(e) => set({ sizeOp: e.target.value as SearchFields["sizeOp"] })}>
              <option value="larger">Larger than</option>
              <option value="smaller">Smaller than</option>
            </select>
            <input aria-label="Size" inputMode="decimal" className={`${inputClass} w-20`} value={f.size} onChange={(e) => set({ size: e.target.value })} />
            <select aria-label="Size unit" className={`${inputClass} w-20`} value={f.sizeUnit} onChange={(e) => set({ sizeUnit: e.target.value as SizeUnit })}>
              <option value="KB">KB</option>
              <option value="MB">MB</option>
            </select>
          </div>
          {badSize && <p className="mt-1 text-meta text-err">Use a number.</p>}
        </fieldset>
        <fieldset>
          <legend className="mb-1 text-table font-medium text-ink-2">Date</legend>
          <div className="flex flex-wrap gap-2">
            <select aria-label="Date" className={`${inputClass} w-40`} value={f.date} onChange={(e) => set({ date: e.target.value as DateKind })}>
              {DATES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            {aged && (
              <>
                <input aria-label="Age" inputMode="numeric" className={`${inputClass} w-20`} value={f.age} onChange={(e) => set({ age: e.target.value })} />
                <select aria-label="Age unit" className={`${inputClass} w-28`} value={f.ageUnit} onChange={(e) => set({ ageUnit: e.target.value as AgeUnit })}>
                  <option value="d">days</option>
                  <option value="m">months</option>
                  <option value="y">years</option>
                </select>
              </>
            )}
            {(f.date === "before" || f.date === "after") && (
              <input aria-label="Day" type="date" className={`${inputClass} w-44`} value={f.day} onChange={(e) => set({ day: e.target.value })} />
            )}
          </div>
          {badAge && <p className="mt-1 text-meta text-err">Use a number.</p>}
        </fieldset>
        <div className="flex flex-wrap gap-x-5 gap-y-2 sm:col-span-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.attachment} onChange={(e) => set({ attachment: e.target.checked })} />
            Has attachment
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.unread} onChange={(e) => set({ unread: e.target.checked })} />
            Unread
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={f.starred} onChange={(e) => set({ starred: e.target.checked })} />
            Starred
          </label>
        </div>
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button onClick={() => setF(EMPTY_SEARCH)}>Clear</Button>
          <Button type="submit" tone="primary" disabled={badSize || badAge}>
            Search
          </Button>
        </div>
      </form>
    </section>
  );
}
