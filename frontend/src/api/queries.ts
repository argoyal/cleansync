import { useQuery } from "@tanstack/react-query";

import { api, ApiError, encodePath, query } from "./client";
import type {
  Account,
  ActivityEvent,
  AppSettings,
  Browse,
  CatalogSummary,
  Conflict,
  FileItem,
  FolderEntry,
  Job,
  Me,
  Pair,
  Planned,
  Preview,
  Setup,
  Status,
  Storage,
  SystemInfo,
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
export const useSetup = () => useQuery({ queryKey: ["setup"], queryFn: () => api<Setup>("/api/setup") });
export const useSystem = () => useQuery({ queryKey: ["system"], queryFn: () => api<SystemInfo>("/api/system") });
export const useAppSettings = () =>
  useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/api/settings") });

export const useBrowse = (accountId: number | null, path: string) =>
  useQuery({
    queryKey: ["browse", accountId, path],
    queryFn: () => api<Browse>(`/api/browse${query({ account_id: accountId, path })}`),
    enabled: accountId !== null,
  });
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

export const useHistory = (pairId: number) =>
  useQuery({ queryKey: ["history", pairId], queryFn: () => api<Job[]>(`/api/sync-pairs/${pairId}/history`) });
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
