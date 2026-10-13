import type { AiPause } from "../api/rules";
import { formatWhen } from "./format";

const lower = (s: string) => (s ? s[0].toLowerCase() + s.slice(1) : s);

/** One plain line about a paused model connection (model limits spec §9). */
export function pauseLine(p: AiPause, now = new Date()): string {
  const who = p.connection_name || "The model";
  if (p.kind === "needs_you") return `${who} stopped: ${lower(p.reason)} Check Settings → Models.`;
  const when = formatWhen(p.until, now);
  if (p.kind === "down") return `${who} isn't answering: ${lower(p.reason)} CleanSync tries again at ${when}.`;
  return `${who} is paused until ${when}: ${lower(p.reason)}`;
}
