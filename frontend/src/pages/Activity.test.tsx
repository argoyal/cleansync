import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, mockApi, PAIR, renderApp } from "../test/utils";
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
    await userEvent.click(within(card).getByRole("button", { name: "Sync now" }));
    const photos = screen.getByRole("heading", { name: "Photos" }).closest("article") as HTMLElement;
    await userEvent.click(within(photos).getByRole("button", { name: "Resume" }));
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
    expect(await screen.findByRole("link", { name: "Review blocked sync" })).toHaveAttribute("href", "/activity/7/blocked");
    expect(screen.getAllByRole("button", { name: "Run safe resync" })).toHaveLength(1);
    expect(screen.getByText("This sync would delete 40 files. Your limit is 10.")).toBeInTheDocument();
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
    expect(within(card).getAllByRole("link", { name: "Open" })[0]).toHaveAttribute("href", "/files/1/Drive/Work/notes.conflict1.txt");
    await userEvent.click(within(card).getByRole("button", { name: "Keep Drive" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ action: "keep_remote" }));
  });

  it("says when there are none", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/sync-pairs": [PAIR], "GET /api/sync-pairs/7/conflicts": [] });
    renderApp(<ConflictsPage />, { withAccount: true });
    expect(await screen.findByText("No conflicts")).toBeInTheDocument();
  });
});
