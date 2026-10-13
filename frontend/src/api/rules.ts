import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, query } from "./client";

export type RuleAction = { kind: "archive" | "mark_read" | "star" | "offload" | "delete_forever" } | { kind: "label"; label: string };
export type RuleConditions = {
  senders_in?: string[];
  senders_not_in?: string[];
  subject_contains?: string[];
  list_unsubscribe?: boolean;
  calendar_invite?: boolean;
  has_attachments?: boolean;
  addressed?: "direct" | "cc" | "list";
};
export type Combine = "fixed_only" | "fixed_decides" | "fixed_then_ai" | "ai_only";
export type RuleFields = {
  name: string;
  description: string;
  conditions: RuleConditions;
  combine: Combine;
  actions: RuleAction[];
  mode: "suggest" | "automatic";
  enabled: boolean;
};
export type Rule = RuleFields & {
  id: number;
  position: number;
  version: number;
  decided_7d: number;
  /** Why the rule waits (a Delete forever rule while its mailbox doesn't allow it). */
  blocked?: string | null;
  senders: { id: number; sender: string; approvals: number; remembered: boolean }[];
};
export type RuleDraft = Omit<RuleFields, "mode" | "enabled"> & { notes: string[] };
export type Starter = { name: string; description: string; actions: RuleAction[] };
export type TryResult = {
  message_id: number;
  subject: string;
  sender: string;
  rule_name: string | null;
  this_rule: boolean;
  how: string;
  why: string;
};
export type Sorting = {
  enabled: boolean;
  waiting: number;
  sorted_today: number;
  suggested: number;
  failing_since: string | null;
  last_error: string | null;
  warning: boolean;
  model_set: boolean;
  paused: AiPause | null;
};
export type Decision = {
  id: number;
  message_id: number;
  thread_id: string;
  subject: string;
  from_addr: string;
  date: string;
  rule_id: number | null;
  rule_name: string;
  how: string;
  why: string;
  model: string;
  status: string;
  reason: string | null;
  actions: RuleAction[];
  moved_back: boolean;
  created_at: string;
};
export type JobState = "PREVIEWING" | "PREVIEWED" | "RUNNING" | "DONE" | "STOPPED" | "FAILED";
export type JobPreview = {
  job_id: number;
  state: JobState;
  total: number;
  decided: number;
  planned: number;
  no_rule: number;
  applied: number;
  skipped: number;
  failed: number;
  reason: string | null;
  paused?: AiPause | null;
  per_rule: { rule: string; rule_id: number | null; count: number; actions: RuleAction[]; examples: { subject: string; sender: string; why: string }[] }[];
  /** The rules the run decides with ([]: every enabled rule, for runs from before 0.5.0). */
  rule_ids: number[];
  view: string;
  q: string;
  /** What it ran on, in words: "Inbox · from:shop". */
  filter_text: string;
  left_out: number;
  skip_reasons: { reason: string; count: number }[];
};
/** One row of a run's match list (running rules spec §5). */
export type RuleMatch = {
  id: number;
  message_id: number;
  thread_id: string;
  subject: string;
  from_addr: string;
  date: string;
  rule_id: number | null;
  rule_name: string;
  actions: RuleAction[];
  why: string;
};
/** What a run covers (running rules spec §8): the Inbox (a rule card's Run), a search or a
 * pick-all as the view, label and search, or threads picked by hand in a view. */
export type RunScope = { inbox: true } | { view: string; label?: string; q: string; pick_all?: true } | { view: string; label?: string; thread_ids: string[] };
export type RunStart = { rule_ids: number[]; scope: RunScope };
/** Counts by outcome, e.g. { applied: 2, waiting: 1 }: "waiting" means the mail is queued and not yet changed. */
export type ApproveResult = Record<string, number>;
export type UndoResult = { status: string; left: string[] };

