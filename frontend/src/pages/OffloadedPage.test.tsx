import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Offload } from "../api/types";
import { RESTORE_NEWER_NOTE } from "../components/FileActions";
import { ACCOUNT, item, json, mockApi, renderApp } from "../test/utils";
import { OffloadedPage, OffloadPickPage } from "./OffloadedPage";

const off = (over: Partial<Offload>): Offload => ({
  id: 1, pair_id: 7, pair_name: "My Drive", account_id: 1, path: "Old", is_dir: true, file_count: 3,
  total_bytes: 2048, state: "ACTIVE", trash_verified: true, trash_emptied: false,
  created_at: "2026-10-09T08:00:00Z", updated_at: "2026-10-09T08:00:00Z", note: null, busy: false, ...over,
});
const STATUS = { accounts: 1, accounts_attention: 0, pairs: 1, new: 0, healthy: 1, attention: 0, running: 0, paused: 0, conflicts: 0, sync_stopped: null };
const STOPPED = "Storage disk is missing — syncing stopped. Reconnect the disk and restart CleanSync.";

function setup(offloads: Offload[], extra: Record<string, unknown> = {}) {
  const api = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/status": STATUS, "GET /api/offloads": offloads, ...extra });
  renderApp(<OffloadedPage />, { route: "/offloaded", path: "/offloaded", withAccount: true });
  return api;
}

