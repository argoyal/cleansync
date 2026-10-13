import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, friendlyError, query } from "../api/client";
import type {
  AiConnection,
  AiTask,
  AiTaskName,
  AiTest,
  ApproveResult,
  RuleDraft,
  Rule,
  RuleFields,
  RunStart,
  TryResult,
  UndoResult,
  Sorting,
} from "../api/rules";
import { useToast } from "../components/Toasts";

export const RULE_KEYS = ["rules", "rules-sorting", "rule-decisions", "rule-job", "rule-job-open", "rule-job-matches"];
/** The mail queries a change to mail or rules refreshes. */
export const MAIL_LISTS = ["mail-threads", "mail-counts", "mail-totals", "mail-thread", "mail-message"];

/**
 * One mutation: on success it refreshes rules and mail lists; on error it shows a toast.
 * `quiet` leaves the error to the caller, who shows it next to the form.
 * `keys` replaces the list that is refreshed.
 */
function useChange<A, R>(fn: (a: A) => Promise<R>, { quiet = false, keys = [...RULE_KEYS, ...MAIL_LISTS] } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => keys.forEach((k) => void qc.invalidateQueries({ queryKey: [k] })),
    onError: quiet ? undefined : (e) => toast(friendlyError(e), { tone: "error" }),
  });
}

const post = <R>(path: string, body: unknown) => api<R>(path, { method: "POST", body });
type Acc = { accountId: number };

// ---- rules ----
/** The form shows a refusal next to Save rule, so this one makes no toast. */
export const useSaveRule = () =>
  useChange(
    (b: Acc & { id?: number; rule: RuleFields }) =>
      api<Rule>(b.id ? `/api/rules/${b.id}` : "/api/rules", { method: b.id ? "PUT" : "POST", body: { account_id: b.accountId, ...b.rule } }),
    { quiet: true },
  );

export const useDeleteRule = () =>
  useChange((b: Acc & { id: number }) => api<void>(`/api/rules/${b.id}${query({ account_id: b.accountId })}`, { method: "DELETE" }));

export const useForgetSender = () =>
  useChange((b: Acc & { ruleId: number; senderId: number }) =>
    api<void>(`/api/rules/${b.ruleId}/senders/${b.senderId}${query({ account_id: b.accountId })}`, { method: "DELETE" }),
  );

export const useReorderRules = () =>
  useChange((b: Acc & { ids: number[] }) => post<void>("/api/rules/reorder", { account_id: b.accountId, ids: b.ids }));

export const useAddStarters = () =>
  useChange((b: Acc & { names: string[] }) => post<Rule[]>("/api/rules/starters", { account_id: b.accountId, names: b.names }));

export const useImportRules = () =>
  useChange((b: Acc & { yaml: string }) => post<Rule[]>("/api/rules/import", { account_id: b.accountId, yaml: b.yaml }));

export const useCopyRules = () =>
  useChange((b: Acc & { toAccountId: number }) =>
    post<{ copied: number }>("/api/rules/copy", { account_id: b.accountId, to_account_id: b.toAccountId }),
  );

export const useDraftRule = () =>
  useChange(
    (b: Acc & { sentence: string }) => post<RuleDraft>("/api/rules/draft", { account_id: b.accountId, sentence: b.sentence }),
    { quiet: true, keys: [] },
  );

export const useDraftFromMail = () =>
  useChange(
    (b: Acc & { messageIds: number[] }) =>
      post<RuleDraft>("/api/rules/draft-from-mail", { account_id: b.accountId, message_ids: b.messageIds }),
    { quiet: true, keys: [] },
  );

/** Tries a rule on mail without saving it: by message ids, or by a search `q`. */
export const useTryRule = () =>
  useChange(
    (b: Acc & { rule: RuleFields; ruleId?: number; messageIds?: number[]; q?: string }) =>
      post<TryResult[]>("/api/rules/try", {
        account_id: b.accountId,
        rule: b.rule,
        ...(b.ruleId !== undefined ? { rule_id: b.ruleId } : {}),
        ...(b.messageIds ? { message_ids: b.messageIds } : {}),
        ...(b.q !== undefined ? { q: b.q } : {}),
      }),
    { quiet: true, keys: [] },
  );

export const useSetSorting = () =>
  useChange((b: Acc & { enabled: boolean }) =>
    api<Sorting>("/api/rules/sorting", { method: "PUT", body: { account_id: b.accountId, enabled: b.enabled } }),
  );

// ---- decisions ----
export const useApprove = () =>
  useChange((b: Acc & { ids: number[]; confirm?: number }) =>
    post<ApproveResult>("/api/rule-decisions/approve", {
      account_id: b.accountId,
      ids: b.ids,
      ...(b.confirm !== undefined ? { confirm: b.confirm } : {}),
    }),
  );

