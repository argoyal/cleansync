import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, ApiError, encodePath, query } from "./client";
import type {
  Account,
  ActivityJob,
  ActivityEvent,
  AppSettings,
  Browse,
  CatalogSummary,
  Conflict,
  FileItem,
  FolderEntry,
  GoogleUsage,
  MailTotals,
  Job,
  MailCounts,
  MailMessageInfo,
  MailOffload,
  MailOffloadDetail,
  MailRecoveryData,
  MailSetupInfo,
  MailStatus,
  Me,
  Offload,
  OffloadBatch,
  Pair,
  Planned,
  Preview,
  RecoveryList,
  Setup,
  Status,
  Storage,
  SystemInfo,
  ThreadPage,
} from "./types";

// Query keys start with a word the live-update hook (hooks/live.ts) can invalidate by.
export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/auth/me"), retry: false });
export const useHealth = () =>
  useQuery({
    queryKey: ["health"],
    queryFn: () => api<{ status: string; version: string; app_name: string }>("/api/health"),
    staleTime: Infinity,
  });
export const useAccounts = () => useQuery({ queryKey: ["accounts"], queryFn: () => api<Account[]>("/api/accounts") });
export const usePairs = () => useQuery({ queryKey: ["pairs"], queryFn: () => api<Pair[]>("/api/sync-pairs") });
export const usePair = (id: number) =>
  useQuery({ queryKey: ["pairs", id], queryFn: () => api<Pair>(`/api/sync-pairs/${id}`) });
export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/api/status") });
export const useGoogleUsage = () =>
  useQuery({ queryKey: ["google-usage"], queryFn: () => api<GoogleUsage>("/api/google/usage") });
export const useSetup = () => useQuery({ queryKey: ["setup"], queryFn: () => api<Setup>("/api/setup") });
export const useSystem = () => useQuery({ queryKey: ["system"], queryFn: () => api<SystemInfo>("/api/system") });
export const useAppSettings = () =>
  useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/api/settings") });

/** One folder's listing; shared by useBrowse and code that needs a listing outside a component. */
export const browseOptions = (accountId: number | null, path: string) => ({
  queryKey: ["browse", accountId, path],
  queryFn: () => api<Browse>(`/api/browse${query({ account_id: accountId, path })}`),
});
export const useBrowse = (accountId: number | null, path: string) =>
  useQuery({ ...browseOptions(accountId, path), enabled: accountId !== null });
export const useRecent = (accountId: number | null, limit = 50) =>
  useQuery({
    queryKey: ["recent", accountId, limit],
    queryFn: () => api<FileItem[]>(`/api/recent${query({ account_id: accountId, limit })}`),
    enabled: accountId !== null,
  });
export const useStarred = (accountId: number | null) =>
  useQuery({
    queryKey: ["starred", accountId],
    queryFn: () => api<FileItem[]>(`/api/starred${query({ account_id: accountId })}`),
    enabled: accountId !== null,
  });
export const useSearch = (accountId: number | null, q: string) =>
  useQuery({
    queryKey: ["search", accountId, q],
    queryFn: () => api<FileItem[]>(`/api/search${query({ account_id: accountId, q })}`),
    enabled: accountId !== null && q.trim().length >= 2,
  });
export const useStorage = (accountId: number | null) =>
  useQuery({
    queryKey: ["storage", accountId],
    queryFn: () => api<Storage>(`/api/storage${query({ account_id: accountId })}`),
    enabled: accountId !== null,
  });

export const useHistory = (pairId: number, limit = 50) =>
  useQuery({
    queryKey: ["history", pairId, limit],
    queryFn: () => api<Job[]>(`/api/sync-pairs/${pairId}/history${query({ limit })}`),
  });
export const useLogs = (pairId: number, live: boolean) =>
  useQuery({
    queryKey: ["logs", pairId],
    queryFn: () => api<{ job_id: number | null; lines: string[] }>(`/api/sync-pairs/${pairId}/logs?tail=300`),
    refetchInterval: live ? 2000 : false,
  });
export const useActivity = (pairId?: number) =>
  useQuery({
    queryKey: ["activity", pairId ?? "all"],
    queryFn: () => api<ActivityEvent[]>(`/api/activity${query({ pair_id: pairId, limit: 100 })}`),
  });
export const useConflicts = (pairId: number) =>
  useQuery({
    queryKey: ["conflicts", pairId],
    queryFn: () => api<Conflict[]>(`/api/sync-pairs/${pairId}/conflicts`),
  });
export const usePlanned = (pairId: number) =>
  useQuery({
    queryKey: ["planned", pairId],
    queryFn: () => api<Planned>(`/api/sync-pairs/${pairId}/planned-changes`),
  });
