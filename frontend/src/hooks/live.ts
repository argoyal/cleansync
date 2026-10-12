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
/** Mail events come in bursts (one per backup or offload batch): the reader's lists refresh
 * at most once every 5 seconds. */
export const MAIL_REFRESH_MS = 5000;
const RULES_KEYS = ["rules", "rules-sorting", "rule-decisions", "mail-threads", "mail-counts", "mail-totals", "mail-thread", "mail-message"];
const MAIL_KEYS = [
  "mail-status",
  "mail-overview",
  "mail-counts",
  "mail-totals",
  "mail-threads",
  "mail-thread",
  "mail-offloads",
  "mail-offload",
  "mail-recovery",
  "activity",
  "activity-jobs",
  "google-usage",
  "accounts",
];

/** A backup's progress events (one per page of Gmail's list) refresh only these; the lists
 * and counts wait for the job's own events, so a big first backup doesn't keep re-reading
 * them (issue #16). */
const MAIL_PROGRESS_KEYS = ["mail-status", "mail-overview", "activity-jobs", "google-usage"];
/** While a backup keeps sending progress, the lists and counts still catch up, once a minute. */
const MAIL_LIST_KEYS = ["mail-threads", "mail-counts", "mail-totals"];
export const MAIL_LIST_CATCH_UP_MS = 60_000;

/** Runs `fn` straight away, and at most once per `ms`: calls in between become one catch-up
 * run at the end of that time, so the last state is always shown. */
function throttle(fn: () => void, ms: number) {
  let lastAt = -Infinity;
  let timer: number | undefined;
  const run = () => {
    timer = undefined;
    lastAt = Date.now();
    fn();
  };
  return {
    call() {
      if (timer !== undefined) return;
      const wait = lastAt + ms - Date.now();
      if (wait <= 0) run();
      else timer = window.setTimeout(run, wait);
    },
    stop: () => window.clearTimeout(timer),
  };
}

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
    const recovery = throttle(() => invalidate(["recovery"]), RECOVERY_REFRESH_MS);
    const refreshRecovery = recovery.call;
    const mail = throttle(() => invalidate(MAIL_KEYS), MAIL_REFRESH_MS);
    // Rule changes and sorting come in bursts too: same pace as mail.
    const rules = throttle(() => invalidate(RULES_KEYS), MAIL_REFRESH_MS);
    const mailProgress = throttle(() => invalidate(MAIL_PROGRESS_KEYS), MAIL_REFRESH_MS);
    // One refresh a minute after progress first comes in, then again only if more came.
    let listCatchUp: number | undefined;
    const catchUpLists = () => {
      if (listCatchUp !== undefined) return;
      listCatchUp = window.setTimeout(() => {
        listCatchUp = undefined;
        invalidate(MAIL_LIST_KEYS);
      }, MAIL_LIST_CATCH_UP_MS);
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
        invalidate(["pairs", "status", "history", "logs", "activity", "activity-jobs", "preview", "planned", "conflicts", ...FILE_KEYS]);
        refreshRecovery();
      });
      // Mail jobs (spec §4): progress, pauses and the end of each job.
      source.addEventListener("mail", (e) => {
        const d = data(e) as { done?: unknown; state?: unknown } | null;
        if (d && typeof d.done === "number" && d.state === undefined) {
          mailProgress.call();
          catchUpLists();
        }
        else mail.call();
      });
      source.addEventListener("rules", rules.call);
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
      recovery.stop();
      mail.stop();
      rules.stop();
      mailProgress.stop();
      window.clearTimeout(listCatchUp);
      source?.close();
      liveStore.set("connecting");
      progressStore.set({});
    };
  }, [qc]);
}
