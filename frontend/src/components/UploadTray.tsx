import { useEffect } from "react";

import { uploads, useUploads } from "../hooks/uploads";
import { formatBytes, percent } from "../lib/format";
import { useInvalidateFiles } from "./FileActions";
import { Icon } from "./Icon";
import { Bar, IconButton } from "./ui";

const STATE_TEXT = { waiting: "Waiting", sending: "Uploading", done: "Uploaded", failed: "Failed", cancelled: "Cancelled" };

/** Bottom-right panel with every upload of this session. */
export function UploadTray() {
  const list = useUploads();
  const invalidate = useInvalidateFiles();
  useEffect(() => uploads.onFinished(invalidate), [invalidate]);
  if (list.length === 0) return null;
  const active = list.filter((u) => u.state === "waiting" || u.state === "sending").length;
  return (
    <section
      aria-label="Uploads"
      className="fixed bottom-4 right-4 z-40 w-[min(360px,calc(100vw-2rem))] rounded-card border border-line bg-surface shadow-float"
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <h2 className="flex-1 font-semibold">{active ? `Uploading ${active} ${active === 1 ? "item" : "items"}` : "Uploads done"}</h2>
        {!active && <IconButton icon="close" label="Close uploads" onClick={() => uploads.clearFinished()} />}
      </header>
      <ul className="max-h-64 overflow-y-auto px-4 py-2">
        {list.map((u) => (
          <li key={u.id} className="py-2">
            <div className="flex items-center gap-2 text-table">
              <Icon name="upload_file" className="text-ink-2" />
              <span className="min-w-0 flex-1 truncate">{u.name}</span>
              {(u.state === "waiting" || u.state === "sending") && (
                <button type="button" className="text-meta font-semibold text-accent" onClick={() => uploads.cancel(u.id)}>
                  Cancel
                </button>
              )}
            </div>
            {u.state === "sending" && <Bar tone="run" label={`${u.name} upload`} value={percent(u.sent, u.size)} />}
            <p className={`mt-0.5 text-meta ${u.state === "failed" ? "text-err" : "text-ink-2"}`}>
              {u.state === "failed"
                ? u.error
                : u.savedAs
                  ? `Saved as ${u.savedAs}, a file with that name was already there`
                  : `${STATE_TEXT[u.state]}, ${formatBytes(u.size)}`}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
