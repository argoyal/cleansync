import { useLocalFolders, useRemoteFolders } from "../api/queries";
import { friendlyError } from "../api/client";
import { Icon } from "./Icon";

/** Browse folders one level at a time and pick one: on this server or on Google Drive. */
export function FolderPicker({
  source,
  accountId,
  value,
  onChange,
  rootLabel,
}: {
  source: "local" | "remote";
  accountId: number;
  value: string;
  onChange: (path: string) => void;
  rootLabel: string;
}) {
  const local = useLocalFolders(accountId, value, source === "local");
  const remote = useRemoteFolders(accountId, value, source === "remote");
  const q = source === "local" ? local : remote;
  const crumbs = value ? value.split("/") : [];
  return (
    <div className="rounded-card border border-line">
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-3 py-2 text-table">
        <button type="button" className="font-semibold text-accent" onClick={() => onChange("")}>
          {rootLabel}
        </button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <Icon name="chevron_right" className="text-ink-2" />
            <button type="button" className="text-accent" onClick={() => onChange(crumbs.slice(0, i + 1).join("/"))}>
              {c}
            </button>
          </span>
        ))}
      </div>
      <ul className="max-h-56 overflow-y-auto py-1" aria-label={`Folders in ${value || rootLabel}`}>
        {q.isPending && <li className="px-3 py-2 text-ink-2">Loading folders</li>}
        {q.isError && <li className="px-3 py-2 text-err">{friendlyError(q.error)}</li>}
        {q.data?.length === 0 && <li className="px-3 py-2 text-ink-2">No folders inside. This one is selected.</li>}
        {q.data?.map((f) => (
          <li key={f.path}>
            <button
              type="button"
              className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left hover:bg-sunken"
              onClick={() => onChange(f.path)}
            >
              <Icon name="folder" className="text-ink-2" />
              {f.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
