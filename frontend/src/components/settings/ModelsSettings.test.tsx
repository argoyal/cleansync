import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { mockApi, renderApp } from "../../test/utils";
import { ModelsSettings } from "./ModelsSettings";

const CONN = { id: 1, name: "Ollama", base_url: "http://ollama.example.com:11434/v1", has_key: false, header_names: [], resets_at: "00:00", resets_tz: "UTC", pause: null };
const TASKS = {
  sorting: { task: "sorting", connection_id: null, model: "", temperature: 0, timeout_s: 60 },
  writing_rules: { task: "writing_rules", connection_id: null, model: "", temperature: 0, timeout_s: 60 },
  unsubscribe: { task: "unsubscribe", connection_id: null, model: "", temperature: 0, timeout_s: 60 },
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
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Ollama", base_url: CONN.base_url, api_key: "sk-typed-1", headers: {}, resets_at: "00:00", resets_tz: "UTC" }));
  });

  it("warns that moving a connection to another server removes its saved key", async () => {
    mockApi({ "GET /api/ai/connections": [{ ...CONN, has_key: true }], "GET /api/ai/tasks": TASKS });
    renderApp(<ModelsSettings />);
    await userEvent.click(await screen.findByRole("button", { name: /Edit/ }));
    expect(screen.getByText(/Changing the server removes the saved key\./)).toBeInTheDocument();
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

  it("shows a paused connection with the server's message and resumes it", async () => {
    const pause = {
      kind: "limited",
      until: new Date(Date.now() + 86_400_000).toISOString(),
      reason: "The model's usage limit is used up.",
      detail: "Rate limit reached for requests per day",
      since: new Date().toISOString(),
      connection_id: 1,
      connection_name: "Ollama",
    };
    const { calls } = mockApi({
      "GET /api/ai/connections": [{ ...CONN, pause }],
      "GET /api/ai/tasks": TASKS,
      "POST /api/ai/connections/1/resume": CONN,
    });
    renderApp(<ModelsSettings />);
    expect(await screen.findByText(/Paused until/)).toBeInTheDocument();
    expect(screen.queryByText("Rate limit reached for requests per day")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show the server's message" }));
    expect(screen.getByText("Rate limit reached for requests per day")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resume now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/ai/connections/1/resume")).toBe(true));
  });

  it("saves the daily reset time and time zone", async () => {
    const { calls } = mockApi({ "GET /api/ai/connections": [CONN], "GET /api/ai/tasks": TASKS, "PATCH /api/ai/connections/1": CONN });
    renderApp(<ModelsSettings />);
    await userEvent.click(await screen.findByRole("button", { name: /Edit/ }));
    const at = screen.getByLabelText("Daily limit resets at");
    expect(at).toHaveAttribute("type", "time");
    fireEvent.change(at, { target: { value: "09:30" } });
    await userEvent.clear(screen.getByLabelText("Time zone"));
    await userEvent.type(screen.getByLabelText("Time zone"), "Asia/Kolkata");
    await userEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ resets_at: "09:30", resets_tz: "Asia/Kolkata" }));
  });

  it("shows the server's message when a test fails", async () => {
    mockApi({
      "GET /api/ai/connections": [CONN],
      "GET /api/ai/tasks": { ...TASKS, sorting: { ...TASKS.sorting, connection_id: 1, model: "m" } },
      "POST /api/ai/test": { ok: false, took_ms: 10, mode: null, chose: null, why: "", error: "The model's usage limit is used up.", detail: "Rate limit reached" },
    });
    renderApp(<ModelsSettings />);
    const block = await screen.findByRole("group", { name: "Sorting" });
    await userEvent.click(within(block).getByRole("button", { name: "Test" }));
    expect(await within(block).findByText(/usage limit is used up\. The server said: Rate limit reached/)).toBeInTheDocument();
  });

  it("has an Unsubscribe task that says what it does and its fallback", async () => {
    mockApi({ "GET /api/ai/connections": [CONN], "GET /api/ai/tasks": TASKS });
    renderApp(<ModelsSettings />);
    const block = await screen.findByRole("group", { name: "Unsubscribe" });
    expect(block).toHaveTextContent("Checks the page after an unsubscribe form. Uses the Sorting model when not set.");
    const names = screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(names).toEqual(["Sorting", "Writing rules", "Unsubscribe"]);
  });
});
