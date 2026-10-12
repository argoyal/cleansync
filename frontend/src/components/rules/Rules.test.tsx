import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MailRulesPage } from "../../pages/MailRulesPage";
import { ACCOUNT, mockApi, renderApp } from "../../test/utils";

const SORTING = { enabled: false, waiting: 0, sorted_today: 0, suggested: 0, failing_since: null, last_error: null, warning: false, model_set: true };
const RULE = { id: 1, name: "Receipts", description: "Receipts and invoices", conditions: {}, combine: "ai_only", actions: [{ kind: "label", label: "Receipts" }], mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 4, senders: [] };
// renderApp's withAccount needs the accounts list; ACCOUNT has id 1.
const ACCOUNTS = { "GET /api/accounts": [ACCOUNT] };

describe("Rules section", () => {
  it("shows rules as sentences with plain actions", async () => {
    mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("Receipts and invoices")).toBeInTheDocument();
    expect(screen.getByText("Label Receipts")).toBeInTheDocument();
    expect(screen.getByText(/4 mails in 7 days/)).toBeInTheDocument();
  });

  it("writes a rule from a sentence; nothing is saved until confirmed", async () => {
    const { calls } = mockApi({
      ...ACCOUNTS,
      "GET /api/rules": [],
      "GET /api/rules/sorting": SORTING,
      "POST /api/rules/draft": { name: "Old newsletters", description: "Newsletters I signed up for", conditions: { senders_not_in: ["substack.com"] }, combine: "fixed_then_ai", actions: [{ kind: "archive" }], notes: [] },
      "POST /api/rules": { ...RULE, id: 2, name: "Old newsletters" },
    });
    renderApp(<MailRulesPage />, { withAccount: true });
    await userEvent.type(await screen.findByLabelText(/in your own words/), "archive newsletters except Substack");
    await userEvent.click(screen.getByRole("button", { name: "Write the rule" }));
    expect(await screen.findByDisplayValue("Old newsletters")).toBeInTheDocument();
    expect(screen.getByDisplayValue("substack.com")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/rules")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/rules")?.body).toMatchObject({ name: "Old newsletters", combine: "fixed_then_ai" }));
  });

  it("warns when no model is set and turns sorting on", async () => {
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [], "GET /api/rules/sorting": { ...SORTING, model_set: false }, "PUT /api/rules/sorting": { ...SORTING, enabled: true } });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText(/No model is set up yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Sort new inbox mail" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ account_id: 1, enabled: true }));
  });

  it("moves a rule down, asks before deleting, and links the export with the account", async () => {
    const OTHER = { ...RULE, id: 2, name: "Old news", description: "", position: 1 };
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE, OTHER], "GET /api/rules/sorting": SORTING, "POST /api/rules/reorder": null, "DELETE /api/rules/2": null });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("Old news")).toBeInTheDocument(); // no description: the name
    await userEvent.click(screen.getByRole("button", { name: "Move down, Receipts" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rules/reorder")?.body).toEqual({ account_id: 1, ids: [2, 1] }));
    expect(screen.getByRole("link", { name: "Export" })).toHaveAttribute("href", "/api/rules/export?account_id=1");
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    expect(screen.getByText("Delete this rule? Its waiting suggestions are withdrawn.")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.search === "?account_id=1")).toBe(true));
  });
});
