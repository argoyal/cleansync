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
