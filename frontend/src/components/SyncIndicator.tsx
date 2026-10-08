import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { usePairs } from "../api/queries";
import type { Pair } from "../api/types";
import { useDismiss } from "../hooks/useDismiss";
import { useLiveState, useProgress } from "../hooks/live";
import { formatBytes, formatSpeed, percent } from "../lib/format";
import { Icon } from "./Icon";
import { Bar } from "./ui";

function summary(pairs: Pair[], transferring: number) {
  const running = pairs.filter((p) => p.status === "RUNNING");
  const errors = pairs.filter((p) => p.status === "ERROR");
  const attention = pairs.filter((p) => p.status === "ATTENTION" || p.status === "NEW");
  if (running.length) {
    const text = transferring ? `Syncing ${transferring} ${transferring === 1 ? "file" : "files"}` : "Syncing";
    return { text, tone: "text-run bg-run-soft", icon: "sync" as const, spin: true };
  }
  if (errors.length) return { text: `${errors.length} with errors`, tone: "text-err bg-err-soft", icon: "sync_problem" as const };
  if (attention.length) {
    return { text: `${attention.length} need${attention.length === 1 ? "s" : ""} you`, tone: "text-warn bg-warn-soft", icon: "warning" as const };
  }
  if (pairs.length && pairs.every((p) => p.paused)) return { text: "Paused", tone: "text-paused bg-sunken", icon: "pause" as const };
  return { text: pairs.length ? "Up to date" : "Nothing synced yet", tone: "text-ok bg-sunken", icon: "cloud_done" as const };
}

/**
 * The live heartbeat in the top bar: one pill that says what syncing is doing now, a thin
 * progress line along the top edge while bytes move, and a panel with per-file progress.
 */
export function SyncIndicator() {
  const { data: pairs = [] } = usePairs();
  const progress = useProgress();
  const live = useLiveState();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useDismiss(box, () => setOpen(false), open, trigger);
  const running = pairs.filter((p) => p.status === "RUNNING");
  const transfers = running.flatMap((p) => progress[p.id]?.transferring ?? []);
  const s = summary(pairs, transfers.length);
  const moved = running.reduce((n, p) => n + (progress[p.id]?.bytes ?? 0), 0);
  const total = running.reduce((n, p) => n + (progress[p.id]?.total_bytes ?? 0), 0);
  return (
    <div className="relative" ref={box}>
      {running.length > 0 && total > 0 && (
        <div className="fixed inset-x-0 top-0 z-40 h-[3px] bg-run/15" aria-hidden="true">
          <div className="h-full bg-run" style={{ width: `${percent(moved, total)}%` }} />
        </div>
      )}
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-label={`Sync status: ${s.text}${live === "retrying" ? ", live updates paused, reconnecting" : ""}`}
        className={`flex h-9 items-center gap-2 rounded-full px-3.5 text-table font-semibold ${s.tone}`}
        onClick={() => setOpen(!open)}
      >
        <Icon name={s.icon} className={s.spin ? "spin" : ""} />
        <span className="max-sm:hidden">{s.text}</span>
        {live === "retrying" && <span className="h-1.5 w-1.5 rounded-full bg-warn" title="Live updates paused; reconnecting" />}
      </button>
      {open && (
        <div role="region" aria-label="Sync progress" className="absolute right-0 top-11 z-50 w-[min(380px,calc(100vw-2rem))] rounded-card border border-line bg-surface p-4 shadow-float">
          {live === "retrying" && <p role="status" className="mb-3 text-table text-warn">Live updates paused. Reconnecting.</p>}
          {running.length === 0 ? (
            <p className="text-ink-2">{s.text}.</p>
          ) : (
            running.map((p) => {
              const pr = progress[p.id];
              return (
                <div key={p.id} className="mb-3 last:mb-0">
                  <div className="mb-1 flex justify-between text-table">
                    <span className="font-semibold">{p.name}</span>
                    {pr && <span className="tabular text-ink-2">{formatSpeed(pr.speed)}</span>}
                  </div>
                  {pr ? (
                    <>
                      <Bar tone="run" label={`${p.name} progress`} value={percent(pr.bytes, pr.total_bytes)} />
                      <p className="mt-1 text-meta tabular text-ink-2">
                        {formatBytes(pr.bytes)} of {formatBytes(pr.total_bytes)}
                      </p>
                      <ul className="mt-2 space-y-1.5">
                        {pr.transferring.map((t) => (
                          <li key={t.name} className="text-meta">
                            <div className="flex justify-between gap-3">
                              <span className="truncate">{t.name}</span>
                              <span className="tabular text-ink-2">{t.percentage ?? 0}%</span>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <p className="text-meta text-ink-2">Comparing both sides</p>
                  )}
                </div>
              );
            })
          )}
          <Link to="/activity" className="mt-3 inline-block text-table font-semibold text-accent" onClick={() => setOpen(false)}>
            Open sync activity
          </Link>
        </div>
      )}
    </div>
  );
}
