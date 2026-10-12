import { useQuery } from "@tanstack/react-query";

import { api, query } from "./client";

export type RuleAction = { kind: "archive" | "mark_read" | "star" | "delete" } | { kind: "label"; label: string };
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
  per_rule: { rule: string; count: number; actions: RuleAction[]; examples: { subject: string; sender: string; why: string }[] }[];
};
/** Counts by outcome, e.g. { applied: 2, waiting: 1 }: "waiting" means the mail is queued and not yet changed. */
export type ApproveResult = Record<string, number>;
export type UndoResult = { status: string; left: string[] };
export type DeleteMailResult = { deleted: number; failed: { message_id: number; reason: string }[] };

export type AiConnection = { id: number; name: string; base_url: string; has_key: boolean; header_names: string[] };
export type AiTaskName = "sorting" | "writing_rules";
export type AiTask = { task: AiTaskName; connection_id: number | null; model: string; temperature: number; timeout_s: number };
export type AiTest = { ok: boolean; took_ms: number; mode: string | null; chose: string | null; why: string; error: string | null };

const ACTION_WORDS = { archive: "Archive", mark_read: "Mark read", star: "Star", delete: "Delete" } as const;

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
