import { useSyncExternalStore } from "react";

import { csrfHeaders, detailOf, query, reportAuthProblem } from "../api/client";

export type Upload = {
  id: number;
  name: string;
  size: number;
  sent: number;
  state: "waiting" | "sending" | "done" | "failed" | "cancelled";
  error?: string;
  /** Set when the server saved the file under another name than the one sent. */
  savedAs?: string;
};

type Job = { upload: Upload; file: File; accountId: number; folder: string; relative: string; xhr?: XMLHttpRequest };

const PARALLEL = 2;
let jobs: Job[] = [];
let snapshot: Upload[] = [];
const listeners = new Set<() => void>();
let nextId = 1;
let onFinished: () => void = () => undefined;
const REFRESH_EVERY_MS = 5000;
let lastRefresh = 0;
let unrefreshed = false;

/** Refresh the file list once when the queue drains, and now and then during a long batch. */
function refreshList(drained: boolean) {
  if (!unrefreshed) return;
  if (drained || Date.now() - lastRefresh >= REFRESH_EVERY_MS) {
    unrefreshed = false;
    lastRefresh = Date.now();
    onFinished();
  }
}

function emit() {
  snapshot = jobs.map((j) => ({ ...j.upload }));
  listeners.forEach((l) => l());
}

/** Sends one file as the raw request body, so the server can stream it straight to disk. */
export function sendFile(
  job: { file: File; accountId: number; folder: string; relative: string },
  onProgress: (sent: number) => void,
): { xhr: XMLHttpRequest; done: Promise<string | void> } {
  const xhr = new XMLHttpRequest();
  const url =
    "/api/files/upload" +
    query({ account_id: job.accountId, path: job.folder, name: job.relative, mtime_ms: job.file.lastModified || undefined });
  const done = new Promise<string | void>((resolve, reject) => {
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          return resolve(JSON.parse(xhr.responseText).path);
        } catch {
          return resolve();
        }
      }
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = undefined;
      }
      const { detail } = detailOf(body, xhr.status);
      reportAuthProblem(xhr.status, detail);
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("The connection dropped"));
    xhr.onabort = () => reject(new Error("cancelled"));
  });
  xhr.open("POST", url);
  for (const [k, v] of Object.entries(csrfHeaders())) xhr.setRequestHeader(k, v);
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.send(job.file);
  return { xhr, done };
}

let sender = sendFile;
/** Tests swap the network part out. */
export function setSender(fn: typeof sendFile): void {
  sender = fn;
}

function pump() {
  while (jobs.filter((j) => j.upload.state === "sending").length < PARALLEL) {
    const next = jobs.find((j) => j.upload.state === "waiting");
    if (!next) break;
    next.upload.state = "sending";
    let started: ReturnType<typeof sendFile>;
    try {
      started = sender(next, (sent) => {
        next.upload.sent = sent;
        emit();
      });
    } catch (e) {
      next.upload.state = "failed";
      next.upload.error = e instanceof Error ? e.message : "The upload could not start";
      continue;
    }
    const { xhr, done } = started;
    next.xhr = xhr;
    emit();
    done
      .then((finalPath) => {
        if (next.upload.state === "cancelled") return;
        const sent = next.relative.split("/").pop();
        const got = typeof finalPath === "string" ? finalPath.split("/").pop() : undefined;
        if (got && got !== sent) next.upload.savedAs = got;
        next.upload.state = "done";
        next.upload.sent = next.upload.size;
        unrefreshed = true;
      })
      .catch((e: Error) => {
        if (next.upload.state === "cancelled") return;
        next.upload.state = "failed";
        next.upload.error = e.message;
      })
      .finally(() => {
        emit();
        pump();
        refreshList(!jobs.some((j) => j.upload.state === "waiting" || j.upload.state === "sending"));
      });
  }
  emit();
}

export const uploads = {
  /** Files from a file picker; for a folder picker each file carries webkitRelativePath. */
  add(files: File[], accountId: number, folder: string) {
    if (!jobs.some((j) => j.upload.state === "waiting" || j.upload.state === "sending")) lastRefresh = Date.now();
    for (const file of files) {
      const relative = file.webkitRelativePath || file.name;
      jobs.push({
        file,
        accountId,
        folder,
        relative,
        upload: { id: nextId++, name: relative, size: file.size, sent: 0, state: "waiting" },
      });
    }
    emit();
    pump();
  },
  cancel(id: number) {
    const job = jobs.find((j) => j.upload.id === id);
    if (!job || (job.upload.state !== "waiting" && job.upload.state !== "sending")) return;
    job.upload.state = "cancelled";
    job.xhr?.abort();
    emit();
    pump();
    refreshList(!jobs.some((j) => j.upload.state === "waiting" || j.upload.state === "sending"));
  },
  clearFinished() {
    jobs = jobs.filter((j) => j.upload.state === "waiting" || j.upload.state === "sending");
    emit();
  },
  onFinished(fn: () => void) {
    onFinished = fn;
    return () => {
      if (onFinished === fn) onFinished = () => undefined;
    };
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
  get: () => snapshot,
  reset() {
    jobs = [];
    unrefreshed = false;
    lastRefresh = 0;
    emit();
  },
};

export const useUploads = () => useSyncExternalStore(uploads.subscribe, uploads.get);
