import { screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
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

  it("says plainly why mail was left as it was, without sending it to Suggested", async () => {
    const done = { job_id: 9, state: "DONE", total: 5, decided: 5, planned: 3, no_rule: 2, applied: 1, skipped: 2, failed: 0, reason: null, per_rule: [] };
    mockApi({ "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": done });
    const r = renderApp(<RunRulesDialog account={ACCOUNT} start={{ q: "", view: "inbox" }} onClose={() => {}} />);
    expect(await screen.findByText("Done. Applied 1, skipped 2, failed 0.")).toBeInTheDocument();
    expect(screen.getByText("2 mails were left as they were because they changed since the preview.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Suggested/ })).not.toBeInTheDocument();
    r.unmount();
    mockApi({ "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": { ...done, state: "STOPPED", skipped: 1 } });
    renderApp(<RunRulesDialog account={ACCOUNT} start={{ q: "", view: "inbox" }} onClose={() => {}} />);
    expect(await screen.findByText("1 mail was left as it was.")).toBeInTheDocument();
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

  it("offers Approve only for a suggestion, and Undo only once applied", async () => {
    mockApi({});
    const chip = { id: 3, rule_name: "Newsletters", how: "ai", why: "a weekly digest" };
    const planned = renderApp(<RuleChip accountId={1} chip={{ ...chip, status: "planned" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Newsletters · planned/ }));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    planned.unmount();
    renderApp(<RuleChip accountId={1} chip={{ ...chip, status: "applied" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Newsletters · applied/ }));
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("stops the job when the dialog was closed before the job had started", async () => {
    let answer: (r: Response) => void = () => {};
    const { calls } = mockApi({
      "POST /api/rule-jobs": () => new Promise<Response>((resolve) => (answer = resolve)),
      "POST /api/rule-jobs/9/stop": null,
    });
    function Harness() {
      const [open, setOpen] = useState(true);
      return open ? <RunRulesDialog account={ACCOUNT} start={{ q: "", view: "inbox" }} onClose={() => setOpen(false)} /> : <p>closed</p>;
    }
    renderApp(<Harness />);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/rule-jobs")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("closed")).toBeInTheDocument();
    answer(json(200, { job_id: 9 }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-jobs/9/stop")?.body).toEqual({ account_id: ACCOUNT.id }));
  });

  it("closing a preview stops it, so it never blocks the next run; a running job keeps going", async () => {
    const previewed = { job_id: 9, state: "PREVIEWED", total: 2, decided: 2, planned: 1, no_rule: 1, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] };
    const { calls } = mockApi({ "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": previewed, "POST /api/rule-jobs/9/stop": null });
    const closed = renderApp(<RunRulesDialog account={ACCOUNT} start={{ q: "", view: "inbox" }} onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-jobs/9/stop")?.body).toEqual({ account_id: ACCOUNT.id }));
    closed.unmount();
    const running = mockApi({ "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": { ...previewed, state: "RUNNING" }, "POST /api/rule-jobs/9/stop": null });
    renderApp(<RunRulesDialog account={ACCOUNT} start={{ q: "", view: "inbox" }} onClose={() => {}} />);
    expect(await screen.findByText("It keeps going if you close this.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(running.calls.some((c) => c.path === "/api/rule-jobs/9/stop")).toBe(false);
  });

  it("reopens a run that is already there instead of starting another", async () => {
    const previewed = { job_id: 9, state: "PREVIEWED", total: 2, decided: 2, planned: 1, no_rule: 1, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] };
    const { calls } = mockApi({ "GET /api/rule-jobs/9": previewed });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByLabelText("Type 1 to make these changes")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("runs a rule from several senders on the list the reader showed, never on a view the rules can't use", async () => {
    const draft = { name: "Shops", description: "Shop mail", conditions: { senders_in: ["a@example.com", "b@example.com"] }, combine: "fixed_then_ai", actions: [{ kind: "archive" }], notes: [] };
    const routes = {
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "My Shops", count: 3 }] },
      "POST /api/rules/draft-from-mail": draft,
      "POST /api/rules": { ...draft, id: 8, mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] },
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": { job_id: 9, state: "PREVIEWING", total: 1, decided: 0, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] },
    };
    for (const [route, expected] of [
      ["/mail?label=Label_2&q=older_than%3A1y", { q: 'label:"My Shops" older_than:1y', view: "all" }],
      ["/mail?view=suggested", { q: "", view: "inbox" }],
    ] as const) {
      const { calls } = mockApi(routes);
      const r = renderApp(<MakeRuleDialog account={ACCOUNT} messageIds={[11]} onClose={() => {}} />, { route });
      await userEvent.click(await screen.findByRole("button", { name: "Save rule" }));
      await userEvent.click(await screen.findByRole("button", { name: "Run it on older mail…" }));
      await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-jobs")?.body, route).toEqual({ account_id: ACCOUNT.id, ...expected }));
      r.unmount();
    }
  });
});