export const useCatalog = (pairId: number) =>
  useQuery({
    queryKey: ["catalog", pairId],
    queryFn: () => api<CatalogSummary>(`/api/sync-pairs/${pairId}/catalog`),
  });
const POLL_MS = 2000;
const notFound = (e: unknown) => e instanceof ApiError && e.status === 404;

/** The stored first-sync preview. 404 means "none yet"; polling (while waiting) stops on any other error. */
export const usePreview = (pairId: number | null, waiting: boolean) =>
  useQuery({
    queryKey: ["preview", pairId],
    queryFn: () => api<Preview>(`/api/sync-pairs/${pairId}/preview`),
    enabled: pairId !== null,
    retry: false,
    refetchInterval: (q) => (waiting && (!q.state.error || notFound(q.state.error)) ? POLL_MS : false),
  });

const ACTIVE_JOB = ["QUEUED", "RUNNING"];

/** Recent jobs of a pair including previews and dry runs; polls while one is active (or `poll`), stops on error. */
export const usePairJobs = (pairId: number | null, poll = false) =>
  useQuery({
    queryKey: ["history", pairId, "all"],
    queryFn: () => api<Job[]>(`/api/sync-pairs/${pairId}/history${query({ limit: 20, include_dry_runs: true })}`),
    enabled: pairId !== null,
    retry: false,
    refetchInterval: (q) =>
      !q.state.error && (poll || (q.state.data ?? []).some((j) => ACTIVE_JOB.includes(j.state))) ? POLL_MS : false,
  });
export const useRemoteFolders = (accountId: number, path: string, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ["remote-folders", accountId, path],
    queryFn: () => api<FolderEntry[]>(`/api/remote-browse${query({ account_id: accountId, path })}`),
  });
export const useLocalFolders = (accountId: number, path: string, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ["local-folders", accountId, path],
    queryFn: () => api<FolderEntry[]>(`/api/local-browse${query({ account_id: accountId, path })}`),
  });

export const fileUrl = (accountId: number, path: string, download = false) =>
  `/files/${accountId}/${encodePath(path)}${download ? "?download=1" : ""}`;
export const zipUrl = (accountId: number, path: string) => `/api/files/zip${query({ account_id: accountId, path })}`;
export const thumbUrl = (accountId: number, item: FileItem) =>
  `/api/thumbs/${accountId}/${encodePath(item.path)}${query({ v: item.mtime ?? "" })}`;

const OFFLOAD_MOVING = ["PENDING", "DELETING", "VERIFYING", "RESTORING"];
export const useOffloads = (accountId: number | null, includeRestored = false) =>
  useQuery({
    queryKey: ["offloads", accountId, includeRestored],
    queryFn: () => api<Offload[]>(`/api/offloads${query({ account_id: accountId, include_restored: includeRestored })}`),
    enabled: accountId !== null,
    // Busy rows (being emptied, or on their way back to Drive) change without a page action.
    refetchInterval: (q) => ((q.state.data ?? []).some((o) => o.busy || OFFLOAD_MOVING.includes(o.state)) ? 3000 : false),
  });
export const useOffloadBatch = (batchId: string | null) =>
  useQuery({
    queryKey: ["offload-batch", batchId],
    queryFn: () => api<OffloadBatch>(`/api/offloads/batches/${batchId}`),
    enabled: batchId !== null,
    retry: false,
    refetchInterval: (q) => (q.state.data?.done || q.state.error ? false : 1000),
  });

export type RecoveryFilters = { pair: string; kind: string; path: string; limit: number };
/** Empty filters mean "all" to the API. "Show more" keeps the current page on screen while it loads. */
export const useRecovery = (accountId: number | null, f: RecoveryFilters) =>
  useQuery({
    queryKey: ["recovery", accountId, f.pair, f.kind, f.path, f.limit],
    queryFn: () =>
      api<RecoveryList>(`/api/recovery${query({ account_id: accountId, pair: f.pair, kind: f.kind, path: f.path, limit: f.limit })}`),
    enabled: accountId !== null,
    // Only for a bigger limit with the same filters: other filters must not show old rows.
    placeholderData: (prev, prevQuery) => {
      const same = [accountId, f.pair, f.kind, f.path];
      return prevQuery && same.every((v, i) => prevQuery.queryKey[i + 1] === v) ? prev : undefined;
    },
  });
export const recoveryFileUrl = (accountId: number, id: string) => `/api/recovery/file${query({ account_id: accountId, id })}`;
/** Where a ticket from POST /api/recovery/zip is downloaded (one use, a few minutes). */
export const recoveryZipUrl = (token: string) => `/api/recovery/zip/${encodeURIComponent(token)}`;