/** Why a model connection is not being called right now, and until when. */
export type AiPause = {
  kind: "limited" | "down" | "needs_you";
  until: string | null;
  reason: string;
  detail: string | null;
  since: string;
  connection_id: number;
  connection_name: string;
};
export type AiConnection = {
  id: number;
  name: string;
  base_url: string;
  has_key: boolean;
  header_names: string[];
  resets_at: string;
  resets_tz: string;
  pause: AiPause | null;
};
export type AiTaskName = "sorting" | "writing_rules";
export type AiTask = { task: AiTaskName; connection_id: number | null; model: string; temperature: number; timeout_s: number };
export type AiTest = { ok: boolean; took_ms: number; mode: string | null; chose: string | null; why: string; error: string | null; detail?: string | null };

const ACTION_WORDS = { archive: "Archive", mark_read: "Mark read", star: "Star", offload: "Offload", delete_forever: "Delete forever" } as const;

export function describeAction(a: RuleAction): string {
  return a.kind === "label" ? `Label ${a.label}` : ACTION_WORDS[a.kind];
}

export const useRules = (accountId: number | null) =>
  useQuery({
    queryKey: ["rules", accountId],
    queryFn: () => api<Rule[]>(`/api/rules${query({ account_id: accountId })}`),
    enabled: accountId !== null,
  });

export const useSorting = (accountId: number | null) =>
  useQuery({
    queryKey: ["rules-sorting", accountId],
    queryFn: () => api<Sorting>(`/api/rules/sorting${query({ account_id: accountId })}`),
    enabled: accountId !== null,
  });

export const useDecisions = (accountId: number | null, status?: string) =>
  useQuery({
    queryKey: ["rule-decisions", accountId, status ?? ""],
    queryFn: () => api<Decision[]>(`/api/rule-decisions${query({ account_id: accountId, status })}`),
    enabled: accountId !== null,
  });

const JOB_DONE: JobState[] = ["PREVIEWED", "DONE", "STOPPED", "FAILED"];
export const useRuleJob = (accountId: number, jobId: number | null) =>
  useQuery({
    queryKey: ["rule-job", jobId],
    queryFn: () => api<JobPreview>(`/api/rule-jobs/${jobId}${query({ account_id: accountId })}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && JOB_DONE.includes(q.state.data.state) ? false : 2000),
  });

export const MATCH_PAGE = 50;
/** A run's match list, newest first, 50 a page; `ruleId` keeps one rule's matches. */
export const useRuleMatches = (accountId: number, jobId: number | null, ruleId: number | null) =>
  useInfiniteQuery({
    queryKey: ["rule-job-matches", jobId, ruleId],
    queryFn: ({ pageParam }) =>
      api<RuleMatch[]>(`/api/rule-jobs/${jobId}/matches${query({ account_id: accountId, rule_id: ruleId, before_id: pageParam, limit: MATCH_PAGE })}`),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => (last.length < MATCH_PAGE ? undefined : last[last.length - 1].id),
    enabled: jobId !== null,
  });

const JOB_BUSY: JobState[] = ["PREVIEWING", "RUNNING"];
/** The mailbox's unfinished run of rules (null when there is none), so a closed dialog can be found again. */
export const useOpenRuleJob = (accountId: number | null) =>
  useQuery({
    queryKey: ["rule-job-open", accountId],
    queryFn: () => api<JobPreview | null>(`/api/rule-jobs/open${query({ account_id: accountId })}`),
    enabled: accountId !== null,
    refetchInterval: (q) => (q.state.data && JOB_BUSY.includes(q.state.data.state) ? 3000 : false),
  });

export const useStarters = () =>
  useQuery({ queryKey: ["rule-starters"], queryFn: () => api<Starter[]>("/api/rules/starters"), staleTime: Infinity });

export const useAiConnections = () => useQuery({ queryKey: ["ai-connections"], queryFn: () => api<AiConnection[]>("/api/ai/connections") });
export const useAiTasks = () => useQuery({ queryKey: ["ai-tasks"], queryFn: () => api<Record<string, AiTask>>("/api/ai/tasks") });

/** The rules as a YAML file to download (the server sends it as an attachment). */
export const rulesExportUrl = (accountId: number) => `/api/rules/export${query({ account_id: accountId })}`;

/** A saved rule's own fields, without the counts and order the server adds. */
export function ruleFields(r: Rule): RuleFields {
  return { name: r.name, description: r.description, conditions: r.conditions, combine: r.combine, actions: r.actions, mode: r.mode, enabled: r.enabled };
}
