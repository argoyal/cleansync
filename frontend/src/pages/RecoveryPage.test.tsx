import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RecoveryEntry } from "../api/types";
import { ACCOUNT, json, mockApi, PAIR, renderApp } from "../test/utils";
import { RecoveryPage } from "./RecoveryPage";

const entry = (over: Partial<RecoveryEntry> & { id: string; name: string }): RecoveryEntry => ({
  kind: "deleted", pair_id: 7, pair_name: "My Drive", original_path: `Docs/${over.name}`, size: 1200,
  day: "2026-10-08", expires_on: "2026-11-08", restorable: true, offload_id: null, google_emptied: false, ...over,
});
const ENTRIES = [
  entry({ id: "7/2026-10-08/.drivesync-deleted-101010-ab12/a.txt", name: "a.txt" }),
  entry({ id: "7/2026-10-07/conflicts/notes.txt", name: "notes.txt", kind: "conflict", expires_on: null }),
  entry({ id: "offload:3", name: "Old", kind: "offload", restorable: false, offload_id: 3, original_path: "Old" }),
  entry({ id: "99/2026-10-01/z.txt", name: "z.txt", kind: "sync_backup", pair_id: null, pair_name: null, original_path: null, restorable: false }),
];

function setup(extra: Record<string, unknown> = {}, route = "/recovery") {
  const api = mockApi({
    "GET /api/accounts": [ACCOUNT],
    "GET /api/sync-pairs": [PAIR],
    "GET /api/recovery": { total: ENTRIES.length, entries: ENTRIES },
    ...extra,
  });
  renderApp(<RecoveryPage />, { route, path: "/recovery", withAccount: true });
  return api;
}

const lastList = (calls: { path: string; url: URL }[]) => calls.filter((c) => c.path === "/api/recovery").at(-1)?.url.searchParams;

