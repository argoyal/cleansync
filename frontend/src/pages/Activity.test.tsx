import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Pair } from "../api/types";
import { ACCOUNT, json, mockApi, PAIR, renderApp } from "../test/utils";
import { ActivityPage } from "./ActivityPage";
import { ConflictsPage } from "./ConflictsPage";
import { BlockedPage, PairDetailPage } from "./PairDetailPage";

const CATALOG = { pair_id: 7, refreshed_at: "2026-10-08T07:00:00Z", refreshing: false, error: null, files: 1234, folders: 50, bytes: 1024 ** 3, native_docs: 3 };

describe("sync activity", () => {
  it("shows a card per pair with its state and actions, and the activity feed", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs": [PAIR, { ...PAIR, id: 8, name: "Photos", paused: true, status: "PAUSED" }],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/sync-pairs/8/catalog": CATALOG,
      "GET /api/activity": [{ id: 1, ts: "2026-10-08T07:55:00Z", level: "info", kind: "sync_done", message: "My Drive sync completed: 2 uploaded", account_id: 1, pair_id: 7 }],
      "POST /api/sync-pairs/7/sync": { result: "queued" },
      "POST /api/sync-pairs/8/resume": { ...PAIR, id: 8 },
    });
    renderApp(<ActivityPage />);
    const card = (await screen.findByRole("heading", { name: "My Drive" })).closest("article") as HTMLElement;
    expect(within(card).getByText("Healthy")).toBeInTheDocument();
    expect(await within(card).findByText("1,234 files")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Sync now, My Drive" }));
    const photos = screen.getByRole("heading", { name: "Photos" }).closest("article") as HTMLElement;
    const photosSwitch = within(photos).getByRole("switch", { name: "Sync off, Photos" });
    expect(photosSwitch).toHaveAttribute("aria-checked", "false");
    expect(photosSwitch).toHaveTextContent("Sync off");
    await userEvent.click(photosSwitch);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/sync-pairs/7/sync", "/api/sync-pairs/8/resume"]));
    expect(screen.getByText("My Drive sync completed: 2 uploaded")).toBeInTheDocument();
  });

  it("offers the fix that matches the problem", async () => {
    mockApi({
      "GET /api/sync-pairs": [
        { ...PAIR, status: "ATTENTION", attention_reason: "BLOCKED", last_error: "This sync would delete 40 files. Your limit is 10." },
        { ...PAIR, id: 8, name: "Old", status: "ATTENTION", attention_reason: "NEEDS_RESYNC" },
      ],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/sync-pairs/8/catalog": CATALOG,
      "GET /api/activity": [],
    });
    renderApp(<ActivityPage />);
    expect(await screen.findByRole("link", { name: "Review blocked sync, My Drive" })).toHaveAttribute("href", "/activity/7/blocked");
    expect(screen.getAllByRole("button", { name: "Run safe resync, Old" })).toHaveLength(1);
    expect(screen.getByText("This sync would delete 40 files. Your limit is 10.")).toBeInTheDocument();
  });

  it("turns syncing off with the switch", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs": [PAIR],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/activity": [],
      "POST /api/sync-pairs/7/pause": { ...PAIR, paused: true, status: "PAUSED" },
    });
    renderApp(<ActivityPage />);
    const card = (await screen.findByRole("heading", { name: "My Drive" })).closest("article") as HTMLElement;
    const sw = within(card).getByRole("switch", { name: "Sync on, My Drive" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sw).toHaveTextContent("Sync on");
    expect(within(card).queryByRole("button", { name: /^(Pause|Resume),/ })).not.toBeInTheDocument();
    await userEvent.click(sw);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/sync-pairs/7/pause"]));
    expect(await screen.findByText("Sync is off. Nothing syncs until you turn it back on.")).toBeInTheDocument();
  });

  it("resuming shows 'Sync is on.' and a keyboard Space press toggles the switch", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs": [{ ...PAIR, paused: true, status: "PAUSED" }],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/activity": [],
      "POST /api/sync-pairs/7/resume": PAIR,
    });
    renderApp(<ActivityPage />);
    const sw = await screen.findByRole("switch", { name: "Sync off, My Drive" });
    sw.focus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/sync-pairs/7/resume"]));
    expect(await screen.findByText("Sync is on.")).toBeInTheDocument();
  });

  it("keeps the switch as the server says when the call fails, and says so", async () => {
    mockApi({
      "GET /api/sync-pairs": [PAIR],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/activity": [],
      "POST /api/sync-pairs/7/pause": json(500, { detail: "Something broke" }),
    });
    renderApp(<ActivityPage />);
    const sw = await screen.findByRole("switch", { name: "Sync on, My Drive" });
    await userEvent.click(sw);
    expect(await screen.findByText(/Something broke|went wrong|failed/i)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Sync on, My Drive" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Sync on, My Drive" })).not.toHaveAttribute("aria-disabled", "true");
  });

  it("keeps focus on the switch while the call is running and disables Sync now", async () => {
    let release: (v: unknown) => void = () => {};
    const gate = new Promise((r) => (release = r));
    mockApi({
      "GET /api/sync-pairs": [PAIR],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/activity": [],
      "POST /api/sync-pairs/7/pause": async () => {
        await gate;
        return { ...PAIR, paused: true };
      },
    });
    renderApp(<ActivityPage />);
    const sw = await screen.findByRole("switch", { name: "Sync on, My Drive" });
    sw.focus();
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-disabled", "true"));
    expect(sw).toHaveFocus();
    expect(sw).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Sync now, My Drive" })).toBeDisabled();
    release(null);
    await waitFor(() => expect(sw).not.toHaveAttribute("aria-disabled", "true"));
  });

  it("shows every switch as stopped while the storage disk is missing, and sends nothing", async () => {
    const MSG = "Storage disk is missing — syncing stopped. Reconnect the disk and restart DriveSync.";
    const { calls } = mockApi({
      "GET /api/sync-pairs": [PAIR, { ...PAIR, id: 8, name: "Photos", paused: true, status: "PAUSED" }],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/sync-pairs/8/catalog": CATALOG,
      "GET /api/activity": [],
      "GET /api/status": { accounts: 1, accounts_attention: 0, pairs: 2, new: 0, healthy: 1, attention: 0, running: 0, paused: 1, conflicts: 0, sync_stopped: MSG },
    });
    renderApp(<ActivityPage />);
    for (const name of ["My Drive", "Photos"]) {
      const sw = await screen.findByRole("switch", { name: `Stopped — storage disk missing, ${name}` });
      expect(sw).toHaveTextContent("Stopped — storage disk missing");
      expect(sw).toHaveAttribute("aria-disabled", "true");
      await userEvent.click(sw);
    }
    expect(screen.getByRole("button", { name: "Sync now, My Drive" })).toBeDisabled();
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("calls a pair with syncing off 'Sync off', not 'Paused'", async () => {
    mockApi({
      "GET /api/sync-pairs": [{ ...PAIR, paused: true, status: "PAUSED" }],
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/activity": [],
    });
    renderApp(<ActivityPage />);
    const card = (await screen.findByRole("heading", { name: "My Drive" })).closest("article") as HTMLElement;
    expect(within(card).getAllByText("Sync off").length).toBe(3); // status pill, next sync, switch
    expect(within(card).queryByText("Paused")).not.toBeInTheDocument();
  });
});

