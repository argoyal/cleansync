import { screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { pauseLine } from "../../lib/pause";
import { ACCOUNT, type Call, json, mockApi, renderApp } from "../../test/utils";
import { DeleteForeverDialog } from "./DeleteForeverDialog";
import { MakeRuleDialog } from "./MakeRuleDialog";
import { RuleChip } from "./RuleChip";
import { RunRulesDialog } from "./RunRulesDialog";

const RULES = [{ id: 7, name: "Receipts", description: "", conditions: {}, combine: "ai_only", actions: [{ kind: "star" }], mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] }];
const JOB = {
  job_id: 9,
  state: "PREVIEWED",
  total: 4,
  decided: 4,
  planned: 3,
  no_rule: 1,
  applied: 0,
  skipped: 0,
  failed: 0,
  reason: null,
  view: "inbox",
  q: "",
  rule_ids: [5],
  filter_text: "Inbox",
  left_out: 0,
  skip_reasons: [],
  per_rule: [{ rule: "Newsletters", rule_id: 5, count: 3, actions: [{ kind: "archive" }], examples: [] }],
};
const NEWS = { rule_id: 5, rule_name: "Newsletters", actions: [{ kind: "archive" }] };
const match = (id: number, subject: string, rule: { rule_id: number; rule_name: string; actions: unknown[] } = NEWS) => ({
  id,
  message_id: id + 100,
  thread_id: `t${id}`,
  subject,
  from_addr: "news@example.com",
  date: "2026-10-01T09:00:00Z",
  why: "a digest",
  ...rule,
});
const INBOX_RUN = { rule_ids: [5], scope: { inbox: true as const } };
const runBody = (calls: Call[]) => calls.find((c) => c.path === "/api/rule-jobs/9/run")?.body;

