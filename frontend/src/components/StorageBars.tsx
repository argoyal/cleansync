import { Link } from "react-router-dom";

import { useStorage } from "../api/queries";
import { formatBytes, percent } from "../lib/format";
import { Bar } from "./ui";

/** Drive quota (trash counts against it) and the server disk, for one account. */
export function StorageBars({ accountId, compact = false }: { accountId: number | null; compact?: boolean }) {
  const { data } = useStorage(accountId);
  if (!data) return null;
  const { drive, disk } = data;
  const used = drive.used ?? 0;
  const driveTone = percent(used, drive.total) > 90 ? "err" : percent(used, drive.total) > 75 ? "warn" : "accent";
  return (
    <div className={compact ? "space-y-3 px-3" : "space-y-5"}>
      <div>
        <Bar label="Google Drive storage used" value={percent(used, drive.total)} tone={driveTone} />
        <p className="mt-1.5 text-meta tabular text-ink-2">
          {drive.total
            ? `${formatBytes(used)} of ${formatBytes(drive.total)} on Google Drive`
            : `${formatBytes(used)} on Google Drive`}
          {drive.trash ? `, ${formatBytes(drive.trash)} in trash` : ""}
        </p>
      </div>
      <div>
        <Bar label="Server disk used" value={percent(disk.used, disk.total)} tone={percent(disk.used, disk.total) > 90 ? "err" : "accent"} />
        <p className="mt-1.5 text-meta tabular text-ink-2">
          {formatBytes(disk.free)} free on this server
          {data.synced_bytes !== null ? `, ${formatBytes(data.synced_bytes)} synced here` : ""}
        </p>
      </div>
      {compact && (
        <Link to="/storage" className="inline-block text-meta font-semibold text-accent">
          Storage details
        </Link>
      )}
    </div>
  );
}
