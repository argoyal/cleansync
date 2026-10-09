import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";

import type { Progress } from "../api/types";

/** A tiny store any component can read with a hook; written only by the event stream. */
function createStore<T>(initial: T) {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next: T) {
      value = next;
      listeners.forEach((l) => l());
    },
    subscribe(l: () => void) {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
  };
}

export const progressStore = createStore<Record<number, Progress>>({});
export type LiveState = "connecting" | "open" | "retrying";
export const liveStore = createStore<LiveState>("connecting");

export const useProgress = () => useSyncExternalStore(progressStore.subscribe, progressStore.get);
export const useLiveState = () => useSyncExternalStore(liveStore.subscribe, liveStore.get);

function clearProgress(pairId: number) {
  const current = progressStore.get();
  if (pairId in current) {
    const next = { ...current };
    delete next[pairId];
    progressStore.set(next);
  }
}

const FILE_KEYS = ["browse", "recent", "starred", "search", "storage", "catalog"];
/** Each Recovery refetch reads the whole backups folder: at most one every 5 seconds. */
export const RECOVERY_REFRESH_MS = 5000;

/**
 * Keeps the UI live from /api/events (server-sent events). On a dropped connection it
 * retries with growing pauses (1 s up to 30 s); after reconnecting it refetches everything,
 * because events sent while it was away are lost.
 */
export function useLiveEvents(): void {
  const qc = useQueryClient();
  useEffect(() => {
    let source: EventSource | null = null;
    let timer: number | undefined;
    let retry = 0;
    let stopped = false;
    let hadError = false;
    const invalidate = (keys: string[]) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    // Deletes, syncs and offloads put copies in Recovery. The first refresh is immediate; any
    // more within the next 5 s become one catch-up refresh at the end of that time.
    let recoveryAt = -Infinity;
    let recoveryTimer: number | undefined;
    const refreshRecovery = () => {
      if (recoveryTimer !== undefined) return;
      const wait = recoveryAt + RECOVERY_REFRESH_MS - Date.now();
      const run = () => {
        recoveryTimer = undefined;
        recoveryAt = Date.now();
        invalidate(["recovery"]);
      };
      if (wait <= 0) run();
      else recoveryTimer = window.setTimeout(run, wait);
    };
    const data = (e: Event): unknown => {
      try {
        return JSON.parse((e as MessageEvent).data as string);
      } catch {
        return null; // a garbled message is skipped, not fatal
      }
    };

    const connect = () => {
      if (!hadError) liveStore.set("connecting");
      source = new EventSource("/api/events");
      source.onopen = () => {
        retry = 0;
        liveStore.set("open");
        if (hadError) qc.invalidateQueries(); // events sent while away are lost
        hadError = false;
      };
      source.onerror = () => {
        source?.close();
        if (stopped) return;
        hadError = true;
        liveStore.set("retrying");
        // A lost session ends the retrying: the answer is a 401, which sends the person to login.
        qc.invalidateQueries({ queryKey: ["me"] });
        timer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry));
        retry += 1;
      };
      source.addEventListener("job", (e) => {
        const d = data(e) as { pair_id: number; state: string } | null;
        if (!d) return;
        if (d.state !== "RUNNING") clearProgress(d.pair_id);
        invalidate(["pairs", "status", "history", "logs", "activity", "preview", "planned", "conflicts", ...FILE_KEYS]);
        refreshRecovery();
      });
      source.addEventListener("pair", () => invalidate(["pairs", "status", "browse"]));
      source.addEventListener("progress", (e) => {
        const p = data(e) as Progress | null;
        if (!p) return;
        progressStore.set({ ...progressStore.get(), [p.pair_id]: p });
      });
      source.addEventListener("activity", () => invalidate(["activity"]));
      source.addEventListener("account", () => invalidate(["accounts", "status", "setup", "pairs"]));
      source.addEventListener("files", () => {
        invalidate(FILE_KEYS);
        refreshRecovery();
      });
      source.addEventListener("catalog", () => invalidate(FILE_KEYS));
      // pair_id may be null (a whole batch finished): refresh by key, never by pair.
      source.addEventListener("offload", () => {
        invalidate(["offloads", "offload-batch", "status", ...FILE_KEYS]);
        refreshRecovery();
      });
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(recoveryTimer);
      source?.close();
      liveStore.set("connecting");
      progressStore.set({});
    };
  }, [qc]);
}
