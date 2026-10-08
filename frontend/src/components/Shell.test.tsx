import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { clearCsrfToken, getCsrfToken, setCsrfToken } from "../api/client";
import { progressStore } from "../hooks/live";
import { ACCOUNT, json, mockApi, PAIR, renderApp } from "../test/utils";
import { AppShell } from "./AppShell";
import { AccountSwitcher } from "./AccountSwitcher";
import { SideNav } from "./SideNav";
import { SyncIndicator } from "./SyncIndicator";
import { TopBar } from "./TopBar";

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
    const menu = screen.getByRole("group", { name: "Google accounts" });
    expect(within(menu).getByText("Needs reconnecting")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: /bob@example.com/ }));
    expect(localStorage.getItem("drivesync.account")).toBe("2");
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive"));
  });

  it("starts Google sign-in for a new account", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/accounts/connect": { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" } });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderApp(<AccountSwitcher />, { withAccount: true, route: "/drive?path=Work" });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add Google account" }));
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
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
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
    expect(await screen.findByText("3 open conflicts")).toBeInTheDocument();
    expect(screen.getByText("Offloaded")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Recovery")).toHaveAttribute("aria-disabled", "true");
    expect(await screen.findByText(/40 GB of 100 GB on Google Drive, 1.0 GB in trash/)).toBeInTheDocument();
    expect(screen.getByText(/600 GB free on this server, 30 GB synced here/)).toBeInTheDocument();
  });
});

describe("popovers", () => {
  const open = async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR] });
    renderApp(<TopBar onMenu={() => undefined} />, { withAccount: true });
    const sync = await screen.findByRole("button", { name: /Sync status/ });
    const account = await screen.findByRole("button", { name: /Account:/ });
    return { sync, account };
  };

  it("closes on Escape and gives focus back to the button", async () => {
    const { account } = await open();
    await userEvent.click(account);
    expect(screen.getByRole("group", { name: "Google accounts" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Google accounts" })).not.toBeInTheDocument();
    expect(account).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    const { sync } = await open();
    await userEvent.click(sync);
    expect(screen.getByRole("region", { name: "Sync progress" })).toBeInTheDocument();
    await userEvent.click(document.body);
    expect(screen.queryByRole("region", { name: "Sync progress" })).not.toBeInTheDocument();
  });

  it("opening one closes the other", async () => {
    const { sync, account } = await open();
    await userEvent.click(sync);
    await userEvent.click(account);
    expect(screen.queryByRole("region", { name: "Sync progress" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Google accounts" })).toBeInTheDocument();
  });

  it("tells the search box needs 2 characters", async () => {
    await open();
    expect(screen.getByRole("textbox", { name: "Search files" })).toHaveAccessibleDescription(/at least 2 characters/);
  });
});

describe("account switcher details", () => {
  it("stays on Storage when switching from there", async () => {
    const bob = { ...ACCOUNT, id: 2, email: "bob@example.com" };
    mockApi({ "GET /api/accounts": [ACCOUNT, bob] });
    renderApp(<AccountSwitcher />, { withAccount: true, route: "/storage" });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("button", { name: /bob@example.com/ }));
    expect(localStorage.getItem("drivesync.account")).toBe("2");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/storage");
  });

  it("shows disconnected accounts as disabled", async () => {
    const gone = { ...ACCOUNT, id: 2, email: "gone@example.com", status: "DISCONNECTED" as const };
    mockApi({ "GET /api/accounts": [ACCOUNT, gone] });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    expect(screen.getByRole("button", { name: /gone@example.com/ })).toBeDisabled();
  });

  it("sign out forgets the chosen account", async () => {
    localStorage.setItem("drivesync.account", "1");
    mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/auth/logout": { ok: true } });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(localStorage.getItem("drivesync.account")).toBeNull());
  });

  it("a failed sign out keeps the person signed in", async () => {
    setCsrfToken("tok");
    mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/auth/logout": () => json(500, { detail: "boom" }) });
    renderApp(<AccountSwitcher />, { withAccount: true, route: "/drive" });
    await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(getCsrfToken()).toBe("tok");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive");
    clearCsrfToken();
  });
});

describe("mobile drawer", () => {
  const shell = () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell />, { withAccount: true, route: "/recent" });
  };

  it("is a labelled modal dialog that takes focus and returns it on Escape", async () => {
    shell();
    const menu = screen.getByRole("button", { name: "Open navigation" });
    await userEvent.click(menu);
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(menu).toHaveFocus();
  });

  it("keeps only one + New button while the drawer is open", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell newButton={<button type="button">Make new</button>} />, { withAccount: true, route: "/recent" });
    expect(screen.getAllByRole("button", { name: "Make new" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getAllByRole("button", { name: "Make new" })).toHaveLength(1);
    expect(within(screen.getByRole("dialog", { name: "Navigation" })).getByRole("button", { name: "Make new" })).toBeInTheDocument();
  });

  it("closes when any link is used, even the current page", async () => {
    shell();
    await userEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    await userEvent.click(within(dialog).getByRole("link", { name: /Recent/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
