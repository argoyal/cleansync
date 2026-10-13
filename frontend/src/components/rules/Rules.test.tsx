import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MailRulesPage } from "../../pages/MailRulesPage";
import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";

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

  it("says on the card why a Delete forever rule waits", async () => {
    mockApi({
      ...ACCOUNTS,
      "GET /api/rules": [{ ...RULE, actions: [{ kind: "delete_forever" }], blocked: "Turn on Allow permanent delete in Settings → Accounts." }],
      "GET /api/rules/sorting": SORTING,
    });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("Turn on Allow permanent delete in Settings → Accounts.")).toBeInTheDocument();
    expect(screen.getByText("Delete forever")).toBeInTheDocument();
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
  it("shows a run of rules left open and opens it again", async () => {
    const job = { job_id: 9, state: "PREVIEWING", total: 40, decided: 12, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] };
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": job, "GET /api/rule-jobs/9": job });
    renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("A run of rules is going on (12 of 40 decided).")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByText("Deciding… 12 of 40")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/rule-jobs")).toBe(false);
  });

  it("says when a run of rules waits for the typed count, and says nothing when there is none", async () => {
    const job = { job_id: 9, state: "PREVIEWED", total: 4, decided: 4, planned: 2, no_rule: 2, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] };
    mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": job });
    const r = renderApp(<MailRulesPage />, { withAccount: true });
    expect(await screen.findByText("A run of rules is waiting for you to confirm.")).toBeInTheDocument();
    r.unmount();
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": null });
    renderApp(<MailRulesPage />, { withAccount: true });
    await screen.findByText("Receipts and invoices");
    await waitFor(() => expect(calls.some((c) => c.path === "/api/rule-jobs/open")).toBe(true));
    expect(screen.queryByText(/A run of rules/)).not.toBeInTheDocument();
  });

  it("runs a rule from its card over the Inbox, and has no Run rules on… any more", async () => {
    const job = { job_id: 9, state: "PREVIEWING", total: 12, decided: 3, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [], view: "inbox", q: "", rule_ids: [1], filter_text: "Inbox", left_out: 0, skip_reasons: [] };
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": null, "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": job });
    renderApp(<MailRulesPage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Run, Receipts" }));
    expect(await screen.findByText("Deciding… 3 of 12")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/rule-jobs")?.body).toEqual({ account_id: 1, rule_ids: [1], inbox: true });
    expect(screen.queryByRole("button", { name: "Run rules on…" })).not.toBeInTheDocument();
  });

  it("closing a run that is deciding keeps it going, and the page shows it with Open", async () => {
    const job = { job_id: 9, state: "PREVIEWING", total: 12, decided: 3, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [], view: "inbox", q: "", rule_ids: [1], filter_text: "Inbox", left_out: 0, skip_reasons: [] };
    const { calls } = mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE], "GET /api/rules/sorting": SORTING, "GET /api/rule-jobs/open": () => (calls.some((c) => c.method === "POST" && c.path === "/api/rule-jobs") ? json(200, job) : json(200, null)), "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": job, "POST /api/rule-jobs/9/stop": null });
    renderApp(<MailRulesPage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Run, Receipts" }));
    await screen.findByText("Deciding… 3 of 12");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("A run of rules is going on (3 of 12 decided).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/rule-jobs/9/stop")).toBe(false);
  });

  it("disables Run, with the reason, for a rule that is off or needs a model that isn't set", async () => {
    const OFF = { ...RULE, id: 2, name: "Old news", enabled: false, position: 1 };
    const FIXED = { ...RULE, id: 3, name: "Calendar", combine: "fixed_only", conditions: { calendar_invite: true }, position: 2 };
    mockApi({ ...ACCOUNTS, "GET /api/rules": [RULE, OFF, FIXED], "GET /api/rules/sorting": { ...SORTING, model_set: false } });
    renderApp(<MailRulesPage />, { withAccount: true });
    const receipts = await screen.findByRole("button", { name: "Run, Receipts" });
    expect(receipts).toBeDisabled();
    expect(receipts).toHaveAccessibleDescription("Set up a model in Settings → Models.");
    const off = screen.getByRole("button", { name: "Run, Old news" });
    expect(off).toBeDisabled();
    expect(off).toHaveAccessibleDescription("Turn the rule on to run it.");
    expect(screen.getByRole("button", { name: "Run, Calendar" })).toBeEnabled();
  });
});
