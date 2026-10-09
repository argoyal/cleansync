import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, item, mockApi, renderApp } from "../test/utils";
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
});

describe("Not synced", () => {
  it("marks local-only items outside synced folders as Not synced and says New is off", async () => {
    setup("/drive", {
      "GET /api/browse": { account_id: 1, path: "", pair: null, items: [
        item({ name: "Loose", path: "Loose", is_dir: true, size: null, status: "unsynced", pair_id: null, remote: false, mime: null }),
      ] },
    });
    const folders = await screen.findByRole("region", { name: "Folders" });
    expect(within(folders).getAllByText("Not synced").length).toBeGreaterThan(0);
    expect(screen.getByText("This folder isn't synced with Google Drive. Open a synced folder to add files.")).toBeInTheDocument();
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