describe("Recovery", () => {
  it("says when Google has emptied an offload from its trash, and offers nothing to do on Drive", async () => {
    setup({
      "GET /api/recovery": {
        total: 1,
        entries: [entry({ id: "offload:4", name: "Ancient", kind: "offload", restorable: false, offload_id: 4, original_path: "Ancient", google_emptied: true })],
      },
    });
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Google has emptied this from its trash")).toBeInTheDocument();
    expect(within(table).queryByText("Offloaded, in Drive's trash")).toBeNull();
    expect(within(table).queryByText(/Google empties its trash around/)).toBeNull();
    expect(within(table).queryByRole("link", { name: "Open Offloaded" })).toBeNull();
    expect(within(table).queryByRole("button", { name: /Ancient/ })).toBeNull();
  });

  it("shows where each entry came from, where it goes back, and when it is cleaned up", async () => {
    setup();
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Deleted in CleanSync")).toBeInTheDocument();
    expect(within(table).getByText("Conflict: the version not kept")).toBeInTheDocument();
    expect(within(table).getByText("Offloaded, in Drive's trash")).toBeInTheDocument();
    expect(within(table).getByText("Docs/a.txt")).toBeInTheDocument();
    expect(within(table).getByText("No clean-up date (automatic clean-up is off)")).toBeInTheDocument();
    expect(within(table).getByText(/sync folder was removed/)).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open Offloaded" })).toHaveAttribute("href", "/offloaded");
    expect(within(table).getByRole("checkbox", { name: "Select z.txt" })).toBeDisabled();
  });

  it("restores several and says what happened to each", async () => {
    const { calls } = setup({
      "POST /api/recovery/restore": {
        results: [
          { id: ENTRIES[0].id, restored_to: "Docs/a.txt", error: null },
          { id: ENTRIES[1].id, restored_to: "Docs/notes (restored).txt", error: null },
        ],
        syncs: { 7: "queued" },
      },
    });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select a.txt" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select notes.txt" }));
    await userEvent.click(screen.getByRole("button", { name: "Restore selected (2)" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/recovery/restore")?.body).toEqual({
      account_id: 1, ids: [ENTRIES[0].id, ENTRIES[1].id] }));
    expect(await screen.findByText(/notes \(restored\)\.txt/)).toBeInTheDocument();
    // Said in a live region, so a screen reader hears it.
    expect(screen.getByText(/notes \(restored\)\.txt/).closest("[aria-live]")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Restore selected (0)" })).toBeDisabled();
  });

  it("names an item that could not be restored, with the reason", async () => {
    setup({
      "POST /api/recovery/restore": {
        results: [{ id: ENTRIES[0].id, restored_to: null, error: "The folder it came from can't be used any more. Download it instead." }],
        syncs: {},
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Restore a.txt" }));
    expect(await screen.findByText(/a\.txt: not restored\. The folder it came from can't be used any more/)).toBeInTheDocument();
  });

  it("downloads several as one zip without putting the ids in the address", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { calls } = setup({ "POST /api/recovery/zip": { token: "tok123" } });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select a.txt" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select notes.txt" }));
    await userEvent.click(screen.getByRole("button", { name: "Download selected (2)" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(calls.find((c) => c.path === "/api/recovery/zip")?.body).toEqual({ account_id: 1, ids: [ENTRIES[0].id, ENTRIES[1].id] });
    expect((click.mock.contexts[0] as HTMLAnchorElement).getAttribute("href")).toBe("/api/recovery/zip/tok123");
    click.mockRestore();
  });

  it("says when picked items were cleaned up meanwhile, and refreshes the list", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { calls } = setup({
      "POST /api/recovery/zip": () => json(409, { detail: { message: "1 of the picked items was cleaned up meanwhile.", gone: [ENTRIES[0].id] } }),
    });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select a.txt" }));
    const before = calls.filter((c) => c.path === "/api/recovery").length;
    await userEvent.click(screen.getByRole("button", { name: "Download selected (1)" }));
    expect(await screen.findByText(/1 of the picked items was cleaned up meanwhile\./)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === "/api/recovery").length).toBeGreaterThan(before));
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("shows the plain message when too many are picked for one download", async () => {
    setup({ "POST /api/recovery/zip": () => json(422, { detail: "At most 1000 files can go in one download. Pick fewer." }) });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select a.txt" }));
    await userEvent.click(screen.getByRole("button", { name: "Download selected (1)" }));
    expect(await screen.findByText("At most 1000 files can go in one download. Pick fewer.")).toBeInTheDocument();
  });

  it("checks a single file is still there before downloading it", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { calls } = setup({ "HEAD /api/recovery/file": {} });
    await userEvent.click(await screen.findByRole("button", { name: "Download a.txt" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "HEAD" && c.url.searchParams.get("id") === ENTRIES[0].id)).toBe(true);
    expect((click.mock.contexts[0] as HTMLAnchorElement).getAttribute("href")).toBe(
      `/api/recovery/file?account_id=1&id=${encodeURIComponent(ENTRIES[0].id)}`,
    );
    click.mockRestore();
  });

  it("says so in plain words when a single file was cleaned up meanwhile", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    setup({ "HEAD /api/recovery/file": () => new Response(null, { status: 404 }) });
    await userEvent.click(await screen.findByRole("button", { name: "Download a.txt" }));
    expect(await screen.findByText("a.txt is no longer in Recovery (it was cleaned up or removed).")).toBeInTheDocument();
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("lets an item whose sync folder was removed be downloaded, not restored", async () => {
    setup();
    const table = await screen.findByRole("table");
    expect(within(table).queryByRole("button", { name: "Restore z.txt" })).toBeNull();
    expect(within(table).getByRole("button", { name: "Download z.txt" })).toBeEnabled();
  });

  it("filters by the path it was opened with, and can show everything again", async () => {
    const { calls } = setup({}, "/recovery?path=Docs%2Fa.txt");
    await screen.findByRole("table");
    expect(calls.find((c) => c.path === "/api/recovery")?.url.searchParams.get("path")).toBe("Docs/a.txt");
    expect(screen.getByText(/Only items from/)).toHaveTextContent("Docs/a.txt");
    await userEvent.click(screen.getByRole("button", { name: "Show everything" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/recovery$/));
    await waitFor(() => expect(lastList(calls)?.get("path")).toBe(""));
  });

  it("filters by sync folder and by where it came from", async () => {
    const { calls } = setup();
    await screen.findByRole("table");
    await userEvent.selectOptions(screen.getByLabelText("Came from"), "conflict");
    await waitFor(() => expect(lastList(calls)?.get("kind")).toBe("conflict"));
    await userEvent.selectOptions(await screen.findByLabelText("Sync folder"), "unsynced");
    await waitFor(() => expect(lastList(calls)?.get("pair")).toBe("unsynced"));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("kind=conflict");
  });

  it("asks for more when there are more", async () => {
    const { calls } = setup({ "GET /api/recovery": { total: 250, entries: ENTRIES } });
    await userEvent.click(await screen.findByRole("button", { name: "Show more (246 more)" }));
    await waitFor(() => expect(lastList(calls)?.get("limit")).toBe("400"));
  });

  it("turns restore off while syncing is stopped, and says why", async () => {
    setup({ "GET /api/status": { sync_stopped: "The storage disk is missing." } });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select a.txt" }));
    expect(await screen.findByText(/Restore is paused\. The storage disk is missing\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore selected (1)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Restore a.txt" })).toBeDisabled();
    // Downloading still works.
    expect(screen.getByRole("button", { name: "Download selected (1)" })).toBeEnabled();
  });

  it("says when there is nothing", async () => {
    setup({ "GET /api/recovery": { total: 0, entries: [] } });
    expect(await screen.findByText("Nothing in Recovery")).toBeInTheDocument();
  });
});

describe("mail in Recovery", () => {
  const MAIL = {
    offloads: [{ id: 7, account_id: 1, filter_text: "older_than:1y", state: "DONE", total: 13, done: 13, skipped: 0, not_done: 0, bytes: 8 * 1024 ** 2, reason: null, created_at: "2026-10-10T09:00:00Z", finished_at: "2026-10-10T09:01:00Z", trash_until: "2026-11-09", google_emptied: false }],
    deleted_count: 2,
  };

  it("shows mail offloads and mail deleted in Gmail under Mail", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/recovery/mail": MAIL, "POST /api/mail/restore": { queued: "queued", count: 2 } });
    renderApp(<RecoveryPage />, { withAccount: true, route: "/recovery?source=mail", path: "/recovery" });
    const mail = await screen.findByRole("region", { name: "Mail in Recovery" });
    expect(within(mail).getByRole("link", { name: "older_than:1y" })).toHaveAttribute("href", "/mail/offloads/7");
    expect(within(mail).getByText(/In Gmail's trash until about/)).toBeInTheDocument();
    expect(within(mail).getByText("2 mails were deleted in Gmail and are kept here.")).toBeInTheDocument();
    expect(within(mail).getByRole("link", { name: "Show them" })).toHaveAttribute("href", "/mail?view=deleted");
    await userEvent.click(within(mail).getByRole("button", { name: "Restore all to Gmail" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Put back" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, q: "is:deleted" }));
    expect(calls.some((c) => c.path === "/api/recovery")).toBe(false); // the Files list is not loaded under Mail
  });

  it("asks before putting back mail deleted in Gmail, with the count", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/recovery/mail": MAIL, "POST /api/mail/restore": { queued: "queued", count: 2 } });
    renderApp(<RecoveryPage />, { withAccount: true, route: "/recovery?source=mail", path: "/recovery" });
    const mail = await screen.findByRole("region", { name: "Mail in Recovery" });
    await userEvent.click(within(mail).getByRole("button", { name: "Restore all to Gmail" }));
    const ask = screen.getByRole("dialog", { name: "Put back 2 mails you deleted in Gmail?" });
    await userEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((c) => c.path === "/api/mail/restore")).toBe(false);
    await userEvent.click(within(mail).getByRole("button", { name: "Restore all to Gmail" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Put back" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, q: "is:deleted" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("pages offloads 50 at a time with an offset, and stops at a short page", async () => {
    const off = (id: number) => ({ ...MAIL.offloads[0], id, filter_text: `offload ${id}` });
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/recovery/mail": ({ url }: { url: URL }) => {
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const n = offset === 0 ? 50 : 10;
        return { offloads: Array.from({ length: n }, (_, i) => off(1000 - offset - i)), deleted_count: 0 };
      },
    });
    renderApp(<RecoveryPage />, { withAccount: true, route: "/recovery?source=mail", path: "/recovery" });
    const mail = await screen.findByRole("region", { name: "Mail in Recovery" });
    await within(mail).findByRole("link", { name: "offload 1000" });
    await userEvent.click(within(mail).getByRole("button", { name: "Show more" }));
    expect(await within(mail).findByRole("link", { name: "offload 941" })).toBeInTheDocument();
    expect(within(mail).getByRole("link", { name: "offload 1000" })).toBeInTheDocument(); // the first page stays
    expect(within(mail).getAllByRole("link", { name: /^offload / })).toHaveLength(60);
    expect(within(mail).queryByRole("button", { name: "Show more" })).toBeNull();
    const asked = calls.filter((c) => c.path === "/api/recovery/mail").map((c) => [c.url.searchParams.get("limit"), c.url.searchParams.get("offset")]);
    expect(asked).toEqual([
      ["50", "0"],
      ["50", "50"],
    ]);
  });
});