describe("pair details", () => {
  it("has the tabs and shows history and the log", async () => {
    mockApi({
      "GET /api/sync-pairs/7": PAIR,
      "GET /api/sync-pairs/7/catalog": CATALOG,
      "GET /api/sync-pairs/7/history": [
        { id: 5, pair_id: 7, kind: "BISYNC", trigger: "SCHEDULE", state: "SUCCESS", started_at: "2026-10-08T07:55:00Z", completed_at: null, duration_s: 12.4, exit_code: 0, files_uploaded: 3, files_downloaded: 1, files_deleted_local: 0, files_deleted_remote: 1, files_changed: 5, conflicts: 0, planned_deletions: 1, bytes_transferred: 10, override_used: false, error_message: null },
      ],
      "GET /api/sync-pairs/7/logs": { job_id: 5, lines: ["first line", "last line"] },
    });
    renderApp(<PairDetailPage />, { route: "/activity/7", path: "/activity/:pairId" });
    expect(await screen.findByText(/1,234 files/)).toBeInTheDocument();
    for (const t of ["Overview", "Activity", "Files", "Conflicts", "History", "Logs", "Settings"]) {
      expect(screen.getByRole("tab", { name: t })).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole("tab", { name: "History" }));
    expect(await screen.findByText("Success")).toBeInTheDocument();
    expect(screen.getByText("12 s")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Logs" }));
    expect(await screen.findByText(/last line/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download log" })).toHaveAttribute("href", "/api/sync-pairs/7/logs/5/download");
  });

  it("saves pair settings", async () => {
    const { calls } = mockApi({ "GET /api/sync-pairs/7": PAIR, "PUT /api/sync-pairs/7": PAIR });
    renderApp(<PairDetailPage />, { route: "/activity/7?tab=settings", path: "/activity/:pairId" });
    await userEvent.selectOptions(await screen.findByLabelText("Sync"), "Manual only");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        name: "My Drive",
        schedule_interval_s: null,
        max_deletions: 10,
        backups_enabled: true,
        backup_retention_days: 30,
      }),
    );
  });
});

