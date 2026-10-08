import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";


import { CONFIRM_PHRASE } from "../config";
import { ACCOUNT, mockApi, PAIR, renderApp } from "../test/utils";
import { SetupWizard } from "./SetupWizard";

const SETUP = {
  oauth_configured: true,
  redirect_uri: "https://drivesync.example.com/api/oauth/callback",
  public_url: "https://drivesync.example.com",
  insecure_http: false,
  accounts: 0,
  pairs: 0,
  initialized_pairs: 0,
};
const HEALTH = { "GET /api/health": { status: "ok", version: "0.1.0", app_name: "DriveSync" } };
const PREVIEW = {
  job_id: 3,
  created_at: "2026-10-08T08:00:00Z",
  same: 120,
  only_local: 4,
  only_remote: 30,
  different: 2,
  errors: 0,
  samples: { only_local: ["a.txt"], only_remote: ["b.txt"], different: ["c.txt", "d.txt"], errors: [] },
};

const job = (over: Record<string, unknown>) => ({
  id: 1,
  pair_id: 11,
  kind: "PREVIEW",
  trigger: "INITIAL",
  state: "SUCCESS",
  error_message: null,
  ...over,
});
const NEW_PAIR = { ...PAIR, id: 11, initialized: false, status: "NEW" };
function renderWith(routes: Record<string, unknown>, route = "/setup?step=preview&pair=11") {
  const api = mockApi({ ...HEALTH, "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs/11": NEW_PAIR, ...routes });
  return { ...api, ...renderApp(<SetupWizard />, { route, path: "/setup" }) };
}
const none = () => new Response(JSON.stringify({ detail: "no preview yet" }), { status: 404 });

