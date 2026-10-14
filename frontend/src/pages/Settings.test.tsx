import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setTheme } from "../lib/theme";
import { AppRoutes } from "../routes";
import { ACCOUNT, json, mockApi, PAIR, renderApp } from "../test/utils";
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
  app_name: "CleanSync",
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

  it("turns Allow permanent delete on through Google's sign-in", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "active" } }],
      "POST /api/accounts/1/permanent-delete": { auth_url: "https://accounts.google.com/full" },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow permanent delete" });
    expect(box).toHaveTextContent("Off");
    expect(box).toHaveTextContent(
      "Offload moves mail to Gmail's trash. Turn this on to delete it from Gmail directly, so the space comes back at once.",
    );
    await userEvent.click(within(box).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/full"));
    expect(calls.find((c) => c.path === "/api/accounts/1/permanent-delete")?.body).toEqual({ on: true });
  });

  it("says what On means, how to take it back at Google, and turns it off", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "active" }, permanent_delete: true }],
      "POST /api/accounts/1/permanent-delete": { auth_url: null },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow permanent delete" });
    expect(box).toHaveTextContent("On");
    expect(box).toHaveTextContent("Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.");
    expect(box).toHaveTextContent("myaccount.google.com/permissions");
    await userEvent.click(within(box).getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/accounts/1/permanent-delete")?.body).toEqual({ on: false }));
  });

  it("shows why permanent delete went off, and nothing for a mailbox without mail backup", async () => {
    mockApi({
      "GET /api/accounts": [
        {
          ...ACCOUNT,
          services: { files: "active", mail: "active" },
          permanent_delete_note: "Permanent delete was turned off: Google no longer allows it for this mailbox.",
        },
        { ...ACCOUNT, id: 2, email: "bob@example.com" },
      ],
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    expect(await screen.findByText("Permanent delete was turned off: Google no longer allows it for this mailbox.")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "Allow permanent delete" })).toHaveLength(1);
  });

  it("says what Google answered after the sign-in for permanent delete", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1&permanent=denied" });
    expect(await screen.findByText("Google didn't give full Gmail access, so permanent delete stays off. Try again and tick the box for it.")).toBeInTheDocument();
  });
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

  it("tells you when a storage check fails", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/accounts/1/quota": () => json(502, { detail: "Google did not answer." }) });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    await userEvent.click(await screen.findByRole("button", { name: "Check storage" }));
    expect(await screen.findByText("Google did not answer.")).toBeInTheDocument();
  });

  it("offers Retry when the system details fail", async () => {
    let fail = true;
    mockApi({ "GET /api/system": () => (fail ? json(500, { detail: "boom" }) : SYSTEM) });
    renderApp(<SettingsPage />, { route: "/settings?tab=system", path: "/settings" });
    const retry = await screen.findByRole("button", { name: "Retry" });
    fail = false;
    await userEvent.click(retry);
    expect(await screen.findByText("rclone v1.75.1")).toBeInTheDocument();
  });

  it("offers the theme switch under General", async () => {
    mockApi({ "GET /api/system": SYSTEM, "GET /api/settings": SETTINGS });
    renderApp(<SettingsPage />, { route: "/settings?tab=general", path: "/settings" });
    await userEvent.click(within(await screen.findByRole("radiogroup", { name: "Theme" })).getByRole("radio", { name: "Light" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("shows a theme picked elsewhere", async () => {
    mockApi({ "GET /api/system": SYSTEM, "GET /api/settings": SETTINGS });
    renderApp(<SettingsPage />, { route: "/settings?tab=general", path: "/settings" });
    const group = await screen.findByRole("radiogroup", { name: "Theme" });
    act(() => setTheme("dark"));
    expect(within(group).getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  });

  it("treats an unknown tab as General", async () => {
    mockApi({ "GET /api/system": SYSTEM, "GET /api/settings": SETTINGS });
    renderApp(<SettingsPage />, { route: "/settings?tab=constructor", path: "/settings" });
    expect(await screen.findByRole("link", { name: "General" })).toHaveAttribute("aria-current", "page");
  });

  it("shows system details and the watcher state", async () => {
    mockApi({ "GET /api/system": SYSTEM });
    renderApp(<SettingsPage />, { route: "/settings?tab=system", path: "/settings" });
    expect(await screen.findByText("rclone v1.75.1")).toBeInTheDocument();
    expect(screen.getByText("Limited: changes are found by the schedule")).toBeInTheDocument();
  });
});

describe("storage page", () => {
  it("says so when there are no accounts", async () => {
    mockApi({ "GET /api/accounts": [] });
    renderApp(<StoragePage />);
    expect(await screen.findByText("No Google account yet")).toBeInTheDocument();
  });

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
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 0, pairs: 0, initialized_pairs: 0 },
    });
    renderApp(<AppRoutes />, { route: "/" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup"));
  });

  it("still opens the wizard's folder step on an account after setup is done", async () => {
    const second = { ...ACCOUNT, id: 2, email: "bob@example.com" };
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT, second],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 2, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/setup?step=folders&account=2" });
    expect(await screen.findByText("Choose what to sync")).toBeInTheDocument();
    expect(screen.getByLabelText("Google account")).toHaveValue("2");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup?step=folders&account=2");
  });

  it("shows a sign-in error as plain text and clears the address", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
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

  it.each([["//evil.example.com/x"], ["https://evil.example.com"]])("ignores an unsafe saved return path %s", async (saved) => {
    sessionStorage.setItem("drivesync.oauthReturn", saved);
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1" });
    expect(await screen.findByText("Google account connected.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/$/));
  });

  it("says the mail permission was added, and goes back to Mail", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/mail");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1&mail=ready" });
    expect(await screen.findByText("Mail permission added. Next, start the mail backup.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail$/));
  });

  it("says plainly when Google gave no mail permission", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/mail");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: "/?connected=1&mail=denied" });
    expect(await screen.findByText(/Google didn't give permission to read mail/)).toBeInTheDocument();
  });

  it("ignores a connected value that is not a positive whole number and caps long errors", async () => {
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
    });
    renderApp(<AppRoutes />, { route: `/?connected=abc&oauth_error=${"x".repeat(500)}` });
    expect(await screen.findByText("x".repeat(300))).toBeInTheDocument();
    expect(screen.queryByText("Google account connected.")).not.toBeInTheDocument();
  });

  it("returns from Google sign-in to where the person started", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
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

describe("setup wizard skip", () => {
  it("Skip for now goes to My Drive and Home stops sending you back this session", async () => {
    mockApi({
      "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [],
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [] },
      "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 0, initialized_pairs: 0 },
    });
    renderApp(<AppRoutes />, { route: "/setup" });
    await userEvent.click(await screen.findByRole("link", { name: "Skip for now" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/drive$/));
    await userEvent.click(screen.getByRole("link", { name: "CleanSync home" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/$/));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/$/);
  });
});

const MAIL_SETTINGS = { ...SETTINGS, mail_check_interval_s: 300, mail_min_age_days: 30, mail_disk_floor_gb: 2, google_daily_budget_units: 10_000_000 };

describe("mail and Google API settings", () => {
  it("changes how often mail is checked and the offload and disk limits", async () => {
    const { calls } = mockApi({ "GET /api/settings": MAIL_SETTINGS, "PUT /api/settings": ({ body }: { body: object }) => ({ ...MAIL_SETTINGS, ...body }) });
    renderApp(<SettingsPage />, { route: "/settings?tab=mail", path: "/settings" });
    await userEvent.selectOptions(await screen.findByLabelText("Check for new mail"), "900");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ mail_check_interval_s: 900 }));
    const age = screen.getByLabelText("Never offload mail newer than (days)");
    await userEvent.clear(age);
    await userEvent.type(age, "60{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").at(-1)?.body).toEqual({ mail_min_age_days: 60 }));
    expect(screen.getByLabelText("Pause the backup when free space drops below (GB)")).toHaveValue("2");
  });

  it("shows Google API units used today, the Drive estimate, and the budget", async () => {
    mockApi({
      "GET /api/settings": MAIL_SETTINGS,
      "GET /api/google/usage": { day: "2026-10-10", used: 1_250_000, budget: 10_000_000, drive_estimate: 4_300, reached: false },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=google", path: "/settings" });
    expect(await screen.findByText("1,250,000 of 10,000,000 units used today (UTC).")).toBeInTheDocument();
    expect(screen.getByText("Drive syncs, estimated: about 4,300 units today. They count toward the budget but are never held back.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Google API units used today" })).toHaveAttribute("aria-valuenow", "13");
    expect(screen.getByLabelText("Daily budget (units)")).toHaveValue("10000000");
  });

  it("caps the daily budget at half of Google's 80,000,000 paid line", async () => {
    mockApi({
      "GET /api/settings": MAIL_SETTINGS,
      "GET /api/google/usage": { day: "2026-10-10", used: 0, budget: 10_000_000, drive_estimate: 0, reached: false },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=google", path: "/settings" });
    const input = await screen.findByLabelText("Daily budget (units)");
    await userEvent.clear(input);
    await userEvent.type(input, "40000001");
    expect(screen.getByText("Use a whole number from 1 to 40000000.")).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "40000000");
    expect(screen.queryByText(/Use a whole number/)).not.toBeInTheDocument();
  });

  it("says when mail waits for the next UTC day", async () => {
    mockApi({
      "GET /api/settings": MAIL_SETTINGS,
      "GET /api/google/usage": { day: "2026-10-10", used: 10_000_000, budget: 10_000_000, drive_estimate: 0, reached: true },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=google", path: "/settings" });
    expect(await screen.findByText("10,000,000 of 10,000,000 units used today (UTC). Mail jobs are paused until midnight UTC.")).toBeInTheDocument();
  });
});
