const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  let v = n;
  let u = 0;
  while (v >= 1024 && u < UNITS.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${u === 0 ? v : v.toFixed(v < 10 ? 1 : 0)} ${UNITS[u]}`;
}

export function formatSpeed(bytesPerSecond: number): string {
  return `${formatBytes(Math.round(bytesPerSecond))}/s`;
}

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

/** "14:05" today, "3 Oct 2026" otherwise. */
export function formatWhen(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toDateString() === now.toDateString() ? timeFmt.format(d) : dateFmt.format(d);
}

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(s);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return rtf.format(s, "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

export function formatInterval(seconds: number | null): string {
  if (seconds === null) return "Manual only";
  return seconds < 3600 ? `Every ${seconds / 60} min` : "Every hour";
}

export function percent(part: number | null | undefined, whole: number | null | undefined): number {
  if (!part || !whole) return 0;
  return Math.min(100, Math.max(0, (part / whole) * 100));
}

/** Roughly how long is left: "less than a minute", "about 12 minutes", "about 3 hours". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const m = Math.round(seconds / 60);
  if (m < 60) return `about ${m} ${m === 1 ? "minute" : "minutes"}`;
  const h = Math.round(seconds / 3600);
  if (h < 48) return `about ${h} ${h === 1 ? "hour" : "hours"}`;
  return `about ${Math.round(seconds / 86400)} days`;
}
