// frontend/src/lib/mailSearch.ts
import { quoted } from "./mail";

/**
 * Mail panel spec §5: the search form writes the search box's keywords, and reads them back.
 * The backend parser (search.py) stays the only real one: this knows just the keywords the
 * form has fields for. Anything else stays in "Has the words", as typed.
 */

export type SizeUnit = "KB" | "MB";
export type AgeUnit = "d" | "m" | "y";
export type DateKind = "any" | "older" | "newer" | "before" | "after";
export type SearchFields = {
  from: string;
  to: string;
  words: string;
  label: string;
  sizeOp: "larger" | "smaller";
  size: string;
  sizeUnit: SizeUnit;
  date: DateKind;
  age: string;
  ageUnit: AgeUnit;
  day: string;
  attachment: boolean;
  unread: boolean;
  starred: boolean;
};

export const EMPTY_SEARCH: SearchFields = {
  from: "",
  to: "",
  words: "",
  label: "",
  sizeOp: "larger",
  size: "",
  sizeUnit: "MB",
  date: "any",
  age: "",
  ageUnit: "y",
  day: "",
  attachment: false,
  unread: false,
  starred: false,
};

export const isNumber = (v: string) => /^\d+(\.\d+)?$/.test(v.trim());
export const isWhole = (v: string) => /^\d+$/.test(v.trim());

/** The order of the form: from, to, the words, label, size, date, then the ticks. */
export function writeSearch(f: SearchFields): string {
  const parts: string[] = [];
  const add = (key: string, v: string) => {
    // The server has no way to escape a quote mark, so a typed one is dropped.
    const t = v.replace(/"/g, "").trim();
    if (t) parts.push(`${key}:${quoted(t)}`);
  };
  add("from", f.from);
  add("to", f.to);
  if (f.words.trim()) parts.push(f.words.trim());
  add("label", f.label);
  if (f.size.trim()) parts.push(`${f.sizeOp}:${f.size.trim()}${f.sizeUnit === "MB" ? "M" : "K"}`);
  if ((f.date === "older" || f.date === "newer") && f.age.trim()) parts.push(`${f.date === "older" ? "older_than" : "newer_than"}:${f.age.trim()}${f.ageUnit}`);
  if ((f.date === "before" || f.date === "after") && f.day) parts.push(`${f.date}:${f.day}`);
  if (f.attachment) parts.push("has:attachment");
  if (f.unread) parts.push("is:unread");
  if (f.starred) parts.push("is:starred");
  return parts.join(" ");
}

// The same tokens as the backend: an optional "key:", then a quoted value or a plain word.
const TOKEN = /(?:[A-Za-z_]+:)?"[^"]*"?|\S+/g;
const SIZE = /^(\d+(?:\.\d+)?)([km])b?$/i;
const AGE = /^(\d+)([dmy])$/i;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function unquote(v: string): string {
  let s = v;
  if (s.startsWith('"')) {
    s = s.slice(1);
    if (s.endsWith('"')) s = s.slice(0, -1);
  }
  return s.trim();
}

/** `labelNames`: the mailbox's labels. A label: that isn't one of them stays in the words. */
export function readSearch(text: string, labelNames: string[] = []): SearchFields {
  const f: SearchFields = { ...EMPTY_SEARCH };
  const rest: string[] = [];
  let sized = false;
  let dated = false;
  const tokens = text.match(TOKEN) ?? [];
  // The server keeps the last size keyword of a kind, but the form writes its size after the
  // words. So when a kind appears twice, all of them stay in the words, in their order.
  const sizeCount = (k: string) => tokens.filter((t) => t.toLowerCase().startsWith(`${k}:`)).length;
  const twice = sizeCount("larger") > 1 || sizeCount("smaller") > 1;
  for (const tok of tokens) {
    const at = tok.indexOf(":");
    const key = at > 0 && !tok.startsWith('"') ? tok.slice(0, at).toLowerCase() : "";
    const value = key ? unquote(tok.slice(at + 1)) : "";
    let used = false;
    if (key === "from" && value && !f.from) {
      f.from = value;
      used = true;
    } else if (key === "to" && value && !f.to) {
      f.to = value;
      used = true;
    } else if (key === "label" && value && !f.label) {
      const name = labelNames.find((n) => n.toLowerCase() === value.toLowerCase());
      if (name) {
        f.label = name;
        used = true;
      }
    } else if ((key === "larger" || key === "smaller") && !sized && !twice) {
      const m = SIZE.exec(value);
      if (m) {
        f.sizeOp = key;
        f.size = m[1];
        f.sizeUnit = m[2].toLowerCase() === "m" ? "MB" : "KB";
        sized = used = true;
      }
    } else if ((key === "older_than" || key === "newer_than") && !dated) {
      const m = AGE.exec(value);
      if (m) {
        f.date = key === "older_than" ? "older" : "newer";
        f.age = m[1];
        f.ageUnit = m[2].toLowerCase() as AgeUnit;
        dated = used = true;
      }
    } else if ((key === "before" || key === "after") && !dated && DAY.test(value)) {
      f.date = key;
      f.day = value;
      dated = used = true;
    } else if (key === "has" && /^attachments?$/i.test(value)) {
      f.attachment = used = true;
    } else if (key === "is" && value.toLowerCase() === "unread") {
      f.unread = used = true;
    } else if (key === "is" && value.toLowerCase() === "starred") {
      f.starred = used = true;
    }
    if (!used) rest.push(tok);
  }
  f.words = rest.join(" ");
  return f;
}
