import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { clearCsrfToken, getCsrfToken, setCsrfToken } from "../api/client";
import { progressStore } from "../hooks/live";
import { ACCOUNT, json, mockApi, PAIR, renderApp } from "../test/utils";
import { AppShell } from "./AppShell";
import { AccountSwitcher } from "./AccountSwitcher";
import { ContentBar } from "./ContentBar";
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
    const menu = screen.getByRole("group", { name: "Google accounts" });
    expect(within(menu).getByText("Needs reconnecting")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: /bob@example.com/ }));
    expect(localStorage.getItem("drivesync.account")).toBe("2");
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive"));
  });

  it("switches the theme from the account menu", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT] });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Account: alice@example.com" }));
    const theme = screen.getByRole("radiogroup", { name: "Theme" });
    expect(within(theme).getByRole("radio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(theme).getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(within(theme).getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(theme).getByRole("radio", { name: "System" }));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("theme radios use arrow keys and a single tab stop", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT] });
    renderApp(<AccountSwitcher />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Account: alice@example.com" }));
    const theme = screen.getByRole("radiogroup", { name: "Theme" });
    const radio = (n: string) => within(theme).getByRole("radio", { name: n });
    expect(radio("System")).toHaveAttribute("tabindex", "0");
    expect(radio("Light")).toHaveAttribute("tabindex", "-1");
    radio("System").focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(radio("Light")).toHaveFocus();
    expect(radio("Light")).toHaveAttribute("aria-checked", "true");
    expect(radio("Light")).toHaveAttribute("tabindex", "0");
    expect(radio("System")).toHaveAttribute("tabindex", "-1");
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(radio("Dark")).toHaveFocus(); // wraps round
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
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

describe("rail and panel", () => {
  const shell = (route: string, account = ACCOUNT) => {
    mockApi({ "GET /api/accounts": [account], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS, "GET /api/storage": STORAGE });
    renderApp(<AppShell />, { withAccount: true, route });
  };

  it("has the five sections, the active one marked, and a conflicts badge", async () => {
    shell("/recent");
    const rail = screen.getByRole("navigation", { name: "Sections" });
    for (const [name, href] of [["Files", "/drive"], ["Mail", "/mail"], ["Activity", "/activity"], ["Recovery", "/recovery"], ["Settings", "/settings"]]) {
      expect(within(rail).getByRole("link", { name: new RegExp(`^${name}`) })).toHaveAttribute("href", href);
    }
    expect(within(rail).getByRole("link", { name: /^Files/ })).toHaveAttribute("aria-current", "page");
    expect(await within(rail).findByText("3 open conflicts")).toBeInTheDocument();
    // A fixed strong red: the white number stays readable in dark mode too.
    expect(within(rail).getByText("3 open conflicts").parentElement?.className).toContain("bg-rail-badge");
  });

  it("shows the Files views with the account switcher on top", async () => {
    shell("/recent");
    const views = screen.getByRole("navigation", { name: "Files views" });
    for (const [name, href] of [["My Drive", "/drive"], ["Recent", "/recent"], ["Starred", "/starred"], ["Offloaded", "/offloaded"], ["Conflicts", "/conflicts"]]) {
      expect(within(views).getByRole("link", { name: new RegExp(`^${name}`) })).toHaveAttribute("href", href);
    }
    expect(within(views).getByRole("link", { name: /^Recent/ })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("button", { name: "Account: alice@example.com" })).toBeInTheDocument();
    expect(within(views).getByRole("link", { name: "Sync a Drive folder…" })).toHaveAttribute("href", "/setup?step=folders&account=1");
    expect(await screen.findByText(/40 GB of 100 GB on Google Drive/)).toBeInTheDocument();
  });

  it("shows each section's own views", async () => {
    const cases: [string, string, [string, string][]][] = [
      ["/activity", "Activity views", [["Sync activity", "/activity"], ["Storage", "/storage"]]],
      ["/recovery", "Recovery views", [["All", "/recovery"], ["Files", "/recovery?source=files"], ["Mail", "/recovery?source=mail"]]],
      ["/settings", "Settings views", [["General", "/settings?tab=general"], ["Accounts", "/settings?tab=accounts"], ["System", "/settings?tab=system"]]],
    ];
    for (const [route, label, links] of cases) {
      shell(route);
      const views = screen.getByRole("navigation", { name: label });
      for (const [name, href] of links) expect(within(views).getByRole("link", { name })).toHaveAttribute("href", href);
      cleanup();
    }
  });

  it("asks to set up Mail instead of showing its views", async () => {
    shell("/mail");
    expect(await screen.findByRole("heading", { name: "Set up Mail" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Mail views" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up mail backup" })).toBeInTheDocument();
  });

  it("shows the Mail views once mail is set up", async () => {
    shell("/mail?view=inbox", { ...ACCOUNT, services: { files: "active", mail: "active" }, mail_permission: true });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    for (const [name, href] of [["All mail", "/mail"], ["Inbox", "/mail?view=inbox"], ["Sent", "/mail?view=sent"], ["Starred", "/mail?view=starred"], ["Important", "/mail?view=important"], ["Offloaded", "/mail?view=offloaded"], ["Deleted in Gmail", "/mail?view=deleted"]]) {
      expect(within(views).getByRole("link", { name })).toHaveAttribute("href", href);
    }
    expect(within(views).getByRole("link", { name: "Inbox" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("textbox", { name: "Search mail" })).toBeInTheDocument();
  });

  it("puts the Gmail views first and CleanSync's own below its line", async () => {
    const active = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
    mockApi({
      "GET /api/accounts": [active],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": STATUS,
      "GET /api/mail/counts": {
        views: { inbox: 3, starred: 1, important: 0, sent: 2, archived: 4, all: 9, suggested: 1, offloaded: 5, deleted: 6 },
        labels: [{ id: "Label_2", name: "Receipts", count: 2 }],
      },
      "GET /api/mail/unsubscribe/senders": { items: [], total: 0, suggested: 7, scan: { scanned: 0, total: 0 } },
    });
    renderApp(<AppShell />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    const names = (group: HTMLElement) => within(group).getAllByRole("link").map((l) => l.textContent?.replace(/\s+/g, " ").trim());
    const gmail = within(views).getByRole("group", { name: "Gmail" });
    await within(gmail).findByRole("link", { name: "Inbox 3" });
    expect(names(gmail)).toEqual(["Inbox 3", "Starred 1", "Important 0", "Sent 2", "Archived 4", "All mail 9", "Receipts 2"]);
    const own = within(views).getByRole("group", { name: "CleanSync" });
    await within(own).findByRole("link", { name: "Unsubscribe 7" });
    expect(names(own)).toEqual(["Suggested 1", "Rules", "Unsubscribe 7", "Offloaded 5", "Deleted in Gmail 6"]);
    expect(within(own).getByRole("link", { name: "Unsubscribe 7" })).toHaveAttribute("href", "/mail/unsubscribe");
    expect(within(own).getByRole("link", { name: "Offloaded 5" })).toHaveAttribute("aria-current", "page");
    expect(within(views).getByText("CleanSync")).toBeInTheDocument();
    expect(within(views).queryByRole("link", { name: /Local archive/ })).not.toBeInTheDocument();
  });

  it("asks to set up Files when an account has no file storage set up", async () => {
    shell("/drive", { ...ACCOUNT, services: { files: "not_set_up", mail: "not_set_up" } });
    expect(await screen.findByRole("heading", { name: "Set up Files" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Files views" })).not.toBeInTheDocument();
  });

  it("puts the New button in the Files panel only", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell newButton={<button type="button">Make new</button>} />, { withAccount: true, route: "/drive" });
    expect(screen.getAllByRole("button", { name: "Make new" })).toHaveLength(1);
    cleanup();
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell newButton={<button type="button">Make new</button>} />, { withAccount: true, route: "/activity" });
    expect(screen.queryByRole("button", { name: "Make new" })).not.toBeInTheDocument();
  });

  it("keeps only one New button while the views sheet is open", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell newButton={<button type="button">Make new</button>} />, { withAccount: true, route: "/recent" });
    expect(screen.getAllByRole("button", { name: "Make new" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Show views" }));
    expect(screen.getAllByRole("button", { name: "Make new" })).toHaveLength(1);
    expect(within(screen.getByRole("dialog", { name: "Views" })).getByRole("button", { name: "Make new" })).toBeInTheDocument();
  });

  it("the views sheet is a labelled dialog that closes on a link or Escape", async () => {
    shell("/recent");
    const opener = screen.getByRole("button", { name: "Show views" });
    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Views" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    await userEvent.click(opener);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Views" })).getByRole("link", { name: /^Recent/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); // even the page already open
  });

  it("the views sheet has the way Home on phones, and closes on it", async () => {
    shell("/recent");
    await userEvent.click(screen.getByRole("button", { name: "Show views" }));
    const dialog = screen.getByRole("dialog", { name: "Views" });
    await userEvent.click(within(dialog).getByRole("link", { name: "CleanSync home" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0].textContent).toBe("/"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Tab inside the views sheet while it is open", async () => {
    shell("/recent");
    await userEvent.click(screen.getByRole("button", { name: "Show views" }));
    const dialog = screen.getByRole("dialog", { name: "Views" });
    const all = Array.from(dialog.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input, [tabindex]:not([tabindex='-1'])"));
    expect(all.length).toBeGreaterThan(1);
    all[all.length - 1].focus();
    await userEvent.tab();
    expect(all[0]).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(all[all.length - 1]).toHaveFocus();
    dialog.focus(); // the sheet itself has focus right after it opens
    await userEvent.tab({ shift: true });
    expect(all[all.length - 1]).toHaveFocus();
  });

  it("shows a white focus ring on the indigo rail and bottom bar", () => {
    shell("/recent");
    for (const name of ["Sections", "Sections at the bottom"]) {
      const links = within(screen.getByRole("navigation", { name })).getAllByRole("link");
      for (const link of links) expect(link.className, `${name}: ${link.textContent}`).toContain("focus-visible:outline-rail-mark");
    }
  });

  it("keeps the bottom bar clear of the phone's home bar, and the content clear of the bar", () => {
    shell("/recent");
    const bar = screen.getByRole("navigation", { name: "Sections at the bottom" });
    expect(bar.className).toContain("pb-[env(safe-area-inset-bottom)]");
    expect(bar.className).toContain("h-[calc(4rem+env(safe-area-inset-bottom))]");
    expect(screen.getByRole("main").parentElement?.className).toContain("pb-[calc(4rem+env(safe-area-inset-bottom))]");
  });

  it("has a bottom bar with every section for phones", () => {
    shell("/mail");
    const bar = screen.getByRole("navigation", { name: "Sections at the bottom" });
    expect(within(bar).getAllByRole("link").map((l) => l.textContent?.replace(/\d+.*/, "").trim())).toEqual(["Files", "Mail", "Activity", "Recovery", "Settings"]);
    expect(within(bar).getByRole("link", { name: /^Mail/ })).toHaveAttribute("aria-current", "page");
  });

  it("says when syncing stopped, on every page", async () => {
    const MSG = "Storage disk is missing — syncing stopped. Reconnect the disk and restart CleanSync.";
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": { ...STATUS, sync_stopped: MSG } });
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    expect(await screen.findByRole("alert")).toHaveTextContent(MSG);
  });

  it("shows no stop notice while syncing runs", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": { ...STATUS, sync_stopped: null } });
    renderApp(<AppShell />, { withAccount: true, route: "/recent" });
    await within(screen.getByRole("navigation", { name: "Sections" })).findByText("3 open conflicts");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("shows failed mails as a badge on Mail", async () => {
    const active = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
    mockApi({
      "GET /api/accounts": [active],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": STATUS,
      "GET /api/mail/status": { account_id: 1, failed: [{ provider_id: "a", attempts: 3, last_error: "x" }, { provider_id: "b", attempts: 4, last_error: "y" }] },
    });
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    const rail = screen.getByRole("navigation", { name: "Sections" });
    expect(await within(rail).findByText("2 mails could not be backed up")).toBeInTheDocument();
  });

  it("shows mail counts and a Labels list that folds away", async () => {
    const active = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
    mockApi({
      "GET /api/accounts": [active],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": STATUS,
      "GET /api/mail/counts": {
        views: { all: 38214, inbox: 1902, sent: 300, starred: 12, important: 40, offloaded: 5000, deleted: 7 },
        labels: [{ id: "Label_2", name: "Receipts", count: 410 }, { id: "Label_1", name: "Work & <Stuff>", count: 9 }],
      },
    });
    renderApp(<AppShell />, { withAccount: true, route: "/mail?label=Label_2" });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    expect(await within(views).findByRole("link", { name: "All mail 38,214" })).toHaveAttribute("href", "/mail");
    expect(within(views).getByRole("link", { name: "Inbox 1,902" })).toBeInTheDocument();
    const labels = within(views).getByRole("list", { name: "Labels" });
    expect(within(labels).getByRole("link", { name: "Receipts 410" })).toHaveAttribute("aria-current", "page");
    expect(within(labels).getByRole("link", { name: "Work & <Stuff> 9" })).toHaveAttribute("href", "/mail?label=Label_1");
    const fold = within(views).getByRole("button", { name: "Labels" });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(fold);
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(within(views).queryByRole("list", { name: "Labels" })).not.toBeInTheDocument();
  });

  const labelsApi = () => {
    const active = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
    mockApi({
      "GET /api/accounts": [active],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/status": STATUS,
      "GET /api/mail/counts": {
        views: { all: 1, inbox: 1, sent: 0, starred: 0, important: 0, offloaded: 0, deleted: 0 },
        labels: [{ id: "Label_2", name: "Receipts", count: 1 }],
      },
    });
  };

  it("remembers the Labels fold in this browser", async () => {
    labelsApi();
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    await userEvent.click(await within(views).findByRole("button", { name: "Labels" }));
    cleanup();
    labelsApi();
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    const again = await screen.findByRole("navigation", { name: "Mail views" });
    const fold = await within(again).findByRole("button", { name: "Labels" });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(within(again).queryByRole("list", { name: "Labels" })).not.toBeInTheDocument();
    await userEvent.click(fold);
    expect(within(again).getByRole("list", { name: "Labels" })).toBeInTheDocument();
  });

  it("still folds the Labels when the browser blocks storage", async () => {
    const fail = () => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: fail, setItem: fail, removeItem: fail, clear: fail, key: fail, length: 0 });
    labelsApi();
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    const views = await screen.findByRole("navigation", { name: "Mail views" });
    const fold = await within(views).findByRole("button", { name: "Labels" });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(fold);
    expect(fold).toHaveAttribute("aria-expanded", "false");
  });
});

describe("content bar", () => {
  const open = async (route = "/drive") => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR] });
    renderApp(<><ContentBar section={route.startsWith("/mail") ? "mail" : "files"} onViews={() => undefined} /><AccountSwitcher /></>, { withAccount: true, route });
    const sync = await screen.findByRole("button", { name: /Sync status/ });
    const account = await screen.findByRole("button", { name: /Account:/ });
    return { sync, account };
  };

  it("closes popovers on Escape and gives focus back", async () => {
    const { account } = await open();
    await userEvent.click(account);
    expect(screen.getByRole("group", { name: "Google accounts" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Google accounts" })).not.toBeInTheDocument();
    expect(account).toHaveFocus();
  });

  it("closes popovers on a click outside", async () => {
    const { sync } = await open();
    await userEvent.click(sync);
    expect(screen.getByRole("region", { name: "Sync progress" })).toBeInTheDocument();
    await userEvent.click(document.body);
    expect(screen.queryByRole("region", { name: "Sync progress" })).not.toBeInTheDocument();
  });

  it("opening one popover closes the other", async () => {
    const { sync, account } = await open();
    await userEvent.click(sync);
    await userEvent.click(account);
    expect(screen.queryByRole("region", { name: "Sync progress" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Google accounts" })).toBeInTheDocument();
  });

  it("searches files, or mail in the Mail section", async () => {
    await open("/drive");
    expect(screen.getByRole("textbox", { name: "Search files" })).toHaveAccessibleDescription(/at least 2 characters/);
    await userEvent.type(screen.getByRole("textbox", { name: "Search files" }), "budget{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/search?q=budget");
    cleanup();
    await open("/mail");
    await userEvent.type(screen.getByRole("textbox", { name: "Search mail" }), "from:bank{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/mail?q=from%3Abank");
  });

  it("searches mail in the view and label on screen, and an empty search clears it", async () => {
    await open("/mail?view=inbox&label=Label_2");
    const box = screen.getByRole("textbox", { name: "Search mail" });
    await userEvent.type(box, "from:bank{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/mail?view=inbox&label=Label_2&q=from%3Abank");
    await userEvent.clear(box);
    await userEvent.type(box, "{Enter}");
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail\?view=inbox&label=Label_2$/);
  });
});

describe("search across sections", () => {
  it("a mail search does not stay in the files search box", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/status": STATUS });
    renderApp(<AppShell />, { withAccount: true, route: "/mail" });
    await userEvent.type(screen.getByRole("textbox", { name: "Search mail" }), "from:bank{Enter}");
    expect(screen.getByRole("textbox", { name: "Search mail" })).toHaveValue("from:bank");
    await userEvent.click(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("link", { name: /^Files/ }));
    expect(screen.getByRole("textbox", { name: "Search files" })).toHaveValue("");
  });
});

describe("switching account", () => {
  it("switching account stays in the section", async () => {
    const bob = { ...ACCOUNT, id: 2, email: "bob@example.com" };
    // The whole address is checked, so "/mail?view=inbox" staying put would fail.
    for (const [route, to] of [["/mail?view=inbox", "/mail"], ["/recovery?source=files", "/recovery?source=files"], ["/recent", "/drive"]]) {
      mockApi({ "GET /api/accounts": [ACCOUNT, bob] });
      renderApp(<AccountSwitcher />, { withAccount: true, route });
      await userEvent.click(await screen.findByRole("button", { name: /Account:/ }));
      await userEvent.click(screen.getByRole("button", { name: /bob@example.com/ }));
      await waitFor(() => expect(screen.getAllByTestId("location")[0].textContent).toBe(to));
      cleanup();
      localStorage.clear();
    }
  });
});

describe("search options", () => {
  const openMail = async (route: string) => {
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 4 }] },
    });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route });
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    return screen.getByRole("region", { name: "Search options" });
  };
  const box = () => screen.getByRole("textbox", { name: "Search mail" });

  it("builds the search from the form and runs it in the view", async () => {
    const form = await openMail("/mail?view=inbox");
    await userEvent.type(within(form).getByLabelText("From"), "Example News");
    await userEvent.type(within(form).getByLabelText("To"), "me@example.com");
    await userEvent.type(within(form).getByLabelText("Has the words"), "invoice");
    await within(form).findByRole("option", { name: "Receipts" });
    await userEvent.selectOptions(within(form).getByLabelText("Label"), "Receipts");
    await userEvent.selectOptions(within(form).getByLabelText("Larger or smaller"), "smaller");
    await userEvent.type(within(form).getByLabelText("Size"), "5");
    await userEvent.selectOptions(within(form).getByLabelText("Size unit"), "KB");
    await userEvent.selectOptions(within(form).getByLabelText("Date"), "older");
    await userEvent.type(within(form).getByLabelText("Age"), "2");
    await userEvent.selectOptions(within(form).getByLabelText("Age unit"), "y");
    await userEvent.click(within(form).getByLabelText("Has attachment"));
    await userEvent.click(within(form).getByLabelText("Unread"));
    await userEvent.click(within(form).getByLabelText("Starred"));
    await userEvent.click(within(form).getByRole("button", { name: "Search" }));
    const text = 'from:"Example News" to:me@example.com invoice label:Receipts smaller:5K older_than:2y has:attachment is:unread is:starred';
    expect(box()).toHaveValue(text);
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(`/mail?${new URLSearchParams({ view: "inbox", q: text })}`);
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
  });

  it("reads the search box back into the form; Clear empties the form; closing changes nothing", async () => {
    const q = 'from:"Example News" larger:2M before:2024-01-31 has:attachment shoes';
    const form = await openMail(`/mail?${new URLSearchParams({ q })}`);
    expect(within(form).getByLabelText("From")).toHaveValue("Example News");
    expect(within(form).getByLabelText("Size")).toHaveValue("2");
    expect(within(form).getByLabelText("Size unit")).toHaveValue("MB");
    expect(within(form).getByLabelText("Date")).toHaveValue("before");
    expect(within(form).getByLabelText("Day")).toHaveValue("2024-01-31");
    expect(within(form).getByLabelText("Has attachment")).toBeChecked();
    expect(within(form).getByLabelText("Has the words")).toHaveValue("shoes");
    await userEvent.click(within(form).getByRole("button", { name: "Clear" }));
    expect(within(form).getByLabelText("From")).toHaveValue("");
    expect(within(form).getByLabelText("Has attachment")).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
    expect(box()).toHaveValue(q);
  });

  it("keeps what it can't read in Has the words, so nothing is lost", async () => {
    const q = 'from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope';
    const form = await openMail(`/mail?${new URLSearchParams({ q })}`);
    await within(form).findByRole("option", { name: "Receipts" });
    expect(within(form).getByLabelText("Has the words")).toHaveValue('from:b@x.com "big deal" larger:2G in:archived label:Nope');
    await userEvent.click(within(form).getByRole("button", { name: "Search" }));
    expect(box()).toHaveValue(q);
  });

  it("has no Label in Deleted in Gmail, where a typed label stays in the words", async () => {
    const form = await openMail(`/mail?${new URLSearchParams({ view: "deleted", q: "label:Receipts" })}`);
    expect(within(form).queryByLabelText("Label")).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Has the words")).toHaveValue("label:Receipts");
  });

  it("takes only numbers for the size and the age, and closes on Escape", async () => {
    const form = await openMail("/mail");
    await userEvent.type(within(form).getByLabelText("Size"), "abc");
    expect(within(form).getByText("Use a number.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Search" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Search options" })).not.toBeInTheDocument();
  });

  it("keeps a size that isn't a number in the Size field when the labels arrive late", async () => {
    let answer: (v: unknown) => void = () => {};
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/mail/counts": () => new Promise((r) => (answer = r)),
    });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    const form = screen.getByRole("region", { name: "Search options" });
    await userEvent.type(within(form).getByLabelText("Size"), "abc");
    await userEvent.type(within(form).getByLabelText("Has the words"), "shoes");
    answer({ views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 4 }] });
    await within(form).findByRole("option", { name: "Receipts" });
    expect(within(form).getByLabelText("Size")).toHaveValue("abc");
    expect(within(form).getByLabelText("Has the words")).toHaveValue("shoes");
    expect(within(form).getByText("Use a number.")).toBeInTheDocument();
  });

  it("keeps an age that isn't a whole number in the Age field when the labels arrive late", async () => {
    let answer: (v: unknown) => void = () => {};
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [PAIR],
      "GET /api/mail/counts": () => new Promise((r) => (answer = r)),
    });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Search options" }));
    const form = screen.getByRole("region", { name: "Search options" });
    await userEvent.selectOptions(within(form).getByLabelText("Date"), "older");
    await userEvent.type(within(form).getByLabelText("Age"), "2.5");
    answer({ views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 4 }] });
    await within(form).findByRole("option", { name: "Receipts" });
    expect(within(form).getByLabelText("Age")).toHaveValue("2.5");
    expect(within(form).getByLabelText("Has the words")).toHaveValue("");
  });

  it("says when a quote mark is left open, because the keywords after it are read as words", async () => {
    const form = await openMail("/mail");
    const words = within(form).getByLabelText("Has the words");
    await userEvent.type(words, 'a "big deal');
    expect(within(form).getByText("The quote mark isn't closed, so keywords after it are read as words.")).toBeInTheDocument();
    await userEvent.type(words, '"');
    expect(within(form).queryByText(/isn't closed/)).not.toBeInTheDocument();
  });

  it("is offered on the mail list only", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR] });
    renderApp(<ContentBar section="mail" onViews={() => undefined} />, { withAccount: true, route: "/mail/rules" });
    expect(screen.queryByRole("button", { name: "Search options" })).not.toBeInTheDocument();
  });
});
