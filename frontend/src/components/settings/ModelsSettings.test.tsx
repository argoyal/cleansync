import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { mockApi, renderApp } from "../../test/utils";
import { ModelsSettings } from "./ModelsSettings";

const CONN = { id: 1, name: "Ollama", base_url: "http://ollama.example.com:11434/v1", has_key: false, header_names: [] };
const TASKS = {
  sorting: { task: "sorting", connection_id: null, model: "", temperature: 0, timeout_s: 60 },
  writing_rules: { task: "writing_rules", connection_id: null, model: "", temperature: 0, timeout_s: 60 },
};

describe("Settings → Models", () => {
  it("adds a connection without ever showing the key again", async () => {
    const { calls } = mockApi({ "GET /api/ai/connections": [], "GET /api/ai/tasks": TASKS, "POST /api/ai/connections": { ...CONN, has_key: true } });
    renderApp(<ModelsSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    await userEvent.type(screen.getByLabelText("Name"), "Ollama");
    await userEvent.type(screen.getByLabelText("Address"), CONN.base_url);
    await userEvent.type(screen.getByLabelText(/API key/), "sk-typed-1");
    await userEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Ollama", base_url: CONN.base_url, api_key: "sk-typed-1", headers: {} }));
  });

  it("lists models, saves the Sorting model and tests it", async () => {
    const { calls } = mockApi({
      "GET /api/ai/connections": [CONN],
      "GET /api/ai/tasks": TASKS,
      "GET /api/ai/connections/1/models": { models: ["llama3.1:8b", "qwen2.5:7b"] },
      "PUT /api/ai/tasks/sorting": { ...TASKS.sorting, connection_id: 1, model: "llama3.1:8b" },
      "POST /api/ai/test": { ok: true, took_ms: 1200, mode: "json_schema", chose: "Newsletters", why: "a digest", error: null },
    });
    renderApp(<ModelsSettings />);
    const block = await screen.findByRole("group", { name: "Sorting" });
    await userEvent.selectOptions(within(block).getByLabelText("Connection"), "Ollama");
    await userEvent.click(within(block).getByRole("button", { name: "List models" }));
    await userEvent.type(within(block).getByLabelText("Model"), "llama3.1:8b");
    await userEvent.click(within(block).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ connection_id: 1, model: "llama3.1:8b" }));
    await userEvent.click(within(block).getByRole("button", { name: "Test" }));
    expect(await within(block).findByText(/picked Newsletters/)).toBeInTheDocument();
  });
});
