import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { clearCsrfToken, getCsrfToken, setCsrfToken } from "../api/client";
import { progressStore } from "../hooks/live";
import { ACCOUNT, mockApi, PAIR, renderApp } from "../test/utils";
import { AccountSwitcher } from "./AccountSwitcher";
import { SideNav } from "./SideNav";
import { SyncIndicator } from "./SyncIndicator";

const STATUS = { accounts: 1, accounts_attention: 0, pairs: 1, new: 0, healthy: 1, attention: 0, running: 0, paused: 0, conflicts: 3 };
const STORAGE = {
  account_id: 1,
  email: ACCOUNT.email,
  drive: { total: 100 * 1024 ** 3, used: 40 * 1024 ** 3, trash: 1024 ** 3, checked_at: null },
  disk: { total: 1000 * 1024 ** 3, used: 400 * 1024 ** 3, free: 600 * 1024 ** 3 },
  synced_bytes: 30 * 1024 ** 3,
};

describe("sync indicator", () => {
  it("says everything is up to date", async () => {
    mockApi({ "GET /api/sync-pairs": [PAIR] });
    renderApp(<SyncIndicator />);
    expect(await screen.findByRole("button", { name: "Sync status: Up to date" })).toBeInTheDocument();
  });

  it("shows live file progress while a pair runs", async () => {
    mockApi({ "GET /api/sync-pairs": [{ ...PAIR, status: "RUNNING" }] });
    renderApp(<SyncIndicator />);
    act(() =>
      progressStore.set({
        7: { pair_id: 7, job_id: 1, bytes: 512, total_bytes: 1024, speed: 2048, eta: 1, transferring: [{ name: "big.iso", percentage: 50 }, { name: "b.txt", percentage: 10 }] },
      }),
    );
    const button = await screen.findByRole("button", { name: "Sync status: Syncing 2 files" });
    await userEvent.click(button);
    expect(screen.getByText("big.iso")).toBeInTheDocument();
    expect(screen.getByText("512 B of 1.0 KB")).toBeInTheDocument();
    act(() => progressStore.set({}));
  });

  it("points out pairs that need the person", async () => {
    mockApi({ "GET /api/sync-pairs": [{ ...PAIR, status: "ATTENTION" }, { ...PAIR, id: 8, status: "ERROR" }] });
    renderApp(<SyncIndicator />);
    expect(await screen.findByRole("button", { name: "Sync status: 1 with errors" })).toBeInTheDocument();
  });
});

describe("account switcher", () => {
  it("lists accounts with their state and switches", async () => {
    const bob = { ...ACCOUNT, id: 2, email: "bob@example.com", status: "NEEDS_REAUTH" as const };
    mockApi({ "GET /api/accounts": [ACCOUNT, bob] });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Account: alice@example.com" }));
    const menu = screen.getByRole("menu", { name: "Google accounts" });
    expect(within(menu).getByText("Needs reconnecting")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("menuitemradio", { name: /bob@example.com/ }));
    expect(localStorage.getItem("drivesync.account")).toBe("2");
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive"));
  });

  it("starts Google sign-in for a new account", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/accounts/connect": { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" } });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderApp(<AccountSwitcher />, { withAccount: true, route: "/drive?path=Work" });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Add Google account" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"));
    expect(sessionStorage.getItem("drivesync.oauthReturn")).toBe("/drive?path=Work");
  });
});

describe("sign out", () => {
  it("ends the session, forgets the CSRF token and goes to the login page", async () => {
    setCsrfToken("tok");
    const { calls } = mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/auth/logout": { ok: true } });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/login"));
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/auth/logout")).toBe(true);
    expect(getCsrfToken()).toBe("");
    clearCsrfToken();
  });
});

describe("side navigation", () => {
  it("has the Drive-style entries, a conflicts badge and storage bars", async () => {
    mockApi({ "GET /api/status": STATUS, "GET /api/accounts": [ACCOUNT], "GET /api/storage": STORAGE });
    renderApp(<SideNav />, { withAccount: true });
    for (const name of ["Home", "My Drive", "Recent", "Starred", "Sync activity", "Storage"]) {
      expect(screen.getByRole("link", { name: new RegExp(name) })).toBeInTheDocument();
    }
    expect(await screen.findByLabelText("3 open")).toBeInTheDocument();
    expect(screen.getByText("Offloaded")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Recovery")).toHaveAttribute("aria-disabled", "true");
    expect(await screen.findByText(/40 GB of 100 GB on Google Drive, 1.0 GB in trash/)).toBeInTheDocument();
    expect(screen.getByText(/600 GB free on this server, 30 GB synced here/)).toBeInTheDocument();
  });
});
