import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

describe("first-run wizard", () => {
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
});