describe("AI in mail", () => {
  it("a suggestion chip approves, and corrects to another rule", async () => {
    const { calls } = mockApi({
      "GET /api/rules": RULES,
      "POST /api/rule-decisions/approve": { applied: 1 },
      "POST /api/rule-decisions/3/correct": { decision_id: 4 },
    });
    renderApp(<RuleChip accountId={1} chip={{ id: 3, rule_name: "Newsletters", status: "suggested", how: "ai", why: "a weekly digest", undoable: true, forever: false }} />);
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
    const { calls } = mockApi({ "POST /api/mail/delete-forever": { deleted: 2, left: 0 } });
    renderApp(<DeleteForeverDialog account={ACCOUNT} start={{ view: "offloaded", message_ids: [1, 2] }} count={2} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("2 mails from Offloaded.");
    const go = within(dialog).getByRole("button", { name: "Delete forever" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Type 2 to delete forever"), "2");
    await userEvent.click(go);
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "offloaded", message_ids: [1, 2], confirm: 2 }));
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
    expect(calls.find((c) => c.path === "/api/rules/draft-from-mail")?.body).toEqual({ account_id: ACCOUNT.id, message_ids: [11, 12] });
    expect(screen.getByText(/only receipts/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await userEvent.click(await screen.findByRole("button", { name: "Run it on older mail…" }));
    expect(await screen.findByText("Deciding… 4 of 10")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, rule_ids: [8], view: "all", q: "from:shop@example.com" });
  });

  it("doesn't offer to run a saved rule that needs a model when none is set up", async () => {
    const draft = { name: "Shops", description: "Shop mail", conditions: { senders_in: ["a@example.com"] }, combine: "fixed_then_ai", actions: [{ kind: "archive" }], notes: [] };
    mockApi({
      "GET /api/rules/sorting": { enabled: false, waiting: 0, sorted_today: 0, suggested: 0, failing_since: null, last_error: null, warning: false, model_set: false },
      "POST /api/rules/draft-from-mail": draft,
      "POST /api/rules": { ...draft, id: 8, mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] },
    });
    renderApp(<MakeRuleDialog account={ACCOUNT} messageIds={[11]} onClose={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Save rule" }));
    expect(await screen.findByText("Set up a model in Settings → Models.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run it on older mail…" })).not.toBeInTheDocument();
  });

  it("offers Approve only for a suggestion, and Undo only once applied", async () => {
    mockApi({});
    const chip = { id: 3, rule_name: "Newsletters", how: "ai", why: "a weekly digest", undoable: true, forever: false };
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
      return open ? <RunRulesDialog account={ACCOUNT} start={INBOX_RUN} onClose={() => setOpen(false)} /> : <p>closed</p>;
    }
    renderApp(<Harness />);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/rule-jobs")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("closed")).toBeInTheDocument();
    answer(json(200, { job_id: 9 }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-jobs/9/stop")?.body).toEqual({ account_id: ACCOUNT.id }));
  });

  it("closing a run that has an id leaves it going, so the Rules page can open it again", async () => {
    const previewed = { ...JOB, planned: 1 };
    for (const state of ["PREVIEWING", "PREVIEWED", "RUNNING"]) {
      const { calls } = mockApi({ "POST /api/rule-jobs": { job_id: 9 }, "GET /api/rule-jobs/9": { ...previewed, state }, "GET /api/rule-jobs/9/matches": [], "POST /api/rule-jobs/9/stop": null });
      const r = renderApp(<RunRulesDialog account={ACCOUNT} start={INBOX_RUN} onClose={() => {}} />);
      await userEvent.click(await screen.findByRole("button", { name: state === "RUNNING" ? "Close" : "Cancel" }));
      expect(calls.some((c) => c.path === "/api/rule-jobs/9/stop"), state).toBe(false);
      r.unmount();
    }
  });

  it("shows why deciding is slow when the model is paused", async () => {
    const paused = { kind: "down" as const, until: "2026-10-14T00:00:00Z", reason: "Couldn't reach x.", detail: null, since: "2026-10-13T10:00:00Z", connection_id: 1, connection_name: "Groq" };
    mockApi({ "GET /api/rule-jobs/9": { job_id: 9, state: "PREVIEWING", total: 10, decided: 4, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [], paused } });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText(pauseLine(paused))).toBeInTheDocument();
  });

  it("runs a rule from several senders on the list the reader showed, or on the Inbox from a list rules can't use", async () => {
    const draft = { name: "Shops", description: "Shop mail", conditions: { senders_in: ["a@example.com", "b@example.com"] }, combine: "fixed_then_ai", actions: [{ kind: "archive" }], notes: [] };
    const routes = {
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "My Shops", count: 3 }] },
      "POST /api/rules/draft-from-mail": draft,
      "POST /api/rules": { ...draft, id: 8, mode: "suggest", enabled: true, position: 0, version: 1, decided_7d: 0, senders: [] },
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": { job_id: 9, state: "PREVIEWING", total: 1, decided: 0, planned: 0, no_rule: 0, applied: 0, skipped: 0, failed: 0, reason: null, per_rule: [] },
    };
    for (const [route, expected] of [
      ["/mail?label=Label_2&q=older_than%3A1y", { rule_ids: [8], view: "all", label: "Label_2", q: "older_than:1y", pick_all: true }],
      ["/mail?view=suggested", { rule_ids: [8], inbox: true }],
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

describe("the Run rules window", () => {
  it("starts with every matched mail picked, and unticking one leaves it out", async () => {
    const { calls } = mockApi({
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": JOB,
      "GET /api/rule-jobs/9/matches": [match(13, "Digest three"), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} start={INBOX_RUN} onClose={() => {}} />);
    expect(await screen.findByText("Newsletters would archive 3 mails. 1 didn't fit.")).toBeInTheDocument();
    expect(screen.getByText("On: Inbox")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, rule_ids: [5], inbox: true });
    const all = screen.getByRole("checkbox", { name: "Pick all" });
    expect(all).toBeChecked();
    expect(await screen.findByRole("checkbox", { name: "Pick Digest two" })).toBeChecked();
    expect(screen.queryByLabelText(/^Type /)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest two" }));
    expect(all).toBePartiallyChecked();
    expect(screen.getByRole("checkbox", { name: "Pick Digest three" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Archive 2 mails" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [12] }));
  });

  it("unticking Pick all leaves nothing picked; ticked rows are sent as the only ones", async () => {
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": JOB,
      "GET /api/rule-jobs/9/matches": [match(13, "Digest three"), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    const all = await screen.findByRole("checkbox", { name: "Pick all" });
    await screen.findByRole("checkbox", { name: "Pick Digest one" });
    await userEvent.click(all);
    expect(all).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick Digest one" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Archive 0 mails" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest one" }));
    expect(all).toBePartiallyChecked();
    await userEvent.click(screen.getByRole("button", { name: "Archive 1 mail" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [], only: [11] }));
  });

  it("with several rules: a line per rule, a chip per row, a filter that keeps the picks, and Apply N changes", async () => {
    const two = {
      ...JOB,
      rule_ids: [5, 6],
      per_rule: [
        { rule: "Newsletters", rule_id: 5, count: 2, actions: [{ kind: "archive" }], examples: [] },
        { rule: "Receipts", rule_id: 6, count: 1, actions: [{ kind: "label", label: "Receipts" }], examples: [] },
      ],
    };
    const RECEIPTS = { rule_id: 6, rule_name: "Receipts", actions: [{ kind: "label", label: "Receipts" }] };
    const { calls } = mockApi({
      "POST /api/rule-jobs": { job_id: 9 },
      "GET /api/rule-jobs/9": two,
      "GET /api/rule-jobs/9/matches": ({ url }: { url: URL }) =>
        url.searchParams.get("rule_id") === "6" ? [match(13, "Bill", RECEIPTS)] : [match(13, "Bill", RECEIPTS), match(12, "Digest two"), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} start={{ rule_ids: [5, 6], scope: { view: "inbox", q: "from:shop" } }} onClose={() => {}} />);
    const lines = await screen.findByRole("list", { name: "What each rule would do" });
    expect(within(lines).getByText("Newsletters · archive · 2")).toBeInTheDocument();
    expect(within(lines).getByText("Receipts · label Receipts · 1")).toBeInTheDocument();
    expect(screen.getByText("1 didn't fit.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/rule-jobs")?.body).toEqual({ account_id: ACCOUNT.id, rule_ids: [5, 6], view: "inbox", q: "from:shop" });
    const list = screen.getByRole("list", { name: "Matched mail" });
    expect(await within(list).findByText("Receipts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 3 changes" })).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Digest one" }));
    expect(screen.getByRole("button", { name: "Apply 2 changes" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "Receipts");
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Pick Digest two" })).not.toBeInTheDocument());
    expect(calls.some((c) => c.path === "/api/rule-jobs/9/matches" && c.url.searchParams.get("rule_id") === "6")).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Bill" }));
    await userEvent.click(screen.getByRole("button", { name: "Archive 1 mail" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [11, 13] }));
  });

  it("asks for the typed count only for picked offloads", async () => {
    const mixed = {
      ...JOB,
      rule_ids: [5, 7],
      per_rule: [
        { rule: "Junk", rule_id: 7, count: 2, actions: [{ kind: "offload" }], examples: [] },
        { rule: "Newsletters", rule_id: 5, count: 1, actions: [{ kind: "archive" }], examples: [] },
      ],
    };
    const JUNK = { rule_id: 7, rule_name: "Junk", actions: [{ kind: "offload" }] };
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": mixed,
      "GET /api/rule-jobs/9/matches": [match(13, "Junk one", JUNK), match(12, "Junk two", JUNK), match(11, "Digest one")],
      "POST /api/rule-jobs/9/run": { job_id: 9 },
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByLabelText("Type 2 to offload these mails")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 3 changes" })).toBeDisabled();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick Junk two" }));
    await userEvent.type(screen.getByLabelText("Type 1 to offload these mails"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Apply 2 changes" }));
    await waitFor(() => expect(runBody(calls)).toEqual({ account_id: ACCOUNT.id, left_out: [12], confirm: 1 }));
  });

  it("reopens a run with its match list, and Show more loads the next page", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => match(200 - i, `Digest ${200 - i}`));
    const { calls } = mockApi({
      "GET /api/rule-jobs/9": { ...JOB, planned: 51, per_rule: [{ ...JOB.per_rule[0], count: 51 }] },
      "GET /api/rule-jobs/9/matches": ({ url }: { url: URL }) => (url.searchParams.get("before_id") === "151" ? [match(7, "Digest 7")] : page1),
    });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByRole("checkbox", { name: "Pick Digest 151" })).toBeChecked();
    expect(calls.find((c) => c.path === "/api/rule-jobs/9/matches")?.url.searchParams.get("limit")).toBe("50");
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("checkbox", { name: "Pick Digest 7" })).toBeChecked();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive 51 mails" })).toBeEnabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("says when nothing fits, with Close", async () => {
    const none = { ...JOB, planned: 0, no_rule: 4, per_rule: [] };
    mockApi({ "GET /api/rule-jobs/9": none });
    const r = renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("No mail fits this rule.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    r.unmount();
    mockApi({ "GET /api/rule-jobs/9": { ...none, rule_ids: [5, 6] } });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("No mail fits these rules.")).toBeInTheDocument();
  });

  it("ends with applied, skipped and why, failed and left out", async () => {
    const reason = "It changed since the preview, so it was left as it is.";
    const done = { ...JOB, state: "DONE", planned: 0, applied: 1, skipped: 2, failed: 0, left_out: 3, skip_reasons: [{ reason, count: 2 }] };
    mockApi({ "GET /api/rule-jobs/9": done });
    const r = renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("Done. Applied 1, skipped 2, failed 0, left out 3.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Why mail was skipped" })).getByText(`${reason} (2)`)).toBeInTheDocument();
    r.unmount();
    mockApi({ "GET /api/rule-jobs/9": { ...done, state: "STOPPED", skipped: 1, left_out: 0, skip_reasons: [] } });
    renderApp(<RunRulesDialog account={ACCOUNT} jobId={9} onClose={() => {}} />);
    expect(await screen.findByText("Stopped. Applied 1, skipped 1, failed 0, left out 0.")).toBeInTheDocument();
  });
});