describe("first-run wizard", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the redirect address to paste and starts Google sign-in", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    mockApi({ ...HEALTH, "GET /api/setup": SETUP, "GET /api/accounts": [], "POST /api/accounts/connect": { auth_url: "https://accounts.google.com/x" } });
    renderApp(<SetupWizard />, { route: "/setup", path: "/setup" });
    expect(await screen.findByText("https://drivesync.example.com/api/oauth/callback")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign in with Google" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/x"));
    expect(sessionStorage.getItem("drivesync.oauthReturn")).toBe("/setup");
  });

  it("says what to set when no Google client is configured", async () => {
    mockApi({ ...HEALTH, "GET /api/setup": { ...SETUP, oauth_configured: false }, "GET /api/accounts": [] });
    renderApp(<SetupWizard />, { route: "/setup", path: "/setup" });
    expect(await screen.findByText(/Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in with Google" })).toBeDisabled();
  });

  it("creates the pair from the two folder pickers", async () => {
    const { calls } = mockApi({
      ...HEALTH,
      "GET /api/accounts": [ACCOUNT],
      "GET /api/remote-browse": ({ url }: { url: URL }) => (url.searchParams.get("path") ? [] : [{ name: "Work", path: "Work" }]),
      "GET /api/local-browse": [],
      "POST /api/sync-pairs": { ...PAIR, initialized: false, status: "NEW", id: 11 },
    });
    renderApp(<SetupWizard />, { route: "/setup", path: "/setup" });
    await userEvent.click(await screen.findByRole("button", { name: "Work" }));
    expect(screen.getByText(/stay in step with/)).toHaveTextContent("Google Drive Work will stay in step with alice@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Create and preview" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup?step=preview&pair=11"));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ account_id: 1, name: "My Drive", remote_path: "Work", local_subpath: "" });
  });

  it("previews, then needs the exact phrase before starting", async () => {
    const { calls } = mockApi({
      ...HEALTH,
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs/11": { ...PAIR, id: 11, initialized: false, status: "NEW" },
      "GET /api/sync-pairs/11/preview": PREVIEW,
      "POST /api/sync-pairs/11/confirm-initial": { result: "queued" },
    });
    renderApp(<SetupWizard />, { route: "/setup?step=preview&pair=11", path: "/setup" });
    expect(await screen.findByText("Only on Drive (download)")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "Start the first sync" });
    await userEvent.type(screen.getByLabelText(/To start, type/), "I understand");
    expect(start).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/To start, type/));
    await userEvent.type(screen.getByLabelText(/To start, type/), CONFIRM_PHRASE);
    await userEvent.click(start);
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("step=start"));
    expect(calls.find((c) => c.path.endsWith("confirm-initial"))?.body).toEqual({ phrase: CONFIRM_PHRASE });
  });

  it("starts the comparison when there is no preview yet", async () => {
    const { calls } = mockApi({
      ...HEALTH,
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs/11": { ...PAIR, id: 11, initialized: false, status: "NEW" },
      "GET /api/sync-pairs/11/preview": () => new Response(JSON.stringify({ detail: "no preview yet" }), { status: 404 }),
      "POST /api/sync-pairs/11/preview": { result: "queued" },
    });
    renderApp(<SetupWizard />, { route: "/setup?step=preview&pair=11", path: "/setup" });
    await userEvent.click(await screen.findByRole("button", { name: "Compare the folders" }));
    expect(await screen.findByText(/Comparing/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/sync-pairs/11/preview")).toBe(true);
  });

  it("refuses to start when the comparison had errors", async () => {
    mockApi({
      ...HEALTH,
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs/11": { ...PAIR, id: 11, initialized: false, status: "NEW" },
      "GET /api/sync-pairs/11/preview": { ...PREVIEW, errors: 2, samples: { ...PREVIEW.samples, errors: ["e.txt"] } },
    });
    renderApp(<SetupWizard />, { route: "/setup?step=preview&pair=11", path: "/setup" });
    expect(await screen.findByText(/will refuse to start/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/To start, type/), CONFIRM_PHRASE);
    expect(screen.getByRole("button", { name: "Start the first sync" })).toBeDisabled();
  });

  it("resumes a comparison that was already running when the page loaded", async () => {
    mockApi({
      ...HEALTH,
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs/11": NEW_PAIR,
      "GET /api/sync-pairs/11/history": [job({ id: 5, state: "RUNNING" })],
      "GET /api/sync-pairs/11/preview": none,
    });
    renderApp(<SetupWizard />, { route: "/setup?step=preview&pair=11", path: "/setup" });
    expect(await screen.findByText(/Comparing/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Compare the folders" })).not.toBeInTheDocument();
  });

  it("moves from comparing to the result when the job finishes", async () => {
    let done = false;
    const { client } = renderWith({
      "GET /api/sync-pairs/11/history": () => [job({ id: 5, state: done ? "SUCCESS" : "RUNNING" })],
      "GET /api/sync-pairs/11/preview": () => (done ? PREVIEW : none()),
    });
    expect(await screen.findByText(/Comparing/)).toBeInTheDocument();
    done = true;
    await client.invalidateQueries();
    expect(await screen.findByText("Only on Drive (download)")).toBeInTheDocument();
    expect(screen.queryByText(/Comparing/)).not.toBeInTheDocument();
  });

  it("compares again, hiding the old result while it runs", async () => {
    const { calls } = renderWith({
      "GET /api/sync-pairs/11/history": [job({ id: 5 })],
      "GET /api/sync-pairs/11/preview": PREVIEW,
      "POST /api/sync-pairs/11/preview": { result: "queued" },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Compare again" }));
    expect(await screen.findByText(/Comparing/)).toBeInTheDocument();
    expect(screen.queryByText("Only on Drive (download)")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/sync-pairs/11/preview")).toBe(true);
  });

  it("treats a busy answer as already comparing, not as a failure", async () => {
    renderWith({
      "GET /api/sync-pairs/11/history": [],
      "GET /api/sync-pairs/11/preview": none,
      "POST /api/sync-pairs/11/preview": () => new Response(JSON.stringify({ detail: "this pair is busy; try again" }), { status: 409 }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Compare the folders" }));
    expect(await screen.findByText(/Already comparing/)).toBeInTheDocument();
    expect(screen.queryByText(/this pair is busy/)).not.toBeInTheDocument();
  });

  it("shows the 503 message when syncing is switched off", async () => {
    renderWith({
      "GET /api/sync-pairs/11/history": [],
      "GET /api/sync-pairs/11/preview": none,
      "POST /api/sync-pairs/11/preview": () => new Response(JSON.stringify({ detail: "database integrity check failed; restore a backup (see docs/backup-recovery.md)" }), { status: 503 }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Compare the folders" }));
    expect(await screen.findByText(/restore a backup/)).toBeInTheDocument();
  });

  it("shows the failed comparison's own error, not a stale pair error", async () => {
    renderWith({
      "GET /api/sync-pairs/11": { ...NEW_PAIR, last_error: "old unrelated error" },
      "GET /api/sync-pairs/11/history": [job({ id: 5, state: "FAILED", error_message: "Google refused the listing" })],
      "GET /api/sync-pairs/11/preview": none,
    });
    expect(await screen.findByText("Google refused the listing")).toBeInTheDocument();
    expect(screen.queryByText("old unrelated error")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare again" })).toBeInTheDocument();
  });

  it("stops polling and shows the error when the preview fails for a reason other than 404", async () => {
    const { calls } = renderWith({
      "GET /api/sync-pairs/11/history": [job({ id: 5, state: "RUNNING" })],
      "GET /api/sync-pairs/11/preview": () => new Response(JSON.stringify({ detail: "preview file is damaged" }), { status: 500 }),
    });
    expect(await screen.findByText("preview file is damaged")).toBeInTheDocument();
    const before = calls.filter((c) => c.path.endsWith("/preview")).length;
    await new Promise((r) => setTimeout(r, 2500));
    expect(calls.filter((c) => c.path.endsWith("/preview")).length).toBe(before);
  });

  it("shows the server's message when the phrase is refused", async () => {
    renderWith({
      "GET /api/sync-pairs/11/history": [job({ id: 5 })],
      "GET /api/sync-pairs/11/preview": PREVIEW,
      "POST /api/sync-pairs/11/confirm-initial": () => new Response(JSON.stringify({ detail: "The phrase does not match." }), { status: 422 }),
    });
    await userEvent.type(await screen.findByLabelText(/To start, type/), CONFIRM_PHRASE);
    await userEvent.click(screen.getByRole("button", { name: "Start the first sync" }));
    expect(await screen.findByText("The phrase does not match.")).toBeInTheDocument();
  });

  it("moves focus to the heading of each step", async () => {
    renderWith({ "GET /api/sync-pairs/11/history": [], "GET /api/sync-pairs/11/preview": none });
    await screen.findByRole("button", { name: "Compare the folders" });
    expect(screen.getByRole("heading", { name: "Check before anything moves" })).toHaveFocus();
  });

  it("goes back to the folders step for a missing or garbled pair", async () => {
    mockApi({ ...HEALTH, "GET /api/accounts": [ACCOUNT], "GET /api/remote-browse": [], "GET /api/local-browse": [] });
    renderApp(<SetupWizard />, { route: "/setup?step=preview&pair=abc", path: "/setup" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup?step=folders"));
  });

  it("goes back to the folders step when the pair does not exist", async () => {
    mockApi({ ...HEALTH, "GET /api/accounts": [ACCOUNT], "GET /api/remote-browse": [], "GET /api/local-browse": [] });
    renderApp(<SetupWizard />, { route: "/setup?step=start&pair=99", path: "/setup" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup?step=folders"));
  });

  it("says Synced only after the first sync finished, and focuses the status", async () => {
    renderWith(
      {
        "GET /api/sync-pairs/11": { ...NEW_PAIR, status: "RUNNING" },
        "GET /api/sync-pairs/11/history": [job({ id: 8, kind: "RESYNC", state: "RUNNING" })],
      },
      "/setup?step=start&pair=11",
    );
    expect(await screen.findByText("First sync running")).toBeInTheDocument();
    expect(screen.queryByText("Synced")).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "First sync status" })).toHaveFocus();
  });

  it("shows the failed first sync instead of Synced", async () => {
    renderWith(
      {
        "GET /api/sync-pairs/11": { ...NEW_PAIR, status: "ERROR" },
        "GET /api/sync-pairs/11/history": [job({ id: 8, kind: "RESYNC", state: "FAILED", error_message: "Drive ran out of space" })],
      },
      "/setup?step=start&pair=11",
    );
    expect(await screen.findByText("Drive ran out of space")).toBeInTheDocument();
    expect(screen.queryByText("Synced")).not.toBeInTheDocument();
  });

  it("says Synced once the pair is initialized", async () => {
    renderWith({ "GET /api/sync-pairs/11": { ...PAIR, id: 11 }, "GET /api/sync-pairs/11/history": [job({ id: 8, kind: "RESYNC" })] }, "/setup?step=start&pair=11");
    expect(await screen.findByText("Synced")).toBeInTheDocument();
  });

  it("copies the redirect address and says so, or says it failed", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    mockApi({ ...HEALTH, "GET /api/setup": SETUP, "GET /api/accounts": [] });
    renderApp(<SetupWizard />, { route: "/setup", path: "/setup" });
    fireEvent.click(await screen.findByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(write).toHaveBeenCalledWith(SETUP.redirect_uri);
    write.mockRejectedValue(new Error("denied"));
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });
});
