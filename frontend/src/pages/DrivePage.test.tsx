import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, item, json, mockApi, PAIR, renderApp } from "../test/utils";
import { DrivePage } from "./DrivePage";
import { RecentPage, SearchPage } from "./ListPages";

const ITEMS = [
  item({ name: "Work", path: "Work", is_dir: true, size: null, status: "upload", mime: null }),
  item({ name: "photo.jpg", path: "photo.jpg", mime: "image/jpeg", thumb: true }),
  item({ name: "Budget.xlsx", path: "Budget.xlsx", mime: "application/vnd.ms-excel", google_url: "https://docs.google.com/spreadsheets/d/S1/edit?rtpof=true&sd=true" }),
  item({ name: "Plan", path: "Plan.docx", local: false, native: "document", status: "native", google_url: "https://docs.google.com/document/d/D1/edit" }),
  item({ name: "incoming.pdf", path: "incoming.pdf", local: false, status: "download", mime: "application/pdf" }),
  item({ name: "notes.conflict1.txt", path: "notes.conflict1.txt", status: "conflict" }),
];

function browse(path = "", items = ITEMS) {
  return { account_id: 1, path, pair: { id: 7, name: "My Drive", status: "HEALTHY", initialized: true, local_subpath: "" }, items };
}

function setup(route = "/drive", extra: Record<string, unknown> = {}) {
  const api = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/browse": ({ url }: { url: URL }) => browse(url.searchParams.get("path") ?? ""), ...extra });
  const open = vi.fn(() => null);
  vi.stubGlobal("open", open);
  renderApp(<DrivePage />, { route, path: "/drive", withAccount: true });
  return { ...api, open };
}

describe("My Drive", () => {
  it("shows folders then files with their sync state", async () => {
    setup();
    const folders = await screen.findByRole("region", { name: "Folders" });
    expect(within(folders).getByText("Work")).toBeInTheDocument();
    expect(within(folders).getByLabelText("Waiting to upload")).toBeInTheDocument();
    const files = screen.getByRole("region", { name: "Files" });
    expect(within(files).getByLabelText("Google file (lives in Google Drive)")).toBeInTheDocument();
    expect(within(files).getByLabelText("Waiting to download")).toBeInTheDocument();
    expect(within(files).getByLabelText("Conflict: two versions")).toBeInTheDocument();
    expect(files.querySelector("img")?.getAttribute("src")).toBe("/api/thumbs/1/photo.jpg?v=2026-10-08T07%3A00%3A00Z");
  });

  it("opens folders, files, Office files and Google files the right way", async () => {
    const { open } = setup();
    await userEvent.dblClick(await screen.findByText("photo.jpg"));
    expect(open).toHaveBeenLastCalledWith("/files/1/photo.jpg", "_blank", "noopener,noreferrer");
    await userEvent.dblClick(screen.getByText("Budget.xlsx"));
    expect(open).toHaveBeenLastCalledWith("https://docs.google.com/spreadsheets/d/S1/edit?rtpof=true&sd=true", "_blank", "noopener,noreferrer");
    await userEvent.dblClick(screen.getByText("Plan"));
    expect(open).toHaveBeenLastCalledWith("https://docs.google.com/document/d/D1/edit", "_blank", "noopener,noreferrer");
    await userEvent.dblClick(screen.getByText("Work"));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive?path=Work"));
  });

  it("says an Office file opens in Google after the next sync, and offers Sync now", async () => {
    const { calls } = setup("/drive", {
      "GET /api/browse": browse("", [item({ name: "New.docx", path: "New.docx", status: "upload", remote: false })]),
      "POST /api/sync-pairs/7/sync": { result: "queued" },
    });
    await userEvent.dblClick(await screen.findByText("New.docx"));
    expect(screen.getByText("It opens in Google after the next sync.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/sync-pairs/7/sync")).toBe(true));
  });

  it("renames from the right-click menu", async () => {
    const { calls } = setup("/drive", { "POST /api/files/rename": { paths: ["notes.txt"], syncs: {} } });
    const target = await screen.findByText("notes.conflict1.txt");
    await userEvent.pointer({ keys: "[MouseRight]", target });
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByLabelText("New name");
    await userEvent.clear(input);
    await userEvent.type(input, "notes.txt");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/files/rename")?.body).toEqual({ account_id: 1, path: "notes.conflict1.txt", new_name: "notes.txt" }),
    );
  });

  it("deletes into Recovery after a clear warning, also with the Delete key", async () => {
    const { calls } = setup("/drive", { "POST /api/files/delete": { paths: ["x"], syncs: {} } });
    await userEvent.click(await screen.findByText("photo.jpg"));
    await userEvent.keyboard("{Delete}");
    const dialog = screen.getByRole("dialog", { name: 'Delete "photo.jpg"?' });
    expect(dialog).toHaveTextContent("Recovery");
    expect(dialog).toHaveTextContent("Drive's trash");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/files/delete")?.body).toEqual({ account_id: 1, paths: ["photo.jpg"] }));
  });

  it("only offers local actions for items that are on this server", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "More actions for incoming.pdf" }));
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeDisabled();
  });

  it("moves an item with the folder picker", async () => {
    const { calls } = setup("/drive", {
      "GET /api/local-browse": [{ name: "Work", path: "Work" }],
      "POST /api/files/move": { paths: ["Work/photo.jpg"], syncs: {} },
    });
    await userEvent.click(await screen.findByRole("button", { name: "More actions for photo.jpg" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Move" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Work" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Move here" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/files/move")?.body).toEqual({ account_id: 1, paths: ["photo.jpg"], to: "Work" }));
  });

  it("won't move into a folder that isn't synced, and says why", async () => {
    setup("/drive", {
      "GET /api/local-browse": [{ name: "Loose", path: "Loose" }],
      "GET /api/browse": ({ url }: { url: URL }) =>
        url.searchParams.get("path") === "Loose" ? { account_id: 1, path: "Loose", pair: null, items: [] } : browse(""),
    });
    await userEvent.click(await screen.findByRole("button", { name: "More actions for photo.jpg" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Move" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Loose" }));
    expect(await within(dialog).findByText("This folder isn't synced with Google Drive. Open a synced folder to move items.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move here" })).toBeDisabled();
  });

  it("downloads folders as zip", async () => {
    setup();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    await userEvent.click(await screen.findByRole("button", { name: "More actions for Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Download as zip" }));
    expect((click.mock.contexts[0] as HTMLAnchorElement).getAttribute("href")).toBe("/api/files/zip?account_id=1&path=Work");
  });

  it("switches between grid and list and remembers it", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "Show as list" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(localStorage.getItem("drivesync.view")).toBe("list");
  });

  it("shows the folder path as breadcrumbs", async () => {
    setup("/drive?path=Work%2FDeep");
    const nav = await screen.findByRole("navigation", { name: "Folder path" });
    expect(within(nav).getByRole("link", { name: "Work" })).toHaveAttribute("href", "/drive?path=Work");
    expect(within(nav).getByText("Deep")).toHaveAttribute("aria-current", "page");
  });

  it("shows an item's history in Recovery", async () => {
    setup();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "photo.jpg" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Show in Recovery" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/recovery?path=photo.jpg"));
  });

  it("offers Show in Recovery for an item only on Google Drive too", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "More actions for incoming.pdf" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Show in Recovery" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/recovery?path=incoming.pdf"));
  });
});