describe("blocked sync", () => {
  it("lists what would be deleted on each side and allows it once after confirming", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs/7": { ...PAIR, status: "ATTENTION", attention_reason: "BLOCKED" },
      "GET /api/sync-pairs/7/planned-changes": {
        job_id: 4,
        changes: [
          { side: "remote", action: "delete", path: "Old/a.txt" },
          { side: "remote", action: "delete", path: "Old/b.txt" },
          { side: "local", action: "delete", path: "c.txt" },
          { side: "remote", action: "copy", path: "new.txt" },
        ],
      },
      "POST /api/sync-pairs/7/override-once": { result: "queued" },
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByText(/This sync would delete 3 files; your limit is 10/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /deleted on Google Drive.*\(2\)/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /deleted on this server \(1\)/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Override once" }));
    await userEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/sync-pairs/7/override-once")).toBe(true));
  });

  it("says when the list is cut short and what the override covers", async () => {
    mockApi({
      "GET /api/sync-pairs/7": { ...PAIR, status: "ATTENTION", attention_reason: "BLOCKED" },
      "GET /api/sync-pairs/7/planned-changes": { job_id: 4, changes: [{ side: "local", action: "delete", path: "a.txt" }] },
      "GET /api/sync-pairs/7/history": [{ id: 4, pair_id: 7, kind: "BISYNC", trigger: "MANUAL", state: "BLOCKED", started_at: null, completed_at: null, duration_s: null, exit_code: null, files_uploaded: 0, files_downloaded: 0, files_deleted_local: 0, files_deleted_remote: 0, files_changed: 0, conflicts: 0, planned_deletions: 12000, bytes_transferred: 0, override_used: false, error_message: null }],
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByText(/This sync would delete 12,000 files/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has been deleted yet/)).toBeInTheDocument();
    expect(await screen.findByText(/cut short: they show 1 of the 12,000/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Override once" }));
    expect(screen.getByText(/not a new limit/)).toBeInTheDocument();
  });

  it("changes the limit", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs/7": { ...PAIR, status: "ATTENTION", attention_reason: "BLOCKED" },
      "GET /api/sync-pairs/7/planned-changes": { job_id: 4, changes: [] },
      "PUT /api/sync-pairs/7": PAIR,
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    const input = await screen.findByLabelText("New deletion limit");
    await userEvent.clear(input);
    await userEvent.type(input, "50");
    await userEvent.click(screen.getByRole("button", { name: "Change limit" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ max_deletions: 50 }));
  });
});

describe("conflicts", () => {
  const CONFLICT = {
    id: 21,
    pair_id: 7,
    path: "Work/notes.txt",
    local_variant: "Work/notes.conflict1.txt",
    remote_variant: "Work/notes.conflict2.txt",
    local_size: 120,
    local_mtime: "2026-10-08T07:00:00Z",
    remote_size: 98,
    remote_mtime: "2026-10-08T06:00:00Z",
    state: "OPEN",
    resolution: null,
  };

  it("shows both versions and keeps the chosen one after confirming", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/sync-pairs": [{ ...PAIR, local_subpath: "Drive" }],
      "GET /api/sync-pairs/7/conflicts": [CONFLICT],
      "POST /api/conflicts/21/resolve": { ...CONFLICT, state: "RESOLVED", sync: "queued" },
    });
    renderApp(<ConflictsPage />, { withAccount: true });
    const card = (await screen.findByRole("heading", { name: "Work/notes.txt" })).closest("article") as HTMLElement;
    expect(within(card).getByRole("link", { name: "Open on this server version of Work/notes.txt" })).toHaveAttribute("href", "/files/1/Drive/Work/notes.conflict1.txt");
    expect(within(card).getByRole("link", { name: "Open on google drive version of Work/notes.txt" })).toHaveAttribute("href", "/files/1/Drive/Work/notes.conflict2.txt");
    expect(within(card).getByRole("link", { name: "Download on google drive version of Work/notes.txt" })).toHaveAttribute("href", "/files/1/Drive/Work/notes.conflict2.txt?download=1");
    await userEvent.click(within(card).getByRole("button", { name: "Keep Drive version of Work/notes.txt" }));
    expect(screen.getByText(/moved to Recovery on this server, not deleted/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ action: "keep_remote" }));
  });

  it("says when there are none", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/sync-pairs/7/conflicts": [] });
    renderApp(<ConflictsPage />, { withAccount: true });
    expect(await screen.findByText("No conflicts")).toBeInTheDocument();
  });
});