describe("Offloaded", () => {
  it("lists offloads with plain states and their notes", async () => {
    setup([
      off({ id: 1 }),
      off({ id: 2, path: "Photos/2019", state: "ATTENTION", trash_verified: false, note: "These files are not in Google Drive's trash: Photos/2019/a.jpg." }),
      off({ id: 3, path: "Tax", state: "RESTORING" }),
      off({ id: 4, path: "Music", trash_emptied: true }),
      off({ id: 5, path: "Video", busy: true }),
    ]);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Offloaded, in Drive's trash")).toBeInTheDocument();
    expect(within(table).getByText("Needs a look")).toBeInTheDocument();
    expect(within(table).getByText("Going back to Google Drive")).toBeInTheDocument();
    expect(within(table).getByText("Offloaded, deleted from Drive's trash")).toBeInTheDocument();
    expect(within(table).getByText("Emptying Drive's trash…")).toBeInTheDocument();
    expect(within(table).getByText(/not in Google Drive's trash/)).toBeInTheDocument();
    // Only an offload whose trash was checked, and not yet emptied, can be emptied.
    expect(screen.getByRole("button", { name: "Empty from trash now: Old" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Empty from trash now: 2019" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Empty from trash now: Music" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Restore to Drive: Tax" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select Tax" })).toBeDisabled();
  });

  it("says so when nothing is offloaded", async () => {
    setup([]);
    expect(await screen.findByText("Nothing is offloaded")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("restores several at once, with one confirm and one message", async () => {
    const { calls } = setup([off({ id: 1 }), off({ id: 2, path: "Tax" })], {
      "POST /api/offloads/1/restore": off({ id: 1, state: "RESTORING" }),
      "POST /api/offloads/2/restore": off({ id: 2, state: "RESTORING" }),
    });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Old" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Tax" }));
    await userEvent.click(screen.getByRole("button", { name: "Restore selected (2)" }));
    const dialog = screen.getByRole("dialog", { name: "Restore 2 items to Drive?" });
    expect(within(dialog).getByText(RESTORE_NEWER_NOTE)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore to Drive" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.path.endsWith("/restore")).map((c) => c.path)).toEqual([
        "/api/offloads/1/restore",
        "/api/offloads/2/restore",
      ]),
    );
    expect(await screen.findByText("2 items are going back to Google Drive with the next sync.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Restore selected (0)" })).toBeDisabled();
  });

  it("selects every restorable offload at once", async () => {
    setup([off({ id: 1 }), off({ id: 2, path: "Tax" }), off({ id: 3, path: "Gone", state: "RESTORING" })]);
    const all = await screen.findByRole("checkbox", { name: "Select all" });
    expect(all).toHaveAttribute("aria-checked", "false");
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Old" }));
    expect(all).toHaveAttribute("aria-checked", "mixed");
    await userEvent.click(all);
    expect(all).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Restore selected (2)" })).toBeEnabled();
    await userEvent.click(all);
    expect(screen.getByRole("button", { name: "Restore selected (0)" })).toBeDisabled();
  });

  it("keeps the dialog open and names what could not be restored", async () => {
    setup([off({ id: 1 }), off({ id: 2, path: "Tax" })], {
      "POST /api/offloads/1/restore": json(409, { detail: "This folder is syncing right now. Try again when the sync finishes." }),
      "POST /api/offloads/2/restore": off({ id: 2, state: "RESTORING" }),
    });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Old" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Tax" }));
    await userEvent.click(screen.getByRole("button", { name: "Restore selected (2)" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restore to Drive" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(/1 of 2 is going back to Google Drive/)).toBeInTheDocument();
    expect(within(dialog).getByText(/syncing right now/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("restores one from its row", async () => {
    const { calls } = setup([off({ id: 1 })], { "POST /api/offloads/1/restore": off({ id: 1, state: "RESTORING" }) });
    await userEvent.click(await screen.findByRole("button", { name: "Restore to Drive: Old" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restore to Drive" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/offloads/1/restore")).toBe(true));
  });

  it("empties one from the trash after a separate confirm", async () => {
    const { calls } = setup([off({ id: 1 })], { "POST /api/offloads/1/empty-trash": off({ id: 1, busy: true }) });
    await userEvent.click(await screen.findByRole("button", { name: "Empty from trash now: Old" }));
    const dialog = screen.getByRole("dialog", { name: "Delete these from Google Drive's trash for good?" });
    expect(within(dialog).getByText(/Nothing else in the trash is touched/)).toBeInTheDocument();
    expect(within(dialog).getByText(/can't be undone/)).toBeInTheDocument();
    // The safe choice has the focus, not the permanent one.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole("button", { name: "Empty from trash now" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/offloads/1/empty-trash")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("can't be closed while the empty-trash request is on its way", async () => {
    let finish: (v: unknown) => void = () => undefined;
    setup([off({ id: 1 })], {
      "POST /api/offloads/1/empty-trash": () => new Promise((r) => (finish = r)),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Empty from trash now: Old" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Empty from trash now" }));
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    finish(off({ id: 1, busy: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows a refused empty-trash in its dialog", async () => {
    setup([off({ id: 1 })], {
      "POST /api/offloads/1/empty-trash": json(409, { detail: "A file here changed since it was offloaded." }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Empty from trash now: Old" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Empty from trash now" }));
    expect(await within(screen.getByRole("dialog")).findByText(/changed since it was offloaded/)).toBeInTheDocument();
  });

  it("explains and turns actions off while syncing is stopped", async () => {
    setup([off({ id: 1 })], { "GET /api/status": { ...STATUS, sync_stopped: STOPPED } });
    expect(await screen.findByText(/Storage disk is missing/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Restore to Drive: Old" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Empty from trash now: Old" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Offload files…" })).toHaveAttribute("aria-disabled", "true");
  });

  it("links to Offload files…", async () => {
    setup([]);
    await userEvent.click(await screen.findByRole("link", { name: "Offload files…" }));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/offloaded/pick");
  });
});

// ---- Offload files… ------------------------------------------------------------------------

const PREVIEW = { items: [], total_files: 5, total_bytes: 3 * 1024 ** 2, stopped: null };
const dir = (path: string, over: Partial<Parameters<typeof item>[0]> = {}) =>
  item({ name: path.split("/").pop()!, path, is_dir: true, size: null, mime: null, ...over });
const LISTINGS: Record<string, ReturnType<typeof item>[]> = {
  "": [
    dir("Work"),
    dir("Loose", { status: "unsynced", pair_id: null }),
    item({ name: "Plan", path: "Plan.docx", local: false, native: "document", status: "native" }),
    item({ name: "notes.txt", path: "notes.txt" }),
    dir("Gone", { status: "offloaded" }),
  ],
  Work: [
    item({ name: "big.mov", path: "Work/big.mov" }),
    item({ name: "small.txt", path: "Work/small.txt" }),
    dir("Work/Sub"),
    item({ name: "Deck", path: "Work/Deck", local: false, native: "presentation", status: "native" }),
  ],
  "Work/Sub": [item({ name: "a.txt", path: "Work/Sub/a.txt" }), item({ name: "b.txt", path: "Work/Sub/b.txt" })],
  Loose: [dir("Loose/Team", { pair_id: 8, pair_root: true })],
  "Loose/Team": [item({ name: "x.txt", path: "Loose/Team/x.txt", pair_id: 8 })],
};

function pickSetup(extra: Record<string, unknown> = {}) {
  const api = mockApi({
    "GET /api/accounts": [ACCOUNT],
    "GET /api/status": STATUS,
    "GET /api/browse": ({ url }: { url: URL }) => {
      const path = url.searchParams.get("path") ?? "";
      return { account_id: 1, path, pair: null, items: LISTINGS[path] ?? [] };
    },
    "POST /api/offloads/preview": PREVIEW,
    ...extra,
  });
  const { client } = renderApp(<OffloadPickPage />, { route: "/offloaded/pick", path: "/offloaded/pick", withAccount: true });
  return { ...api, client };
}
const node = (name: string) => screen.getByRole("treeitem", { name });
/** Click the row itself (not the open/close arrow): picks or unpicks it. */
const clickRow = (name: string) => userEvent.click(node(name).querySelector<HTMLElement>("[data-row]")!);
/** Click the open/close arrow of a folder. */
const clickArrow = (name: string) => userEvent.click(node(name).querySelector<HTMLElement>("[data-arrow]")!);
const previews = (calls: { path: string; body: unknown }[]) => calls.filter((c) => c.path === "/api/offloads/preview");

describe("Offload files…", () => {
  it("is a tree, and only lets synced files and folders be picked", async () => {
    pickSetup();
    const tree = await screen.findByRole("tree", { name: "Your folders" });
    expect(within(tree).getAllByRole("treeitem")).toHaveLength(5);
    expect(await screen.findByRole("treeitem", { name: "Loose" })).toHaveAccessibleDescription("Not synced");
    expect(node("Plan")).toHaveAccessibleDescription("Stays on Drive");
    expect(node("Plan")).toHaveAttribute("aria-disabled", "true");
    expect(node("Gone")).toHaveAccessibleDescription("Offloaded");
    expect(node("Work")).toHaveAttribute("aria-expanded", "false");
    expect(node("notes.txt")).not.toHaveAttribute("aria-expanded");
    await clickRow("Plan");
    await clickRow("Gone");
    expect(node("Plan")).toHaveAttribute("aria-checked", "false");
    expect(node("Gone")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: "Offload selected (0)…" })).toBeDisabled();
  });

  it("shows a folder as partly picked, and picking the folder covers everything in it", async () => {
    const { calls } = pickSetup();
    await screen.findByRole("tree");
    await clickArrow("Work");
    expect(node("Work")).toHaveAttribute("aria-expanded", "true");
    expect(within(node("Work")).getByRole("group")).toBeInTheDocument();
    await screen.findByRole("treeitem", { name: "big.mov" });
    await clickRow("big.mov");
    expect(node("big.mov")).toHaveAttribute("aria-checked", "true");
    expect(node("Work")).toHaveAttribute("aria-checked", "mixed");
    await clickRow("Work");
    expect(node("Work")).toHaveAttribute("aria-checked", "true");
    expect(node("small.txt")).toHaveAttribute("aria-checked", "true");
    expect(node("Deck")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: "Offload selected (1)…" })).toBeEnabled();
    // Taking one file back out leaves the rest of the folder picked.
    await clickRow("big.mov");
    expect(node("Work")).toHaveAttribute("aria-checked", "mixed");
    expect(node("small.txt")).toHaveAttribute("aria-checked", "true");
    expect(node("big.mov")).toHaveAttribute("aria-checked", "false");
    await userEvent.click(screen.getByRole("button", { name: "Offload selected (2)…" }));
    await screen.findByRole("dialog", { name: "Offload 2 items?" });
    await waitFor(() =>
      expect(previews(calls).at(-1)?.body).toEqual({ account_id: 1, paths: ["Work/small.txt", "Work/Sub"] }),
    );
  });

  it("splits a picked folder two levels down", async () => {
    pickSetup();
    await screen.findByRole("tree");
    await clickRow("Work");
    await clickArrow("Work");
    await screen.findByRole("treeitem", { name: "Sub" });
    await clickArrow("Sub");
    await screen.findByRole("treeitem", { name: "a.txt" });
    await clickRow("a.txt");
    expect(node("a.txt")).toHaveAttribute("aria-checked", "false");
    expect(node("b.txt")).toHaveAttribute("aria-checked", "true");
    expect(node("Sub")).toHaveAttribute("aria-checked", "mixed");
    expect(node("Work")).toHaveAttribute("aria-checked", "mixed");
    expect(node("big.mov")).toHaveAttribute("aria-checked", "true");
    // big.mov, small.txt and Sub/b.txt are picked; Deck stays on Drive.
    expect(screen.getByRole("button", { name: "Offload selected (3)…" })).toBeEnabled();
  });

  it("asks for each listing it needs to split a picked folder (loading one that isn't there)", async () => {
    const { client } = pickSetup();
    await screen.findByRole("tree");
    await clickRow("Work");
    await clickArrow("Work");
    await screen.findByRole("treeitem", { name: "Sub" });
    await clickArrow("Sub");
    await screen.findByRole("treeitem", { name: "a.txt" });
    const fetchQuery = vi.spyOn(client, "fetchQuery");
    await clickRow("a.txt");
    await waitFor(() => expect(node("a.txt")).toHaveAttribute("aria-checked", "false"));
    expect(fetchQuery.mock.calls.map(([o]) => o.queryKey)).toEqual([
      ["browse", 1, "Work"],
      ["browse", 1, "Work/Sub"],
    ]);
    expect(screen.getByRole("button", { name: "Offload selected (3)…" })).toBeEnabled();
  });

  it("says so when a needed listing can't be loaded, and keeps the folder picked", async () => {
    const { client } = pickSetup();
    await screen.findByRole("tree");
    await clickRow("Work");
    await clickArrow("Work");
    await screen.findByRole("treeitem", { name: "big.mov" });
    vi.spyOn(client, "fetchQuery").mockRejectedValueOnce(new Error("Disk read failed."));
    await clickRow("big.mov");
    expect(await screen.findByText(/Couldn't leave out big.mov: Disk read failed/)).toBeInTheDocument();
    expect(node("Work")).toHaveAttribute("aria-checked", "true");
    expect(node("big.mov")).toHaveAttribute("aria-checked", "true");
  });

  it("shows a folder that isn't synced as partly picked when a synced folder inside it has picks", async () => {
    pickSetup();
    await screen.findByRole("tree");
    await clickArrow("Loose");
    await screen.findByRole("treeitem", { name: "Team" });
    await clickArrow("Team");
    await screen.findByRole("treeitem", { name: "x.txt" });
    await clickRow("x.txt");
    expect(node("x.txt")).toHaveAttribute("aria-checked", "true");
    expect(node("Team")).toHaveAttribute("aria-checked", "mixed");
    expect(node("Loose")).toHaveAttribute("aria-checked", "mixed");
  });

  it("works with the keyboard: one Tab stop, arrows move and open, Space picks", async () => {
    pickSetup();
    await screen.findByRole("tree");
    const items = screen.getAllByRole("treeitem");
    expect(items.filter((el) => el.tabIndex === 0)).toEqual([node("Work")]);
    await userEvent.tab(); // the "Offloaded" link
    await userEvent.tab();
    expect(node("Work")).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(node("Work")).toHaveAttribute("aria-expanded", "true");
    await screen.findByRole("treeitem", { name: "big.mov" });
    await userEvent.keyboard("{ArrowRight}");
    expect(node("big.mov")).toHaveFocus();
    await userEvent.keyboard(" ");
    expect(node("big.mov")).toHaveAttribute("aria-checked", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(node("small.txt")).toHaveFocus();
    expect(screen.getAllByRole("treeitem").filter((el) => el.tabIndex === 0)).toEqual([node("small.txt")]);
    await userEvent.keyboard("{ArrowUp}{ArrowLeft}");
    expect(node("Work")).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(node("Work")).toHaveAttribute("aria-expanded", "false");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(node("notes.txt")).toHaveFocus();
    await userEvent.keyboard(" ");
    expect(node("notes.txt")).toHaveAttribute("aria-checked", "true");
    await userEvent.keyboard("{Home}");
    expect(node("Work")).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(node("Gone")).toHaveFocus();
    // Tab leaves the tree in one step.
    await userEvent.tab();
    expect(screen.getAllByRole("treeitem").some((el) => el === document.activeElement)).toBe(false);
  });

  it("closing a folder with the focus inside it moves the focus stop to the folder", async () => {
    pickSetup();
    await screen.findByRole("tree");
    await clickArrow("Work");
    await screen.findByRole("treeitem", { name: "big.mov" });
    await clickRow("big.mov");
    expect(node("big.mov")).toHaveFocus();
    await clickArrow("Work");
    expect(screen.getAllByRole("treeitem").filter((el) => el.tabIndex === 0)).toEqual([node("Work")]);
  });

  it("picks across folders, shows the total, then offloads them together", async () => {
    const { calls } = pickSetup();
    await screen.findByRole("tree");
    await clickArrow("Work");
    await screen.findByRole("treeitem", { name: "big.mov" });
    await clickRow("big.mov");
    await clickRow("notes.txt");
    expect(await screen.findByText(/5 files, 3.0 MB freed on Google Drive/)).toBeInTheDocument();
    expect(screen.getByText(/2 items picked/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Offload selected (2)…" }));
    await screen.findByRole("dialog", { name: "Offload 2 items?" });
    await waitFor(() => expect(previews(calls).at(-1)?.body).toEqual({ account_id: 1, paths: ["Work/big.mov", "notes.txt"] }));
  });

  it("cancels a total that is no longer wanted, and does not ask again on window focus", async () => {
    const { client } = pickSetup();
    await screen.findByRole("tree");
    await clickRow("notes.txt");
    await screen.findByText(/5 files, 3.0 MB freed/);
    const fetchMock = vi.mocked(fetch);
    const init = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/offloads/preview"))?.[1];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    const q = client.getQueryCache().find({ queryKey: ["offload-preview"], exact: false });
    expect((q?.options as { refetchOnWindowFocus?: unknown }).refetchOnWindowFocus).toBe(false);
  });

  it("keeps the picks when the confirm is cancelled, and clears them once an offload starts", async () => {
    const batch = { id: "b1", account_id: 1, done: true, items: [{ path: "notes.txt", state: "done", message: null, offload_id: 9 }] };
    pickSetup({
      "POST /api/offloads/preview": {
        items: [{ path: "notes.txt", pair_id: 7, pair_name: "My Drive", is_dir: false, file_count: 1, total_bytes: 1200, stays_on_drive: [], blocked: null }],
        total_files: 1,
        total_bytes: 1200,
        stopped: null,
      },
      "POST /api/offloads": batch,
      "GET /api/offloads/batches/b1": batch,
    });
    await screen.findByRole("tree");
    await clickRow("notes.txt");
    await userEvent.click(screen.getByRole("button", { name: "Offload selected (1)…" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Offload selected (1)…" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Offload selected (1)…" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Offload" })).toBeEnabled());
    await userEvent.click(within(dialog).getByRole("button", { name: "Offload" }));
    await userEvent.click(await within(screen.getByRole("dialog")).findByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: "Offload selected (0)…" })).toBeDisabled();
  });

  it("turns Offload off while syncing is stopped", async () => {
    pickSetup({ "GET /api/status": { ...STATUS, sync_stopped: STOPPED } });
    expect(await screen.findByText(/Storage disk is missing/)).toBeInTheDocument();
    await screen.findByRole("tree");
    await clickRow("notes.txt");
    expect(screen.getByRole("button", { name: "Offload selected (1)…" })).toBeDisabled();
  });
});