describe("Not synced", () => {
  it("marks local-only items outside synced folders as Not synced and says New is off", async () => {
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [
        item({ name: "Loose", path: "Loose", is_dir: true, size: null, status: "unsynced", pair_id: null, remote: false, mime: null }),
      ] },
    });
    const folders = await screen.findByRole("region", { name: "Folders" });
    // The state is drawn on the folder icon, not a pill that squeezes the name.
    expect(within(folders).queryByText("Not synced")).not.toBeInTheDocument();
    expect(within(folders).getByTitle(/Not synced: only on this server/)).toBeInTheDocument();
    expect(within(folders).getByTitle("Loose")).toBeInTheDocument();
    expect(
      screen.getByText(
        "My Drive as a whole isn't synced. Only folders with the green sync mark are kept in step with Google Drive. Open one to add files.",
      ),
    ).toBeInTheDocument();
  });

  it("says a folder below the top isn't synced", async () => {
    setup("/drive?path=Loose", {
      "GET /api/browse": { account_id: 1, path: "Loose", pair: null, items: [
        item({ name: "a.txt", path: "Loose/a.txt", status: "unsynced", pair_id: null, remote: false }),
      ] },
    });
    expect(await screen.findByText("This folder isn't synced with Google Drive. Open a synced folder to add files.")).toBeInTheDocument();
  });
});

describe("Recent and Search", () => {
  it("lists recent files with their folder", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/recent": [item({ name: "a.txt", path: "Work/a.txt" })] });
    renderApp(<RecentPage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Show as list" }));
    expect(await screen.findByRole("cell", { name: "Work" })).toBeInTheDocument();
  });

  it("searches with the query from the address", async () => {
    const { calls } = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/search": [item({ name: "report.pdf", path: "report.pdf" })] });
    renderApp(<SearchPage />, { withAccount: true, route: "/search?q=report", path: "/search" });
    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/search")?.url.searchParams.get("q")).toBe("report");
  });
});

describe("Menus and errors", () => {
  it("Escape closes the menu and puts focus back on the button", async () => {
    setup();
    const more = await screen.findByRole("button", { name: "More actions for photo.jpg" });
    await userEvent.click(more);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(more).toHaveFocus();
  });

  it("moves between menu items with the arrow keys", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "More actions for photo.jpg" }));
    expect(screen.getByRole("menuitem", { name: "Open" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Download" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Show in Recovery" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveFocus();
  });

  it("pressing Enter on the more button opens the menu, not the file", async () => {
    const { open } = setup();
    const more = await screen.findByRole("button", { name: "More actions for photo.jpg" });
    more.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
  });

  it("shows what was done and what failed when a delete stops half way", async () => {
    const { json } = await import("../test/utils");
    setup("/drive", {
      "POST /api/files/delete": () => json(409, { detail: { message: "Some items could not be deleted.", done: ["a.txt"], failed: ["b.txt"], not_done: ["c.txt"] } }),
    });
    await userEvent.click(await screen.findByText("photo.jpg"));
    await userEvent.keyboard("{Delete}");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Some items could not be deleted.");
    expect(alert).toHaveTextContent("a.txt");
    expect(alert).toHaveTextContent("b.txt");
    expect(alert).toHaveTextContent("c.txt");
  });

  it("shows the server's message when a name is refused", async () => {
    const { json } = await import("../test/utils");
    setup("/drive", { "POST /api/files/rename": () => json(422, { detail: "Names cannot contain a colon." }) });
    await userEvent.pointer({ keys: "[MouseRight]", target: await screen.findByText("photo.jpg") });
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByLabelText("New name");
    await userEvent.clear(input);
    await userEvent.type(input, "a:b");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Names cannot contain a colon.");
  });
});

