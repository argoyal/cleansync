import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { describeAction, useRules } from "../api/rules";
import { mockApi, wrapper } from "../test/utils";
import { useApprove, useDeleteMail, useDeleteRule } from "./useRules";

describe("rules api", () => {
  it("describes actions in plain words", () => {
    expect(describeAction({ kind: "label", label: "Receipts" })).toBe("Label Receipts");
    expect(describeAction({ kind: "mark_read" })).toBe("Mark read");
  });

  it("loads rules for one mailbox and approves decisions", async () => {
    const { calls } = mockApi({
      "GET /api/rules": [{ id: 1, name: "News", description: "d", conditions: {}, combine: "ai_only", actions: [{ kind: "archive" }], mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 3, senders: [] }],
      "POST /api/rule-decisions/approve": { applied: 2 },
    });
    const rules = renderHook(() => useRules(1), { wrapper: wrapper() });
    await waitFor(() => expect(rules.result.current.data?.[0].name).toBe("News"));
    expect(calls[0].url.searchParams.get("account_id")).toBe("1");
    const approve = renderHook(() => useApprove(), { wrapper: wrapper() });
    await act(() => approve.result.current.mutateAsync({ accountId: 1, ids: [5, 6] }));
    expect(calls.at(-1)?.body).toEqual({ account_id: 1, ids: [5, 6] });
  });

  it("deletes a rule with the mailbox in the query and mail by message or thread", async () => {
    const { calls } = mockApi({
      "DELETE /api/rules/4": null,
      "POST /api/mail/delete": { deleted: 1, failed: [] },
    });
    const del = renderHook(() => useDeleteRule(), { wrapper: wrapper() });
    await act(() => del.result.current.mutateAsync({ accountId: 1, id: 4 }));
    expect(calls.at(-1)?.method).toBe("DELETE");
    expect(calls.at(-1)?.url.searchParams.get("account_id")).toBe("1");
    const mail = renderHook(() => useDeleteMail(), { wrapper: wrapper() });
    await act(() => mail.result.current.mutateAsync({ accountId: 1, messageIds: [9], threadIds: ["t"] }));
    expect(calls.at(-1)?.body).toEqual({ account_id: 1, message_ids: [9], thread_ids: ["t"] });
  });
});
