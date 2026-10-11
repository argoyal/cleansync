import { useStatus } from "../api/queries";
import type { SectionId } from "../lib/sections";

export type Badges = Partial<Record<SectionId, number>>;

/** Counts shown on the rail: open conflicts on Files (Plan 2 adds failed mails on Mail). */
export function useRailBadges(): Badges {
  const status = useStatus().data;
  return { files: status?.conflicts ?? 0 };
}