export const useReject = () =>
  useChange((b: Acc & { ids: number[] }) => post<{ rejected: number }>("/api/rule-decisions/reject", { account_id: b.accountId, ids: b.ids }));

/** Moves a decision to another rule, or to no rule when `ruleId` is null. */
export const useCorrect = () =>
  useChange((b: Acc & { decisionId: number; ruleId: number | null }) =>
    post<{ decision_id: number }>(`/api/rule-decisions/${b.decisionId}/correct`, { account_id: b.accountId, rule_id: b.ruleId }),
  );

export const useUndo = () =>
  useChange((b: Acc & { ids: number[] }) => post<UndoResult[]>("/api/rule-decisions/undo", { account_id: b.accountId, ids: b.ids }));

// ---- jobs ----
/** Starts a run (running rules spec §8): its rules, and what it covers. */
export const useStartRuleJob = () =>
  useChange((b: Acc & { start: RunStart }) =>
    post<{ job_id: number }>("/api/rule-jobs", { account_id: b.accountId, rule_ids: b.start.rule_ids, ...b.start.scope }),
  );

/** Approves a run's match list: `leftOut` are the unticked matches, or `only` the ticked ones
 * after Pick all was unticked. `confirm` is the typed count, for Offload and Delete forever. */
export const useRunRuleJob = () =>
  useChange((b: Acc & { jobId: number; leftOut?: number[]; only?: number[]; confirm?: number }) =>
    post<{ job_id: number }>(`/api/rule-jobs/${b.jobId}/run`, {
      account_id: b.accountId,
      left_out: b.leftOut ?? [],
      ...(b.only ? { only: b.only } : {}),
      ...(b.confirm !== undefined ? { confirm: b.confirm } : {}),
    }),
  );

export const useStopRuleJob = () =>
  useChange((b: Acc & { jobId: number }) => post<void>(`/api/rule-jobs/${b.jobId}/stop`, { account_id: b.accountId }));

// ---- Delete forever ----
/** Removes CleanSync's copy of mail already out of Gmail (mail panel spec §7): picked mails,
 * or a pick-all as the view, label and search. `confirm` is the typed count. */
export const useDeleteForever = () =>
  useChange((b: Acc & { view: string; messageIds?: number[]; label?: string; q?: string; confirm: number }) =>
    post<{ deleted: number; left: number; job_id: number | null }>("/api/mail/delete-forever", {
      account_id: b.accountId,
      view: b.view,
      ...(b.messageIds ? { message_ids: b.messageIds } : {}),
      ...(b.label ? { label: b.label } : {}),
      ...(b.q !== undefined ? { q: b.q } : {}),
      confirm: b.confirm,
    }),
  );

// ---- model connections and tasks ----
const AI = ["ai-connections", "ai-tasks", "rules-sorting"];
export type ConnectionFields = { name?: string; base_url?: string; api_key?: string | null; headers?: Record<string, string>; resets_at?: string; resets_tz?: string };

/** Without `id` it adds a connection (a key is sent only when typed); with `id` it changes one. */
export const useSaveConnection = () =>
  useChange(
    (b: { id?: number; connection: ConnectionFields }) =>
      api<AiConnection>(b.id ? `/api/ai/connections/${b.id}` : "/api/ai/connections", {
        method: b.id ? "PATCH" : "POST",
        body: b.connection,
      }),
    { keys: AI },
  );

export const useDeleteConnection = () =>
  useChange((b: { id: number }) => api<void>(`/api/ai/connections/${b.id}`, { method: "DELETE" }), { keys: AI });

/** Asks the provider which models it has; the error is shown by the caller. */
export const useListModels = () =>
  useChange((b: { id: number }) => api<{ models: string[] }>(`/api/ai/connections/${b.id}/models`), { quiet: true, keys: [] });

export type TaskFields = { connection_id: number | null; model: string; temperature: number; timeout_s: number };
export const useSaveTask = () =>
  useChange((b: { task: AiTaskName; fields: TaskFields }) => api<AiTask>(`/api/ai/tasks/${b.task}`, { method: "PUT", body: b.fields }), {
    keys: AI,
  });

export const useTestModel = () =>
  useChange((b: TaskFields & { connection_id: number }) => post<AiTest>("/api/ai/test", b), { quiet: true, keys: AI });

/** Lets a paused connection be called again right away. */
export const useResumeConnection = () =>
  useChange((b: { id: number }) => post<AiConnection>(`/api/ai/connections/${b.id}/resume`, {}), { keys: [...AI, "rule-job", "rule-job-open"] });
