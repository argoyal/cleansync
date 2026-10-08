import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../routes";
import { ACCOUNT, mockApi, PAIR, renderApp } from "../test/utils";
import { SettingsPage } from "./SettingsPage";
import { StoragePage } from "./StoragePage";

const SETTINGS = {
  default_interval_s: 300,
  default_max_deletions: 10,
  default_backups_enabled: true,
  default_backup_retention_days: 30,
  watcher_debounce_s: 5,
  max_parallel_pairs: 2,
  session_idle_hours: 12,
  log_retention_days: 30,
};
const SYSTEM = {
  app_name: "DriveSync",
  app_version: "0.1.0",
  python_version: "3.12.9",
  rclone_version: "rclone v1.75.1",
  storage_root: "/drivesync/storage",
  data_dir: "/drivesync/app/data",
  watcher: "limited",
  db_ok: true,
  insecure_http: false,
  public_url: "https://drivesync.example.com",
};

describe("settings", () => {
  it("reconnects and disconnects accounts", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, status: "NEEDS_REAUTH" }],
      "POST /api/accounts/1/reconnect": { auth_url: "https://accounts.google.com/r" },
      "POST /api/accounts/1/disconnect": { ...ACCOUNT, status: "DISCONNECTED" },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    expect(await screen.findByText(/Needs reconnecting/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/r"));
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("No files are deleted");
    await userEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/accounts/1/disconnect")).toBe(true));
  });

  it("saves sync defaults", async () => {
    const { calls } = mockApi({ "GET /api/settings": SETTINGS, "PUT /api/settings": { ...SETTINGS, default_interval_s: 900 } });
    renderApp(<SettingsPage />, { route: "/settings?tab=sync", path: "/settings" });
    await userEvent.selectOptions(await screen.findByLabelText("How often to sync"), "Every 15 minutes");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ default_interval_s: 900 }));
    const limit = screen.getByLabelText(/Deletion limit per sync/);
    await userEvent.clear(limit);
    await userEvent.type(limit, "25");
    await userEvent.click(within(limit.closest("form") as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ default_max_deletions: 25 }));
  });

  it("refuses numbers that are not whole or out of range", async () => {
    const { calls } = mockApi({ "GET /api/settings": SETTINGS });
    renderApp(<SettingsPage />, { route: "/settings?tab=sync", path: "/settings" });
    const limit = await screen.findByLabelText(/Deletion limit per sync/);
    await userEvent.clear(limit);
    await userEvent.type(limit, "2.5");
    expect(screen.getByText("Use a whole number from 0 to 1000000.")).toBeInTheDocument();
    expect(within(limit.closest("form") as HTMLElement).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("shows system details and the watcher state", async () => {
    mockApi({ "GET /api/system": SYSTEM });
    renderApp(<SettingsPage />, { route: "/settings?tab=system", path: "/settings" });
    expect(await screen.findByText("rclone v1.75.1")).toBeInTheDocument();
    expect(screen.getByText("Limited: changes are found by the schedule")).toBeInTheDocument();
  });
});

describe("storage page", () => {
  it("shows Drive quota and disk use per account", async () => {
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/storage": {
        account_id: 1,
        email: ACCOUNT.email,
        drive: { total: 15 * 1024 ** 3, used: 6 * 1024 ** 3, trash: 1024 ** 3, checked_at: null },
        disk: { total: 2000 * 1024 ** 3, used: 500 * 1024 ** 3, free: 1500 * 1024 ** 3 },
        synced_bytes: 5 * 1024 ** 3,
      },
    });
    renderApp(<StoragePage />);
    expect(await screen.findByText(/6.0 GB of 15 GB on Google Drive, 1.0 GB in trash/)).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
  });
});

describe("routes", () => {
  it("sends a new installation from Home to the wizard", async () => {
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "DriveSync" },
      "GET /api/accounts": [],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 0, pairs: 0, initialized_pairs: 0 },
    });
    renderApp(<AppRoutes />, { route: "/" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup"));
  });

  it("shows a sign-in error as plain text and clears the address", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "DriveSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/settings": SETTINGS,
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?oauth_error=%3Cb%3Eboom%3C%2Fb%3E" });
    expect(await screen.findByText("<b>boom</b>")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/settings?tab=accounts"));
    expect(screen.getAllByTestId("location")[0]).not.toHaveTextContent("oauth_error");
    expect(sessionStorage.getItem("drivesync.oauthReturn")).toBeNull();
  });

  it("returns from Google sign-in to where the person started", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "DriveSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": { accounts: 1, accounts_attention: 0, pairs: 1, new: 0, healthy: 1, attention: 0, running: 0, paused: 0, conflicts: 0 },
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/settings?tab=accounts"));
    expect(await screen.findByText("Google account connected.")).toBeInTheDocument();
    expect(localStorage.getItem("drivesync.account")).toBe("1");
  });
});
