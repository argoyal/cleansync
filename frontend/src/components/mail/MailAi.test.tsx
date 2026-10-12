import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, mockApi, renderApp } from "../../test/utils";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { RuleChip } from "./RuleChip";
import { RunRulesDialog } from "./RunRulesDialog";

const RULES = [{ id: 7, name: "Receipts", description: "", conditions: {}, combine: "ai_only", actions: [{ kind: "star" }], mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] }];

describe("AI in mail", () => {
  it("a suggestion chip approves, and corrects to another rule", async () => {
    const { calls } = mockApi({
      "GET /api/rules": RULES,
      "POST /api/rule-decisions/approve": { applied: 1 },
      "POST /api/rule-decisions/3/correct": { decision_id: 4 },
    });
    renderApp(<RuleChip accountId={1} chip={{ id: 3, rule_name: "Newsletters", status: "suggested", how: "ai", why: "a weekly digest" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Newsletters · suggested/ }));
    expect(screen.getByText("a weekly digest")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/rule-decisions/approve")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: /Newsletters · suggested/ }));
    await userEvent.click(screen.getByRole("button", { name: "Correct…" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Receipts" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-decisions/3/correct")?.body).toEqual({ account_id: 1, rule_id: 7 }));
  });

  it("delete forever needs the typed count", async () => {
    const { calls } = mockApi({ "POST /api/mail/archive/delete-forever": { deleted: 2, left: 0 } });
    renderApp(<DeleteForeverDialog account={ACCOUNT} start={{ message_ids: [1, 2] }} count={2} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("can't be undone");
    const go = within(dialog).getByRole("button", { name: "Delete forever" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Type 2/), "2");
    await userEvent.click(go);
    await waitFor(() => expect(calls[0].body).toEqual({ account_id: ACCOUNT.id, message_ids: [1, 2], confirm: 2 }));
  });

  it("run rules shows the preview and runs after the typed count", async () => {
    const preview = { job_id: 9, state: "PREVIEWED", total: 3, decided: 3, planned: 2, no_rule: 1, applied: 0, skipped: 0, failed: 0, reason: null,
      per_rule: [{ rule: "Newsletters", count: 2, actions: [{ kind: "archive" }], examples: [{ subject: "Digest", sender: "n@example.com", why: "a digest" }] }] };
    const { calls } = mockApi({
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": preview,
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} start={{ q: "older_than:30d", view: "inbox" }} onClose={() => {}} />);
    expect(await screen.findByText("Newsletters")).toBeInTheDocument();
    expect(screen.getByText(/Archive/)).toBeInTheDocument();
    expect(screen.getByText(/No rule: 1/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Type 2/), "2");
    await userEvent.click(screen.getByRole("button", { name: "Change 2 mails" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-jobs/9/run")?.body).toEqual({ account_id: ACCOUNT.id, confirm: 2 }));
  });

  it("makes a rule from picked mail, saves it, then runs it on the sender's older mail", async () => {
    const draft = { name: "Shop receipts", description: "Receipts from the shop", conditions: { senders_in: ["shop@example.com"] }, combine: "fixed_then_ai", actions: [{ kind: "label", label: "Receipts" }], notes: ["The shop sends receipts and offers; this is only receipts."] };
    const { calls } = mockApi({
      "POST /api/rules/draft-from-mail": draft,
      "POST /api/rules": { ...draft, id: 8, mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] },
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": { job_id: 9, state: "PREVIEWING", total: 10, decided: 4, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] },
    });
    renderApp(<MakeRuleDialog account={ACCOUNT} messageIds={[11, 12]} onClose={() => {}} />, { route: "/mail?q=older_than%3A1y" });
    expect(await screen.findByDisplayValue("Shop receipts")).toBeInTheDocument();
    expect(calls[0].body).toEqual({ account_id: ACCOUNT.id, message_ids: [11, 12] });
    expect(screen.getByText(/only receipts/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await userEvent.click(await screen.findByRole("button", { name: "Run it on older mail…" }));
    expect(await screen.findByText("Deciding… 4 of 10")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, q: "from:shop@example.com", view: "all" });
  });
});
