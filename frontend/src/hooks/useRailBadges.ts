import { useMailStatus, useStatus } from "../api/queries";
import type { SectionId } from "../lib/sections";
import { useCurrentAccount } from "./useAccount";

export type Badges = Partial<Record<SectionId, number>>;

/** Counts shown on the rail: open conflicts on Files, mails that failed 3 times on Mail. */
export function useRailBadges(): Badges {
  const status = useStatus().data;
  const { account } = useCurrentAccount();
  const mailOn = account?.services.mail === "active";
  const mail = useMailStatus(account?.id ?? null, mailOn).data;
  return { files: status?.conflicts ?? 0, mail: mail?.failed?.length ?? 0 };
}
