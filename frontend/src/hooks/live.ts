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
    let connectedBefore = false;
    const invalidate = (keys: string[]) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    const data = (e: Event) => JSON.parse((e as MessageEvent).data as string);

    const connect = () => {
      liveStore.set(connectedBefore ? "retrying" : "connecting");
      source = new EventSource("/api/events");
      source.onopen = () => {
        retry = 0;
        liveStore.set("open");
        if (connectedBefore) qc.invalidateQueries();
        connectedBefore = true;
      };
      source.onerror = () => {
        source?.close();
        if (stopped) return;
        liveStore.set("retrying");
        timer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry));
        retry += 1;
      };
      source.addEventListener("job", (e) => {
        const d = data(e) as { pair_id: number; state: string };
        if (d.state !== "RUNNING") clearProgress(d.pair_id);
        invalidate(["pairs", "status", "history", "logs", "activity", "preview", "planned", "conflicts", ...FILE_KEYS]);
      });
      source.addEventListener("pair", () => invalidate(["pairs", "status", "browse"]));
      source.addEventListener("progress", (e) => {
        const p = data(e) as Progress;
        progressStore.set({ ...progressStore.get(), [p.pair_id]: p });
      });
      source.addEventListener("activity", () => invalidate(["activity"]));
      source.addEventListener("account", () => invalidate(["accounts", "status", "setup", "pairs"]));
      source.addEventListener("files", () => invalidate(FILE_KEYS));
      source.addEventListener("catalog", () => invalidate(FILE_KEYS));
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      source?.close();
    };
  }, [qc]);
}
