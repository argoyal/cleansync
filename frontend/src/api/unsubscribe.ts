import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, query } from "./client";

/** Unsubscribe (0.5.2 spec §3, §4): senders whose mail has an unsubscribe link, and the jobs. */
export type SenderTab = "suggested" | "all" | "unsubscribed" | "kept" | "auto_archived";
export type SenderSort = "mails" | "unread" | "newest" | "size";
export type SenderStatus = "unsubscribed" | "kept" | "auto_archived";
export type Sender = {
  address: string;
  name: string;
  count: number;
  read_pct: number;
  inbox_pct: number;
  last_at: string;
  bytes: number;
  status: SenderStatus | null;
  method: string | null;
  note: string | null;
  newest_unsubscribe: string;
  newest_unsubscribe_post: string | null;
  newest_mail_id: number;
};
export type SendersPage = { items: Sender[]; total: number; suggested: number; scan: { scanned: number; total: number } };

export type JobKind = "unsubscribe" | "auto_archive" | "keep";
export type JobOutcome = "unsubscribed" | "auto_archived" | "kept" | "not_confirmed" | "needs_you";
export type UnsubscribeJobItem = { address: string; outcome: JobOutcome | null; method: string | null; note: string | null; link: string | null };
export type UnsubscribeJob = {
  id: number;
  account_id: number;
  kind: JobKind;
  state: "WAITING" | "RUNNING" | "DONE" | "STOPPED" | "FAILED";
  total: number;
  done: number;
  stop_asked: boolean;
  reason: string | null;
  created_at: string;
  finished_at: string | null;
  items: UnsubscribeJobItem[];
};

/** The server's page size for the sender list. */
export const SENDER_PAGE = 50;

export const TAB_TITLES: [SenderTab, string][] = [
  ["suggested", "Suggested"],
  ["all", "All"],
  ["unsubscribed", "Unsubscribed"],
  ["kept", "Kept"],
  ["auto_archived", "Auto-archived"],
];
export const SORT_TITLES: [SenderSort, string][] = [
  ["mails", "Most mails"],
  ["unread", "Most unread"],
  ["newest", "Newest"],
  ["size", "Largest"],
];
export const STATUS_TEXT: Record<SenderStatus, string> = { unsubscribed: "Unsubscribed", kept: "Kept", auto_archived: "Auto-archived" };
export const OUTCOME_TEXT: Record<JobOutcome, string> = {
  unsubscribed: "Unsubscribed",
  auto_archived: "Auto-archived",
  kept: "Kept",
  not_confirmed: "Couldn't confirm",
  needs_you: "Needs you",
};
export const JOB_TITLES: Record<JobKind, string> = { unsubscribe: "Unsubscribe", auto_archive: "Auto-archive", keep: "Keep" };

export const jobRunning = (job: UnsubscribeJob | null | undefined) => !!job && (job.state === "WAITING" || job.state === "RUNNING");

/** Where an item still needing the owner points: a web page to open, or an address to write
 * to. Anything else (a javascript: link in a hostile header) is never made a link. */
export function itemLink(item: UnsubscribeJobItem): { href: string; mail: boolean } | null {
  if (item.outcome !== "not_confirmed" && item.outcome !== "needs_you") return null;
  const link = item.link?.trim() ?? "";
  if (/^https?:\/\//i.test(link)) return { href: link, mail: false };
  if (/^mailto:/i.test(link)) return { href: link, mail: true };
  return null;
}

/** The mail search for these senders' mail (spec §6): one `from:`, or `from:(a OR b OR …)`. */
export function sendersSearch(addresses: string[]): string {
  return addresses.length === 1 ? `from:${addresses[0]}` : `from:(${addresses.join(" OR ")})`;
}

export const useSenders = (
  accountId: number | null,
  { tab, q = "", sort = "mails", page = 1 }: { tab: SenderTab; q?: string; sort?: SenderSort; page?: number },
) =>
  useQuery({
    queryKey: ["unsubscribe", "senders", accountId, tab, q, sort, page],
    queryFn: () => api<SendersPage>(`/api/mail/unsubscribe/senders${query({ account_id: accountId, tab, q: q || undefined, sort, page })}`),
    enabled: accountId !== null,
    placeholderData: keepPreviousData,
    // While old mail's headers are still being scanned, the list fills in: look again now and then.
    refetchInterval: (qr) => (qr.state.data && qr.state.data.scan.scanned < qr.state.data.scan.total ? 15_000 : false),
  });

export const useUnsubscribeJob = (jobId: number | null) =>
  useQuery({
    queryKey: ["unsubscribe", "job", jobId],
    queryFn: () => api<UnsubscribeJob>(`/api/mail/unsubscribe/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (qr) => (qr.state.data && !jobRunning(qr.state.data) ? false : 2000),
  });

/** The mailbox's latest job with its items, or null: a closed window can be opened again, and
 * each sender's last result shows in the list. */
export const useLatestUnsubscribeJob = (accountId: number | null) =>
  useQuery({
    queryKey: ["unsubscribe", "latest", accountId],
    queryFn: () => api<UnsubscribeJob | null>(`/api/mail/unsubscribe/jobs${query({ account_id: accountId })}`),
    enabled: accountId !== null,
    refetchInterval: (qr) => (jobRunning(qr.state.data) ? 3000 : false),
  });