describe("Accessible tiles and keyboard", () => {
  it("keeps the more button next to the tile, not inside it, and describes the status", async () => {
    setup();
    const tile = await screen.findByRole("button", { name: "incoming.pdf" });
    const more = screen.getByRole("button", { name: "More actions for incoming.pdf" });
    expect(tile).not.toContainElement(more);
    expect(tile).toHaveAccessibleDescription("Waiting to download");
  });

  it("has one tab stop for the grid and moves with the arrow keys", async () => {
    setup();
    await screen.findByRole("button", { name: "Work" });
    const stops = screen.getAllByRole("button").filter((b) => b.getAttribute("tabindex") === "0" && b.hasAttribute("data-nav"));
    expect(stops).toHaveLength(1);
    screen.getByRole("button", { name: "Work" }).focus();
    expect(screen.getByRole("button", { name: "Work" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("button", { name: "notes.conflict1.txt" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("button", { name: "Work" })).toHaveFocus();
  });

  it("Enter opens, Space selects, and Delete asks to delete the focused item", async () => {
    const { open } = setup();
    await screen.findByRole("button", { name: "Work" });
    screen.getByRole("button", { name: "Work" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "true");
    // Space adds or removes the focused item from the selection.
    await userEvent.keyboard(" ");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.keyboard(" ");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.keyboard("{Enter}");
    expect(open).toHaveBeenLastCalledWith("/files/1/photo.jpg", "_blank", "noopener,noreferrer");
    await userEvent.keyboard("{Delete}");
    expect(screen.getByRole("dialog", { name: 'Delete "photo.jpg"?' })).toBeInTheDocument();
  });

  it("Shift+F10 opens the item menu", async () => {
    setup();
    await screen.findByRole("button", { name: "Work" });
    screen.getByRole("button", { name: "Work" }).focus();
    await userEvent.keyboard("{Shift>}{F10}{/Shift}");
    expect(screen.getByRole("menu", { name: "Actions for Work" })).toBeInTheDocument();
  });

  it("ignores the Delete key in a text box and in a menu", async () => {
    setup();
    await userEvent.click(await screen.findByText("photo.jpg"));
    const box = document.createElement("input");
    document.body.appendChild(box);
    box.focus();
    await userEvent.keyboard("{Delete}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    box.remove();
    await userEvent.click(screen.getByRole("button", { name: "More actions for photo.jpg" }));
    await userEvent.keyboard("{Delete}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("list rows have the same name, status and keyboard behaviour", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "Show as list" }));
    const row = screen.getByRole("button", { name: "incoming.pdf" });
    expect(row).toHaveAccessibleDescription("Waiting to download");
    expect(row).not.toContainElement(screen.getByRole("button", { name: "More actions for incoming.pdf" }));
    expect(screen.getByRole("table").querySelector("[aria-selected]")).toBeNull();
  });
});

describe("Safer opening and URLs", () => {
  it("opens only https Google links", async () => {
    const { open } = setup("/drive", {
      "GET /api/browse": browse("", [item({ name: "Bad.xlsx", path: "Bad.xlsx", google_url: "javascript:alert(1)" })]),
    });
    await userEvent.dblClick(await screen.findByText("Bad.xlsx"));
    expect(open).not.toHaveBeenCalled();
  });

  it("explains a Google file that has no link yet", async () => {
    setup("/drive", { "GET /api/browse": browse("", [item({ name: "Doc", path: "Doc.docx", local: false, native: "document", status: "native" })]) });
    await userEvent.dblClick(await screen.findByText("Doc"));
    expect(screen.getByText("This Google file has no link yet. Try again after the next sync.")).toBeInTheDocument();
  });

  it("encodes #, ?, % and unicode in file and thumbnail addresses", async () => {
    const name = "a#b?c%d é.jpg";
    const { open } = setup("/drive", { "GET /api/browse": browse("", [item({ name, path: name, mime: "image/jpeg", thumb: true })]) });
    const tile = await screen.findByRole("button", { name });
    expect(tile.querySelector("img")?.getAttribute("src")).toBe("/api/thumbs/1/a%23b%3Fc%25d%20%C3%A9.jpg?v=2026-10-08T07%3A00%3A00Z");
    await userEvent.dblClick(tile);
    expect(open).toHaveBeenLastCalledWith("/files/1/a%23b%3Fc%25d%20%C3%A9.jpg", "_blank", "noopener,noreferrer");
  });

  it("Sync now also refreshes pairs and status", async () => {
    mockApi({
      "GET /api/accounts": [ACCOUNT],
      "GET /api/browse": browse("", [item({ name: "New.docx", path: "New.docx", status: "upload", remote: false })]),
      "POST /api/sync-pairs/7/sync": { result: "queued" },
    });
    vi.stubGlobal("open", vi.fn());
    const { client } = renderApp(<DrivePage />, { route: "/drive", path: "/drive", withAccount: true });
    const spy = vi.spyOn(client, "invalidateQueries");
    await userEvent.dblClick(await screen.findByText("New.docx"));
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0]);
      expect(keys).toEqual(expect.arrayContaining(["pairs", "status"]));
    });
  });
});

describe("FolderPicker", () => {
  it("asks only for the selected source", async () => {
    const api = mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/local-browse": [], "GET /api/remote-browse": [] });
    const { FolderPicker } = await import("../components/FolderPicker");
    const { unmount } = renderApp(<FolderPicker source="local" accountId={1} value="" onChange={() => undefined} rootLabel="Here" />);
    await screen.findByText("No folders inside. This one is selected.");
    expect(api.calls.some((c) => c.path === "/api/remote-browse")).toBe(false);
    unmount();
    api.calls.length = 0;
    renderApp(<FolderPicker source="remote" accountId={1} value="" onChange={() => undefined} rootLabel="There" />);
    await screen.findByText("No folders inside. This one is selected.");
    expect(api.calls.some((c) => c.path === "/api/local-browse")).toBe(false);
  });
});

describe("Loading states", () => {
  it("says Loading on Starred and Search", async () => {
    const { StarredPage } = await import("./ListPages");
    mockApi({ "GET /api/accounts": [ACCOUNT], "GET /api/starred": () => new Promise(() => undefined) });
    renderApp(<StarredPage />, { withAccount: true });
    expect(await screen.findByText("Loading")).toBeInTheDocument();
  });
});

describe("Final review fixes", () => {
  const oneWay = (path = "") => ({
    account_id: 1,
    path,
    pair: { id: 7, name: "Down", status: "HEALTHY", initialized: true, local_subpath: "", direction: "REMOTE_TO_LOCAL" },
    items: ITEMS,
  });

  it("explains a Drive-to-here folder and offers no local changes in it", async () => {
    const { PAIR } = await import("../test/utils");
    setup("/drive", { "GET /api/browse": oneWay(), "GET /api/sync-pairs": [{ ...PAIR, direction: "REMOTE_TO_LOCAL" }] });
    expect(await screen.findByText(/only receives changes from Google Drive/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "More actions for photo.jpg" }));
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDisabled());
    expect(screen.getByRole("menuitem", { name: "Move" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Download" })).toBeEnabled();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByText("photo.jpg"));
    await userEvent.keyboard("{Delete}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers Sync now when a rename waits for changes to sync", async () => {
    const { json } = await import("../test/utils");
    const { calls } = setup("/drive", {
      "POST /api/files/rename": () => json(409, { detail: "This has changes waiting to sync. Sync now, then try again." }),
      "POST /api/sync-pairs/7/sync": { result: "queued" },
    });
    await userEvent.pointer({ keys: "[MouseRight]", target: await screen.findByText("photo.jpg") });
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    await userEvent.type(screen.getByLabelText("New name"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("changes waiting to sync");
    await userEvent.click(within(alert).getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/sync-pairs/7/sync")).toBe(true));
  });

  it("copes with a half-done answer that names the failed item as plain text", async () => {
    const { json } = await import("../test/utils");
    setup("/drive", {
      "POST /api/files/delete": () => json(403, { detail: { message: "Stopped half way.", done: ["a.txt"], failed: "b.txt", not_done: [] } }),
    });
    await userEvent.click(await screen.findByText("photo.jpg"));
    await userEvent.keyboard("{Delete}");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Failed: b.txt");
    expect(alert).toHaveTextContent("Done: a.txt");
  });

  it("says Drive was renamed at the same time for items already on Drive", async () => {
    setup("/drive", { "POST /api/files/rename": { paths: ["notes.txt"], syncs: {}, drive: ["notes.txt"] } });
    await userEvent.pointer({ keys: "[MouseRight]", target: await screen.findByText("Work") });
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveTextContent("deletion limit");
    expect(dialog).toHaveTextContent("renamed on Google Drive at the same time");
    await userEvent.type(screen.getByLabelText("New name"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(await screen.findByText("Renamed, on Google Drive too.")).toBeInTheDocument();
  });

  it("says a rename reaches Drive at the next sync for items not there yet", async () => {
    setup("/drive", {
      "GET /api/browse": browse("", [item({ name: "new.txt", path: "new.txt", status: "upload", remote: false })]),
      "POST /api/files/rename": { paths: ["new2.txt"], syncs: {}, drive: [] },
    });
    await userEvent.pointer({ keys: "[MouseRight]", target: await screen.findByText("new.txt") });
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Google Drive gets the new name at the next sync");
    await userEvent.type(screen.getByLabelText("New name"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(await screen.findByText("Renamed. Google Drive gets the change at the next sync.")).toBeInTheDocument();
  });
});

describe("Adding folders to sync", () => {
  const LOOSE = item({ name: "Loose", path: "Loose", is_dir: true, size: null, status: "unsynced", pair_id: null, remote: false, mime: null });

  it("marks synced folders and not-synced folders", async () => {
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [
        item({ name: "Projects", path: "Projects", is_dir: true, size: null, pair_root: true, pair_id: null, mime: null }),
        LOOSE,
      ] },
    });
    const folders = await screen.findByRole("region", { name: "Folders" });
    expect(within(folders).getByRole("img", { name: "Synced folder" })).toBeInTheDocument();
    expect(within(folders).getByTitle(/Not synced: only on this server/)).toBeInTheDocument();
    // A synced folder can't be started again.
    fireEvent.contextMenu(screen.getByRole("button", { name: "Projects" }));
    expect(screen.queryByRole("menuitem", { name: "Start syncing this folder" })).not.toBeInTheDocument();
  });

  it("marks folders in the list view too", async () => {
    localStorage.setItem("drivesync.view", "list");
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [
        item({ name: "Projects", path: "Projects", is_dir: true, size: null, pair_root: true, pair_id: null, mime: null }),
        LOOSE,
      ] },
    });
    expect(await screen.findByRole("img", { name: "Synced folder" })).toBeInTheDocument();
    expect(screen.getByTitle(/Not synced: only on this server/)).toBeInTheDocument();
    localStorage.removeItem("drivesync.view");
  });

  it("starts syncing a local folder and goes to its preview", async () => {
    const { calls } = setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [LOOSE] },
      "POST /api/sync-pairs": { ...PAIR, id: 12, name: "Loose", local_subpath: "Loose", remote_path: "Loose", initialized: false, status: "NEW" },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Loose" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Start syncing this folder" }));
    const dialog = screen.getByRole("dialog", { name: 'Start syncing "Loose"?' });
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/setup?step=preview&pair=12"));
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/sync-pairs")?.body).toEqual({
      account_id: 1, name: "Loose", remote_path: "Loose", local_subpath: "Loose" });
  });

  it("shows why a folder can't start syncing", async () => {
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [LOOSE] },
      "POST /api/sync-pairs": () => json(422, { detail: "local folder overlaps the folder of pair 'Docs'" }),
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Loose" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Start syncing this folder" }));
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/overlaps the folder of pair 'Docs'/)).toBeInTheDocument();
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/drive");
  });

  it.each([["grid"], ["list"]])("tells screen readers what the folder marks mean (%s)", async (view) => {
    localStorage.setItem("drivesync.view", view);
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [
        item({ name: "Projects", path: "Projects", is_dir: true, size: null, pair_root: true, pair_id: null, mime: null }),
        LOOSE,
      ] },
    });
    expect(await screen.findByRole("button", { name: "Loose" })).toHaveAccessibleDescription(
      "Not synced: only on this server. Open its menu to start syncing it.",
    );
    expect(screen.getByRole("button", { name: "Projects" })).toHaveAccessibleDescription(
      "Synced. Synced folder: kept in step with Google Drive.",
    );
    localStorage.removeItem("drivesync.view");
  });

  it("can't be cancelled half way, and a late answer after closing goes nowhere", async () => {
    let answer: (r: Response) => void = () => {};
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [LOOSE] },
      "POST /api/sync-pairs": () => new Promise<Response>((r) => (answer = r)),
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Loose" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Start syncing this folder" }));
    const dialog = screen.getByRole("dialog", { name: 'Start syncing "Loose"?' });
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    answer(json(201, { ...PAIR, id: 12, name: "Loose", local_subpath: "Loose", remote_path: "Loose", initialized: false, status: "NEW" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/drive$/);
  });

  it("offers Sync a Drive folder when nothing is synced yet", async () => {
    setup("/drive", { "GET /api/sync-pairs": [] });
    const link = await screen.findByRole("link", { name: "Sync a Drive folder…" });
    expect(link).toHaveAttribute("href", "/setup?step=folders&account=1");
  });

  it("doesn't offer it once the account has a synced folder", async () => {
    setup("/drive", { "GET /api/sync-pairs": [PAIR] });
    await screen.findByRole("region", { name: "Folders" });
    await waitFor(() => expect(screen.queryByRole("link", { name: "Sync a Drive folder…" })).not.toBeInTheDocument());
  });
});

