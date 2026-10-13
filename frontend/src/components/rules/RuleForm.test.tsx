import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RuleAction, RuleDraft } from "../../api/rules";
import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
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
  it("fills from a draft, keeps Offload on its own, and saves the right body", async () => {
    const { calls } = mockApi({ "POST /api/rules": SAVED });
    const onSaved = vi.fn();
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={onSaved} onCancel={() => {}} />);
    expect(screen.getByLabelText("Name")).toHaveValue("Old newsletters");
    expect(screen.getByLabelText("Which mail is this for?")).toHaveValue("Newsletters I signed up for");
    expect(screen.getByRole("checkbox", { name: "Archive" })).toBeChecked();
    expect(screen.getByLabelText("From")).toHaveValue("news@example.com");
    expect(screen.getByLabelText("How these work with the description")).toHaveValue("fixed_then_ai");

    await userEvent.click(screen.getByRole("checkbox", { name: "Offload" }));
    expect(screen.getByRole("checkbox", { name: "Archive" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Star" })).not.toBeChecked();
    expect(screen.getByText(/Offload checks CleanSync's copy, moves the mail out of Gmail and keeps the copy in Offloaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    expect(screen.getByRole("checkbox", { name: "Offload" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Mark read" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Offload" }));

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
        actions: [{ kind: "offload" }],
        mode: "automatic",
        enabled: true,
      }),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("lists the actions in order, with Delete forever off until the mailbox allows it", () => {
    mockApi({ "GET /api/accounts": [ACCOUNT] });
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={() => {}} onCancel={() => {}} />);
    const what = screen.getByRole("group", { name: "What it does" });
    expect(within(what).getAllByRole("checkbox").map((c) => c.closest("label")?.textContent?.trim())).toEqual([
      "Archive",
      "Label",
      "Mark read",
      "Star",
      "Offload",
      "Delete forever",
    ]);
    const forever = within(what).getByRole("checkbox", { name: "Delete forever" });
    expect(forever).toBeDisabled();
    expect(forever).toHaveAccessibleDescription("Turn on Allow permanent delete in Settings → Accounts.");
    expect(within(what).queryByRole("checkbox", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("offers Delete forever once the mailbox allows it, on its own and always Suggest first", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [{ ...ACCOUNT, permanent_delete: true }], "POST /api/rules": SAVED });
    renderApp(<RuleForm accountId={1} initial={DRAFT} onSaved={() => {}} onCancel={() => {}} />);
    const forever = await screen.findByRole("checkbox", { name: "Delete forever" });
    await waitFor(() => expect(forever).toBeEnabled());
    await userEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    await userEvent.click(forever);
    expect(screen.getByRole("checkbox", { name: "Archive" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Automatic" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Automatic" })).toHaveAccessibleDescription("A Delete forever rule always suggests first.");
    expect(screen.getByRole("radio", { name: /Suggest first/ })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Star" }));
    expect(forever).not.toBeChecked();
    await userEvent.click(forever);
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/rules")?.body).toMatchObject({ actions: [{ kind: "delete_forever" }], mode: "suggest" }),
    );
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
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ conditions: {}, combine: "ai_only", actions: [{ kind: "label", label: "News" }] }));
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

  it("opens an old Automatic Delete forever rule as Suggest first, and saves it that way", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [{ ...ACCOUNT, permanent_delete: true }], "PUT /api/rules/5": SAVED });
    renderApp(
      <RuleForm accountId={1} ruleId={5} initial={{ ...DRAFT, actions: [{ kind: "delete_forever" }], mode: "automatic" }} onSaved={() => {}} onCancel={() => {}} />,
    );
    expect(screen.getByRole("radio", { name: /Suggest first/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Automatic" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Automatic" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ actions: [{ kind: "delete_forever" }], mode: "suggest" }));
  });

  it("keeps an Automatic rule that doesn't delete forever Automatic", () => {
    mockApi({});
    renderApp(<RuleForm accountId={1} ruleId={5} initial={{ ...DRAFT, mode: "automatic" }} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByRole("radio", { name: "Automatic" })).toBeChecked();
  });

  it("shows and saves an old rule action of kind delete as Offload", async () => {
    const { calls } = mockApi({ "PUT /api/rules/5": SAVED });
    renderApp(<RuleForm accountId={1} ruleId={5} initial={{ ...DRAFT, actions: [{ kind: "delete" } as unknown as RuleAction] }} onSaved={() => {}} onCancel={() => {}} />);
    expect(screen.getByRole("checkbox", { name: "Offload" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ actions: [{ kind: "offload" }] }));
  });
});
