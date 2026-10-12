import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RuleDraft } from "../../api/rules";
import { json, mockApi, renderApp } from "../../test/utils";
import { RuleForm } from "./RuleForm";

const DRAFT: RuleDraft = {
  name: "Old newsletters",
  description: "Newsletters I signed up for",
  conditions: { senders_in: ["news@example.com"] },
  combine: "fixed_then_ai",
  actions: [{ kind: "archive" }, { kind: "star" }],
  notes: [],
};
const SAVED = { ...DRAFT, mode: "suggest", enabled: true, id: 5, position: 0, version: 1, decided_7d: 0, senders: [] };

describe("RuleForm", () => {
  it("fills from a draft, keeps Delete on its own, and saves the right body", async () => {
    const { calls } = mockApi({ "POST /api/rules": SAVED });
    const onSaved = vi.fn();
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={onSaved} onCancel={() => {}} />);
    expect(screen.getByLabelText("Name")).toHaveValue("Old newsletters");
    expect(screen.getByLabelText("Which mail is this for?")).toHaveValue("Newsletters I signed up for");
    expect(screen.getByRole("checkbox", { name: "Archive" })).toBeChecked();
    expect(screen.getByLabelText("From")).toHaveValue("news@example.com");
    expect(screen.getByLabelText("How these work with the description")).toHaveValue("fixed_then_ai");

    await userEvent.click(screen.getByRole("checkbox", { name: "Delete" }));
    expect(screen.getByRole("checkbox", { name: "Archive" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Star" })).not.toBeChecked();
    expect(screen.getByText(/Delete moves mail to Gmail's trash; CleanSync keeps it in the Local archive/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    expect(screen.getByRole("checkbox", { name: "Delete" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Delete" }));

    await userEvent.clear(screen.getByLabelText("From"));
    await userEvent.type(screen.getByLabelText("From"), " a@example.com , ,b.example.com ");
    await userEvent.type(screen.getByLabelText("Subject contains"), "digest, weekly");
    await userEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/rules")?.body).toEqual({
        account_id: 1,
        name: "Old newsletters",
        description: "Newsletters I signed up for",
        conditions: { senders_in: ["a@example.com", "b.example.com"], subject_contains: ["digest", "weekly"] },
        combine: "fixed_then_ai",
        actions: [{ kind: "delete" }],
        mode: "automatic",
        enabled: true,
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("hides how conditions combine until one is filled, and sends ai_only without any", async () => {
    const { calls } = mockApi({ "PUT /api/rules/5": SAVED });
    renderApp(
      <RuleForm accountId={1} ruleId={5} initial={{ ...DRAFT, conditions: {}, combine: "ai_only", actions: [{ kind: "label", label: "News" }] }} onSaved={() => {}} onCancel={() => {}} />,
    );
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument(); // collapsed
    await userEvent.click(screen.getByRole("button", { name: "Fixed conditions" }));
    expect(screen.queryByLabelText("How these work with the description")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Not from"), "x.example.com");
    expect(screen.getByLabelText("How these work with the description")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Not from"));
    expect(screen.getByLabelText("Label name")).toHaveValue("News");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls[0]?.body).toMatchObject({ conditions: {}, combine: "ai_only", actions: [{ kind: "label", label: "News" }] }));
  });

  it("shows the server's refusal next to Save, and a slot for trying the rule", async () => {
    mockApi({ "POST /api/rules": json(400, { detail: "Give the rule a name." }) });
    renderApp(
      <RuleForm accountId={1} initial={{ ...DRAFT, name: "" }} onSaved={() => {}} onCancel={() => {}} tryPanel={(rule) => <p>Trying {rule.description}</p>} />,
    );
    expect(screen.getByText("Trying Newsletters I signed up for")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Give the rule a name.");
  });

  it("switches Only the AI to narrowing when the first fixed condition is added, and then leaves the choice alone", async () => {
    mockApi({});
    renderApp(<RuleForm accountId={1} initial={{ ...DRAFT, conditions: {}, combine: "ai_only" }} onSaved={() => {}} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Fixed conditions" }));
    await userEvent.type(screen.getByLabelText("Not from"), "x");
    const how = screen.getByLabelText("How these work with the description");
    expect(how).toHaveValue("fixed_then_ai");
    await userEvent.selectOptions(how, "ai_only");
    await userEvent.type(screen.getByLabelText("Subject contains"), "digest");
    expect(screen.getByLabelText("How these work with the description")).toHaveValue("ai_only");
  });
});
