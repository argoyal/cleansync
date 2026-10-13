import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toasts";
import { mockApi } from "../test/utils";
import { useOffloadOne } from "./useMailOffload";
import { MAIL_LISTS } from "./useRules";

describe("Offload on one mail", () => {
  it("refreshes the one list of mail queries (the same one rules use), and the decisions", async () => {
    mockApi({ "POST /api/mail/messages/5/offload": { offloaded: true, reason: null } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const wrap = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    );
    const { result } = renderHook(() => useOffloadOne(), { wrapper: wrap });
    await act(async () => {
      result.current.mutate({ accountId: 1, messageId: 5 });
    });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0]);
    expect(MAIL_LISTS.length).toBeGreaterThan(0);
    for (const k of MAIL_LISTS) expect(keys).toContain(k);
    expect(keys).toContain("rule-decisions");
  });
});
