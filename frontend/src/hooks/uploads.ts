import { useSyncExternalStore } from "react";

import { csrfHeaders, query } from "../api/client";

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
      let detail = `Upload failed (${xhr.status})`;
      try {
        detail = JSON.parse(xhr.responseText).detail ?? detail;
      } catch {
        // keep the generic text
      }
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
    if (!next) return;
    next.upload.state = "sending";
    const { xhr, done } = sender(next, (sent) => {
      next.upload.sent = sent;
      emit();
    });
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
        onFinished();
      })
      .catch((e: Error) => {
        if (next.upload.state === "cancelled") return;
        next.upload.state = "failed";
        next.upload.error = e.message;
      })
      .finally(() => {
        emit();
        pump();
      });
  }
}

export const uploads = {
  /** Files from a file picker; for a folder picker each file carries webkitRelativePath. */
  add(files: File[], accountId: number, folder: string) {
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
    if (!job) return;
    job.upload.state = "cancelled";
    job.xhr?.abort();
    emit();
    pump();
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
    emit();
  },
};

export const useUploads = () => useSyncExternalStore(uploads.subscribe, uploads.get);