describe("Several items, offload and restore", () => {
  const PREVIEW = {
    items: [
      { path: "Work", pair_id: 7, pair_name: "My Drive", is_dir: true, file_count: 12, total_bytes: 3 * 1024 ** 2, stays_on_drive: ["Work/Plan"], blocked: null },
      { path: "photo.jpg", pair_id: 7, pair_name: "My Drive", is_dir: false, file_count: 0, total_bytes: 0, stays_on_drive: [], blocked: "This has an open conflict. Resolve it in Conflicts first." },
    ],
    total_files: 12,
    total_bytes: 3 * 1024 ** 2,
    stopped: null,
  };
  const OFFLOAD = {
    id: 3, pair_id: 7, pair_name: "My Drive", account_id: 1, path: "Work/Old", is_dir: true, file_count: 2, total_bytes: 5, state: "ACTIVE",
    trash_verified: true, trash_emptied: false, created_at: "2026-10-09T08:00:00Z", updated_at: "2026-10-09T08:00:00Z", note: null, busy: false,
  };

  it("selects several items with Ctrl-click and offloads them from the right-click menu", async () => {
    let polls = 0;
    const { calls } = setup("/drive", {
      "POST /api/offloads/preview": PREVIEW,
      "POST /api/offloads": { id: "b1", account_id: 1, done: false, items: [{ path: "Work", state: "waiting", message: null, offload_id: null }] },
      "GET /api/offloads/batches/b1": () => ({
        id: "b1", account_id: 1, done: ++polls > 1,
        items: [{ path: "Work", state: polls > 1 ? "done" : "running", message: polls > 1 ? "Removed from Google Drive (it is in Drive's trash). Kept on this server." : null, offload_id: 3 }],
      }),
    });
    const work = await screen.findByRole("button", { name: "Work" });
    fireEvent.click(work);
    fireEvent.click(screen.getByRole("button", { name: "photo.jpg" }), { ctrlKey: true });
    expect(work).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.contextMenu(work);
    const menu = screen.getByRole("menu", { name: "Actions for 2 items" });
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: "Offload 2 items?" });
    expect(await within(dialog).findByText(/12 files, 3.0 MB freed on Google Drive/)).toBeInTheDocument();
    expect(within(dialog).getByText("This has an open conflict. Resolve it in Conflicts first.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Stays on Drive — not a file DriveSync can keep a copy of yet\. Plan/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/offloads/preview")?.body).toEqual({ account_id: 1, paths: ["Work", "photo.jpg"] });
    await userEvent.click(within(dialog).getByRole("button", { name: "Offload" }));
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/offloads")?.body).toEqual({ account_id: 1, paths: ["Work"] });
    // The results open as a fresh dialog with the same title.
    const results = await screen.findByRole("dialog", { name: "Offload 2 items?" });
    expect(await within(results).findByText(/Removed from Google Drive/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(results).getByText("Finished.")).toBeInTheDocument();
  });

  it("Ctrl-click on a selected item takes it out of the selection", async () => {
    setup();
    const work = await screen.findByRole("button", { name: "Work" });
    fireEvent.click(work);
    fireEvent.click(screen.getByRole("button", { name: "photo.jpg" }), { ctrlKey: true });
    fireEvent.click(work, { metaKey: true });
    expect(work).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "true");
  });

  it("Shift-click selects a range", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Work" }));
    fireEvent.click(screen.getByRole("button", { name: "Budget.xlsx" }), { shiftKey: true });
    for (const name of ["Work", "photo.jpg", "Budget.xlsx"]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
    }
    expect(screen.getByRole("button", { name: "Plan" })).toHaveAttribute("aria-pressed", "false");
  });

  it("right-click outside the selection acts on that one item only", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Work" }));
    fireEvent.click(screen.getByRole("button", { name: "photo.jpg" }), { ctrlKey: true });
    fireEvent.contextMenu(screen.getByRole("button", { name: "Budget.xlsx" }));
    expect(screen.getByRole("menu", { name: "Actions for Budget.xlsx" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Work" })).toHaveAttribute("aria-pressed", "false");
  });

  it("the Delete key deletes every selected item", async () => {
    setup();
    const work = await screen.findByRole("button", { name: "Work" });
    fireEvent.click(work);
    fireEvent.click(screen.getByRole("button", { name: "photo.jpg" }), { ctrlKey: true });
    work.focus();
    await userEvent.keyboard("{Delete}");
    expect(screen.getByRole("dialog", { name: "Delete 2 items?" })).toBeInTheDocument();
  });

  it("says why nothing can be offloaded and keeps the button off", async () => {
    setup("/drive", { "POST /api/offloads/preview": { ...PREVIEW, items: [PREVIEW.items[1]], total_files: 0, total_bytes: 0 } });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "photo.jpg" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "photo.jpg"?' });
    await within(dialog).findByText(/open conflict/);
    expect(within(dialog).getByRole("button", { name: "Offload" })).toBeDisabled();
  });

  it("shows why syncing is stopped and keeps the Offload button off", async () => {
    setup("/drive", { "POST /api/offloads/preview": { ...PREVIEW, stopped: "Syncing is stopped: the storage disk is missing." } });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    await within(dialog).findByText("Syncing is stopped: the storage disk is missing.");
    expect(within(dialog).getByRole("button", { name: "Offload" })).toBeDisabled();
  });

  it("shows the server's message when an offload is refused", async () => {
    setup("/drive", {
      "POST /api/offloads/preview": { ...PREVIEW, items: [PREVIEW.items[0]] },
      "POST /api/offloads": json(503, { detail: "Syncing is stopped: the storage disk is missing." }),
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    await within(dialog).findByText(/12 files, 3.0 MB freed/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Offload" }));
    expect(await within(dialog).findByText(/Syncing is stopped: the storage disk is missing\./)).toBeInTheDocument();
  });

  it("doesn't offer Offload for Google files, items only on Drive or outside synced folders", async () => {
    setup();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Plan" }));
    expect(screen.queryByRole("menuitem", { name: "Offload…" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    fireEvent.contextMenu(screen.getByRole("button", { name: "incoming.pdf" }));
    expect(screen.queryByRole("menuitem", { name: "Offload…" })).not.toBeInTheDocument();
  });

  it("offers Restore to Drive on an offloaded item, for the whole offload", async () => {
    const { calls } = setup("/drive?path=Work", {
      "GET /api/browse": browse("Work", [item({ name: "a.txt", path: "Work/Old/a.txt", status: "offloaded" })]),
      "GET /api/offloads": [OFFLOAD],
      "POST /api/offloads/3/restore": { ...OFFLOAD, state: "RESTORING" },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "a.txt" }));
    expect(screen.queryByRole("menuitem", { name: "Offload…" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Restore to Drive" })).toBeEnabled());
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore to Drive" }));
    const dialog = screen.getByRole("dialog", { name: "Restore to Drive?" });
    expect(within(dialog).getByText(/Work\/Old/)).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      "If Google Drive now has a newer file with the same name, Drive's version is kept and yours is saved in Recovery.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore to Drive" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/offloads/3/restore")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows the server's message when a restore is refused", async () => {
    setup("/drive?path=Work", {
      "GET /api/browse": browse("Work", [item({ name: "Old", path: "Work/Old", is_dir: true, status: "offloaded" })]),
      "GET /api/offloads": [OFFLOAD],
      "POST /api/offloads/3/restore": json(409, { detail: "This synced folder is busy. Try again in a moment." }),
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Old" }));
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Restore to Drive" })).toBeEnabled());
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore to Drive" }));
    const dialog = screen.getByRole("dialog", { name: "Restore to Drive?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore to Drive" }));
    expect(await within(dialog).findByText("This synced folder is busy. Try again in a moment.")).toBeInTheDocument();
  });
  const OFFLOADED = item({ name: "Old", path: "Work/Old", is_dir: true, size: null, status: "offloaded", mime: null });

  it("Shift with the arrow keys selects a range", async () => {
    setup();
    const work = await screen.findByRole("button", { name: "Work" });
    work.focus();
    await userEvent.keyboard("{ArrowRight}{ArrowLeft}");
    await userEvent.keyboard("{Shift>}{ArrowRight}{ArrowRight}{/Shift}");
    for (const name of ["Work", "photo.jpg", "Budget.xlsx"]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
    }
    expect(screen.getByRole("button", { name: "Budget.xlsx" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Plan" })).toHaveAttribute("aria-pressed", "false");
  });

  it("Ctrl or ⌘ with an arrow moves without changing the selection, and Space adds the item", async () => {
    setup();
    const work = await screen.findByRole("button", { name: "Work" });
    work.focus();
    await userEvent.keyboard(" ");
    expect(work).toHaveAttribute("aria-pressed", "true");
    await userEvent.keyboard("{Control>}{ArrowRight}{ArrowRight}{/Control}");
    expect(screen.getByRole("button", { name: "Budget.xlsx" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.keyboard(" ");
    expect(work).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Budget.xlsx" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.keyboard("{Meta>}{ArrowLeft}{/Meta} ");
    expect(screen.getByRole("button", { name: "photo.jpg" })).toHaveAttribute("aria-pressed", "true");
  });

  it("Escape closes the Offload dialog and focus goes back to the item", async () => {
    setup("/drive", { "POST /api/offloads/preview": PREVIEW });
    const work = await screen.findByRole("button", { name: "Work" });
    work.focus();
    await userEvent.keyboard("{Shift>}{F10}{/Shift}");
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(work).toHaveFocus();
  });

  it("Escape closes the Restore and Delete dialogs and focus goes back to the item", async () => {
    setup("/drive?path=Work", { "GET /api/browse": browse("Work", [OFFLOADED]), "GET /api/offloads": [OFFLOAD] });
    const old = await screen.findByRole("button", { name: "Old" });
    old.focus();
    await userEvent.keyboard("{Shift>}{F10}{/Shift}");
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "Restore to Drive" })).toBeEnabled());
    await userEvent.click(screen.getByRole("menuitem", { name: "Restore to Drive" }));
    expect(screen.getByRole("dialog", { name: "Restore to Drive?" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(old).toHaveFocus();
    await userEvent.keyboard("{Delete}");
    expect(screen.getByRole("dialog", { name: 'Delete "Old"?' })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(old).toHaveFocus();
  });

  it("keeps focus inside the dialog when the results replace the buttons, and reads results out", async () => {
    setup("/drive", {
      "POST /api/offloads/preview": { ...PREVIEW, items: [PREVIEW.items[0]] },
      "POST /api/offloads": { id: "b2", account_id: 1, done: false, items: [{ path: "Work", state: "running", message: null, offload_id: null }] },
      "GET /api/offloads/batches/b2": { id: "b2", account_id: 1, done: false, items: [{ path: "Work", state: "running", message: null, offload_id: null }] },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    await within(dialog).findByText(/12 files, 3.0 MB freed/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Offload" }));
    const hide = await within(screen.getByRole("dialog")).findByRole("button", { name: "Hide" });
    expect(hide).toHaveFocus();
    const results = screen.getByRole("list", { name: "Results" });
    expect(results.closest("[aria-live]")).not.toBeNull();
    expect(within(results).getByText(/Working on it/)).toBeInTheDocument();
  });

  it("says when it lost track of an offload and offers Retry", async () => {
    let fail = true;
    setup("/drive", {
      "POST /api/offloads/preview": { ...PREVIEW, items: [PREVIEW.items[0]] },
      "POST /api/offloads": { id: "b3", account_id: 1, done: false, items: [{ path: "Work", state: "running", message: null, offload_id: null }] },
      "GET /api/offloads/batches/b3": () =>
        fail
          ? json(500, { detail: "the server could not answer" })
          : { id: "b3", account_id: 1, done: true, items: [{ path: "Work", state: "done", message: "Kept on this server.", offload_id: 4 }] },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    await within(dialog).findByText(/12 files, 3.0 MB freed/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Offload" }));
    expect(await screen.findByText(/the server could not answer/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText("Working on it. You can close this; the work goes on.")).not.toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Finished.")).toBeInTheDocument();
  });

  it("doesn't ask for the preview again while the dialog is open", async () => {
    const { calls } = setup("/drive", { "POST /api/offloads/preview": PREVIEW });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Offload…" }));
    const dialog = await screen.findByRole("dialog", { name: 'Offload "Work"?' });
    await within(dialog).findByText(/12 files, 3.0 MB freed/);
    fireEvent.focus(window);
    window.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("online"));
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.filter((c) => c.path === "/api/offloads/preview")).toHaveLength(1);
  });

  it("offers no Rename or Move for an offloaded item and says why", async () => {
    setup("/drive?path=Work", { "GET /api/browse": browse("Work", [OFFLOADED]), "GET /api/offloads": [OFFLOAD] });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Old" }));
    expect(screen.queryByRole("menuitem", { name: "Rename" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Move" })).not.toBeInTheDocument();
    expect(screen.getByText("Offloaded: restore it to Drive to rename or move it.")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeEnabled();
  });

  it("warns that deleting an offloaded item deletes the only copy", async () => {
    const { calls } = setup("/drive?path=Work", {
      "GET /api/browse": browse("Work", [OFFLOADED]),
      "GET /api/offloads": [OFFLOAD],
      "GET /api/sync-pairs": [{ ...PAIR, id: 7, backup_retention_days: 30 }],
      "POST /api/files/delete": { paths: ["Work/Old"], syncs: {} },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Old" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: 'Delete "Old"?' });
    await waitFor(() =>
      expect(dialog).toHaveTextContent(
        "This is the only copy: it's no longer on Google Drive. It goes to Recovery and is removed for good after 30 days.",
      ),
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/files/delete")?.body).toEqual({ account_id: 1, paths: ["Work/Old"], only_copy_ok: true }),
    );
  });

  it("warns that deleting a folder holding an offloaded item deletes its only copy, also with the Delete key", async () => {
    const { calls } = setup("/drive", {
      "GET /api/offloads": [OFFLOAD],
      "GET /api/sync-pairs": [{ ...PAIR, id: 7, backup_retention_days: 14 }],
      "POST /api/files/delete": { paths: ["Work"], syncs: {} },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Work" }));
    await userEvent.keyboard("{Delete}");
    const dialog = screen.getByRole("dialog", { name: 'Delete "Work"?' });
    await waitFor(() =>
      expect(dialog).toHaveTextContent(
        "Offloaded inside it: Work/Old. This is the only copy: it's no longer on Google Drive. It goes to Recovery and is removed for good after 14 days.",
      ),
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/files/delete")?.body).toEqual({ account_id: 1, paths: ["Work"], only_copy_ok: true }),
    );
  });

  it("when the server finds offloaded items the dialog didn't know about, it shows them and asks again", async () => {
    let offloads: unknown[] = [];
    const { calls } = setup("/drive", {
      "GET /api/offloads": () => offloads,
      "POST /api/files/delete": ({ body }: { body: { only_copy_ok?: boolean } }) => {
        if (body.only_copy_ok) return { paths: ["Work"], syncs: {} };
        offloads = [OFFLOAD];
        return json(409, { detail: { message: "This deletes offloaded items, whose only copy is on this server. Read the warning, then delete again: Work/Old.", offloaded: ["Work/Old"] } });
      },
    });
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Work" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: 'Delete "Work"?' });
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await within(dialog).findByText(/Read the warning, then delete again/)).toBeInTheDocument();
    await waitFor(() => expect(dialog).toHaveTextContent("Offloaded inside it: Work/Old."));
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "/api/files/delete").map((c) => c.body)).toEqual([
        { account_id: 1, paths: ["Work"] },
        { account_id: 1, paths: ["Work"], only_copy_ok: true },
      ]),
    );
  });

  it("a mixed selection: Move is off, and Delete lists what it leaves out and why", async () => {
    const { calls } = setup("/drive?path=Work", {
      "GET /api/browse": browse("Work", [OFFLOADED, item({ name: "far.pdf", path: "Work/far.pdf", local: false, status: "download" }), item({ name: "b.txt", path: "Work/b.txt" })]),
      "GET /api/offloads": [OFFLOAD],
      "POST /api/files/delete": { paths: ["Work/Old", "Work/b.txt"], syncs: {} },
    });
    const old = await screen.findByRole("button", { name: "Old" });
    fireEvent.click(old);
    fireEvent.click(screen.getByRole("button", { name: "b.txt" }), { shiftKey: true });
    fireEvent.contextMenu(old);
    const menu = screen.getByRole("menu", { name: "Actions for 3 items" });
    expect(within(menu).getByRole("menuitem", { name: "Move" })).toBeDisabled();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete 2 items?" });
    expect(dialog).toHaveTextContent("Not deleted: far.pdf: only on Google Drive so far, nothing here to delete.");
    expect(dialog).toHaveTextContent("This is the only copy");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/files/delete")?.body).toEqual({ account_id: 1, paths: ["Work/Old", "Work/b.txt"], only_copy_ok: true }),
    );
  });
});