const attn = (reason: string, extra: Partial<Pair> = {}): Pair => ({ ...PAIR, status: "ATTENTION", attention_reason: reason, ...extra } as Pair);

describe("every attention reason is explained", () => {
  const cases: [string, Partial<Pair>, RegExp, RegExp | null][] = [
    ["BLOCKED", {}, /would delete more files than your limit/, /Review blocked sync, My Drive/],
    ["CONFLICTS", {}, /changed on both sides/, /Resolve conflicts, My Drive/],
    ["NEEDS_RESYNC", {}, /safe resync copies both ways/, /Run safe resync, My Drive/],
    ["AUTH", {}, /no longer lets DriveSync in/, /Reconnect account, My Drive/],
    ["ACCESS_CHECK", {}, /storage folder or its marker file can't be found/, /Sync now, My Drive/],
    ["PLAN_UNREADABLE", {}, /couldn't read rclone's plan, so nothing was changed/, /Sync now, My Drive/],
    ["FAILED", { next_sync_at: "2999-01-01T00:00:00Z" }, /The last sync failed. Retrying/, /Sync now, My Drive/],
  ];
  for (const [reason, extra, text, action] of cases) {
    it(`${reason}: a sentence and its action`, async () => {
      mockApi({ "GET /api/sync-pairs": [attn(reason, extra)], "GET /api/sync-pairs/7/catalog": CATALOG, "GET /api/activity": [] });
      renderApp(<ActivityPage />);
      expect(await screen.findByText(text)).toBeInTheDocument();
      expect(screen.getByRole(reason === "NEEDS_RESYNC" || reason === "ACCESS_CHECK" || reason === "PLAN_UNREADABLE" || reason === "FAILED" ? "button" : "link", { name: action as RegExp })).toBeInTheDocument();
    });
  }

  it("does not offer a safe resync on a one-way pair", async () => {
    mockApi({ "GET /api/sync-pairs": [attn("NEEDS_RESYNC", { direction: "LOCAL_TO_REMOTE" })], "GET /api/sync-pairs/7/catalog": CATALOG, "GET /api/activity": [] });
    renderApp(<ActivityPage />);
    expect(await screen.findByText(/lost track of each other/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run safe resync/ })).toBeNull();
  });

  it("shows the same explanation on the pair's overview", async () => {
    mockApi({ "GET /api/sync-pairs/7": attn("ACCESS_CHECK"), "GET /api/sync-pairs/7/catalog": CATALOG });
    renderApp(<PairDetailPage />, { route: "/activity/7", path: "/activity/:pairId" });
    expect(await screen.findByText(/marker file can't be found/)).toBeInTheDocument();
  });
});

describe("blocked page honesty", () => {
  const BLOCKED_PAIR = attn("BLOCKED");

  it("shows no counts and disables Override while the list loads", async () => {
    mockApi({ "GET /api/sync-pairs/7": BLOCKED_PAIR, "GET /api/sync-pairs/7/planned-changes": () => new Promise(() => {}) });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByText("Loading the list…")).toBeInTheDocument();
    expect(screen.queryByText(/This sync would delete/)).toBeNull();
    expect(screen.queryByRole("heading", { name: /Would be deleted/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Override once" })).toBeDisabled();
  });

  it("shows an error with Retry and keeps Override disabled when the list fails", async () => {
    let n = 0;
    mockApi({
      "GET /api/sync-pairs/7": BLOCKED_PAIR,
      "GET /api/sync-pairs/7/planned-changes": () => (n++ === 0 ? json(500, { detail: "boom" }) : { job_id: 4, changes: [{ side: "local", action: "delete", path: "a.txt" }] }),
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Override once" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/This sync would delete 1 files/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Override once" })).toBeEnabled();
  });

  it("warns that the total may be larger when the blocked job is not in the recent jobs", async () => {
    mockApi({
      "GET /api/sync-pairs/7": BLOCKED_PAIR,
      "GET /api/sync-pairs/7/planned-changes": { job_id: 4, changes: [{ side: "local", action: "delete", path: "a.txt" }] },
      "GET /api/sync-pairs/7/history": [],
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByText(/total may be larger/)).toBeInTheDocument();
  });

  it("has no Override button when nothing is blocked", async () => {
    mockApi({ "GET /api/sync-pairs/7": PAIR, "GET /api/sync-pairs/7/planned-changes": { job_id: null, changes: [] } });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    expect(await screen.findByText(/Nothing is blocked/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override once" })).toBeNull();
  });

  it("refuses an empty or fractional limit and shows a failed change", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs/7": BLOCKED_PAIR,
      "GET /api/sync-pairs/7/planned-changes": { job_id: 4, changes: [] },
      "PUT /api/sync-pairs/7": () => json(422, { detail: "nope" }),
    });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    const input = await screen.findByLabelText("New deletion limit");
    await userEvent.clear(input);
    expect(screen.getByRole("button", { name: "Change limit" })).toBeDisabled();
    await userEvent.type(input, "2.5");
    expect(screen.getByRole("button", { name: "Change limit" })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: "Change limit" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("autofocuses Cancel in the allow-once dialog", async () => {
    mockApi({ "GET /api/sync-pairs/7": BLOCKED_PAIR, "GET /api/sync-pairs/7/planned-changes": { job_id: 4, changes: [] } });
    renderApp(<BlockedPage />, { route: "/activity/7/blocked", path: "/activity/:pairId/blocked" });
    await userEvent.click(await screen.findByRole("button", { name: "Override once" }));
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });
});

describe("pair details extras", () => {
  const JOB = { id: 5, pair_id: 7, kind: "BISYNC", trigger: "SCHEDULE", state: "SUCCESS", started_at: "2026-10-08T07:55:00Z", completed_at: null, duration_s: 1, exit_code: 0, files_uploaded: 0, files_downloaded: 0, files_deleted_local: 0, files_deleted_remote: 0, files_changed: 0, conflicts: 0, planned_deletions: 0, bytes_transferred: 0, override_used: false, error_message: null };

  it("labels the history table, links each job's log and loads more", async () => {
    const { calls } = mockApi({
      "GET /api/sync-pairs/7": PAIR,
      "GET /api/sync-pairs/7/history": ({ url }: { url: URL }) => (url.searchParams.get("limit") === "50" ? Array.from({ length: 50 }, (_, i) => ({ ...JOB, id: 100 - i })) : [JOB]),
    });
    renderApp(<PairDetailPage />, { route: "/activity/7?tab=history", path: "/activity/:pairId" });
    expect(await screen.findByRole("table", { name: "Sync history" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Download log of the sync/ })[0]).toHaveAttribute("href", "/api/sync-pairs/7/logs/100/download");
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("limit") === "100")).toBe(true));
  });

  it("refuses an empty deletion limit in settings", async () => {
    mockApi({ "GET /api/sync-pairs/7": PAIR });
    renderApp(<PairDetailPage />, { route: "/activity/7?tab=settings", path: "/activity/:pairId" });
    await userEvent.clear(await screen.findByLabelText("Deletion limit"));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("does not announce the whole log, only a count of new lines", async () => {
    mockApi({ "GET /api/sync-pairs/7": PAIR, "GET /api/sync-pairs/7/logs": { job_id: 5, lines: ["a", "b"] } });
    renderApp(<PairDetailPage />, { route: "/activity/7?tab=logs", path: "/activity/:pairId" });
    const pre = await screen.findByLabelText("Sync log");
    expect(pre).not.toHaveAttribute("aria-live");
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("conflict load errors", () => {
  it("shows a message when the conflicts cannot be loaded", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/sync-pairs/7/conflicts": () => json(500, { detail: "x" }) });
    renderApp(<ConflictsPage />, { withAccount: true });
    expect(await screen.findByText(/Could not load the conflicts of My Drive/)).toBeInTheDocument();
    expect(screen.queryByText("No conflicts")).toBeNull();
  });
});