export const useMailStatus = (accountId: number | null, enabled = true) =>
  useQuery({
    queryKey: ["mail-status", accountId],
    queryFn: () => api<MailStatus>(`/api/mail/status${query({ account_id: accountId })}`),
    enabled: accountId !== null && enabled,
    // Live events say when a job moves; this keeps the progress line fresh between them.
    refetchInterval: (q) => (q.state.data?.running || q.state.data?.waiting ? 3000 : false),
  });
export const useMailCounts = (accountId: number | null, enabled = true) =>
  useQuery({
    queryKey: ["mail-counts", accountId],
    queryFn: () => api<MailCounts>(`/api/mail/counts${query({ account_id: accountId })}`),
    enabled: accountId !== null && enabled,
  });
export const useMailOverview = () => useQuery({ queryKey: ["mail-overview"], queryFn: () => api<MailStatus[]>("/api/mail/overview") });
export const useMailSetupInfo = (accountId: number, enabled: boolean) =>
  useQuery({
    queryKey: ["mail-setup-info", accountId],
    queryFn: () => api<MailSetupInfo>(`/api/mail/setup-info${query({ account_id: accountId })}`),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
export const useActivityJobs = () =>
  useQuery({ queryKey: ["activity-jobs"], queryFn: () => api<ActivityJob[]>("/api/activity/jobs?limit=30") });

export const useMailThreads = (accountId: number, p: { view: string; label: string; q: string }) =>
  useInfiniteQuery({
    queryKey: ["mail-threads", accountId, p.view, p.label, p.q],
    queryFn: ({ pageParam }) =>
      api<ThreadPage>(
        `/api/mail/threads${query({ account_id: accountId, view: p.view, label: p.label || undefined, q: p.q || undefined, cursor: pageParam || undefined, limit: 50 })}`,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });

export const useMailTotals = (accountId: number, p: { view: string; label: string; q: string }) =>
  useQuery({
    queryKey: ["mail-totals", accountId, p.view, p.label, p.q],
    queryFn: () =>
      api<MailTotals>(`/api/mail/totals${query({ account_id: accountId, view: p.view, label: p.label || undefined, q: p.q || undefined })}`),
    retry: false,
  });

export const useMailThread = (accountId: number, threadId: string) =>
  useQuery({
    queryKey: ["mail-thread", accountId, threadId],
    queryFn: () => api<MailMessageInfo[]>(`/api/mail/threads/${encodeURIComponent(threadId)}${query({ account_id: accountId })}`),
    enabled: threadId !== "",
  });
/** One mail in full; the server reads its file to tell if it has remote images. */
export const useMailMessage = (id: number, enabled = true) =>
  useQuery({ queryKey: ["mail-message", id], queryFn: () => api<MailMessageInfo>(`/api/mail/messages/${id}`), enabled });
export const mailBodyUrl = (id: number, images: boolean) => `/api/mail/messages/${id}/body${images ? "?images=1" : ""}`;
export const mailAttachmentUrl = (id: number, index: number, download = false) =>
  `/api/mail/messages/${id}/attachments/${index}${download ? "?download=1" : ""}`;
export const mailEmlUrl = (id: number) => `/api/mail/messages/${id}/eml`;

const OFFLOAD_BUSY = ["WAITING", "RUNNING"];
export const useMailOffloads = (accountId: number | null) =>
  useQuery({
    queryKey: ["mail-offloads", accountId],
    queryFn: () => api<MailOffload[]>(`/api/mail/offloads${query({ account_id: accountId })}`),
    enabled: accountId !== null,
  });
/** One offload; asked again every 2 s while it waits or runs. Not asked for an id that
 * isn't a whole number (null). */
export const useMailOffload = (id: number | null) =>
  useQuery({
    queryKey: ["mail-offload", id],
    queryFn: () => api<MailOffloadDetail>(`/api/mail/offloads/${id}`),
    enabled: id !== null,
    retry: false,
    refetchInterval: (q) => (OFFLOAD_BUSY.includes(q.state.data?.state ?? "") ? 2000 : false),
  });
/** Mail in Recovery, a page of offloads at a time (the server takes at most 200 a page). */
export const MAIL_RECOVERY_PAGE = 50;
export const useMailRecovery = (accountId: number | null) =>
  useInfiniteQuery({
    queryKey: ["mail-recovery", accountId],
    queryFn: ({ pageParam }) =>
      api<MailRecoveryData>(`/api/recovery/mail${query({ account_id: accountId, limit: MAIL_RECOVERY_PAGE, offset: pageParam })}`),
    initialPageParam: 0,
    // The server sends no total: a short page is the last one.
    getNextPageParam: (last, pages) => (last.offloads.length < MAIL_RECOVERY_PAGE ? undefined : pages.length * MAIL_RECOVERY_PAGE),
    enabled: accountId !== null,
    retry: false,
  });
