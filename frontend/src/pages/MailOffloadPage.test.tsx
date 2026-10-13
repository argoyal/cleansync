import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../test/utils";
import { MailOffloadPage, OffloadBatches } from "./MailOffloadPage";

const OFF = {
  id: 7,
  account_id: 1,
  filter_text: "older_than:1y",
  state: "DONE_WITH_SKIPS",
  total: 123,
  done: 120,
  skipped: 3,
  not_done: 0,
  bytes: 850 * 1024 ** 2,
  reason: null,
  created_at: "2026-10-10T09:00:00Z",
  finished_at: "2026-10-10T09:20:00Z",
  kind: "offload",
  removed: 0,
  trashed: 120,
  trash_until: "2026-11-09",
  google_emptied: false,
  skipped_items: [{ message_id: 5, subject: "Old photos", from_addr: "friend@example.com", date: "2024-05-01T00:00:00Z", reason: "The copy on this server didn't check out (the file's contents changed), so it stayed in Gmail." }],
};
const open = (id = 7) => renderApp(<MailOffloadPage />, { withAccount: true, route: `/mail/offloads/${id}`, path: "/mail/offloads/:id" });

describe("an offload's page", () => {
  it("shows the outcome, the skipped mails, and when Gmail frees the space", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads/7": OFF });
    open();
    expect(await screen.findByRole("heading", { name: "Mail offload" })).toBeInTheDocument();
    expect(screen.getByText("Done, some skipped")).toBeInTheDocument();
    expect(screen.getByText("Offloaded 120 of 123 mails · 850 MB · 3 skipped")).toBeInTheDocument();
    expect(screen.getByText(/In Gmail's trash until about/)).toBeInTheDocument();
    expect(screen.getByText(/Gmail empties its trash on its own after 30 days; that's when the space comes back\./)).toBeInTheDocument();
    const skipped = screen.getByRole("list", { name: "Skipped mails" });
    expect(within(skipped).getByText("Old photos")).toBeInTheDocument();
    expect(within(skipped).getByText(/didn't check out/)).toBeInTheDocument();
  });

  it("says the space is free for mail removed for good, and offers Stop while it runs", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/mail/offloads/5": { ...OFF, id: 5, state: "RUNNING", done: 2, removed: 2, trashed: 0, skipped_items: [] },
      "POST /api/mail/offloads/5/stop": {},
    });
    open(5);
    expect(await screen.findByText("Removed from Gmail. The space is free now.")).toBeInTheDocument();
    expect(screen.queryByText(/Gmail empties its trash on its own/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/mail/offloads/5/stop")).toBe(true));
  });

  it("says when Gmail has emptied its trash, and offers no way to empty it", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads/7": { ...OFF, google_emptied: true } });
    open();
    expect(await screen.findByText("Gmail has emptied its trash.")).toBeInTheDocument();
    expect(screen.queryByText(/In Gmail's trash until/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Empty from Gmail trash/ })).toBeNull();
  });

  it("restores the whole offload and shows a failed one's reason", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/mail/offloads/7": { ...OFF, state: "FAILED", reason: "Storage disk is missing — syncing stopped. Reconnect the disk and restart CleanSync.", not_done: 40 },
      "POST /api/mail/restore": { queued: "queued", count: 120 },
    });
    open();
    expect(await screen.findByText(/Storage disk is missing/)).toBeInTheDocument();
    expect(screen.getByText("40 weren't reached and stayed in Gmail.")).toBeInTheDocument();
    const restore = screen.getByRole("button", { name: "Restore all to Gmail" });
    await waitFor(() => expect(restore).toBeEnabled()); // once the account has loaded
    await userEvent.click(restore);
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, offload_id: 7 }));
  });

  it("lists every offload batch", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads": [OFF] });
    renderApp(<OffloadBatches accountId={ACCOUNT.id} />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    expect(await screen.findByRole("link", { name: "older_than:1y" })).toHaveAttribute("href", "/mail/offloads/7");
  });

  it("says plainly when the offload is gone", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads/9": () => json(404, { detail: "offload not found" }) });
    open(9);
    expect(await screen.findByText("That offload doesn't exist.")).toBeInTheDocument();
  });

  it("says so for an id that isn't a number, without asking the server", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [ACCOUNT] });
    renderApp(<MailOffloadPage />, { withAccount: true, route: "/mail/offloads/abc", path: "/mail/offloads/:id" });
    expect(await screen.findByText("That offload doesn't exist.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/mail/offloads"))).toBe(false);
  });

  it("shows Loading… while the offload loads", async () => {
    let answer: (v: Response) => void = () => {};
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/mail/offloads/7": () => new Promise<Response>((r) => (answer = r)) });
    open();
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    answer(json(200, OFF));
    expect(await screen.findByRole("heading", { name: "Mail offload" })).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});
