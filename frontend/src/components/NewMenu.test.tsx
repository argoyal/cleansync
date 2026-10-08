import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import { sendFile, setSender, uploads } from "../hooks/uploads";
import { ACCOUNT, mockApi, renderApp } from "../test/utils";
import { NewMenu } from "./NewMenu";
import { UploadTray } from "./UploadTray";

afterEach(() => {
  uploads.reset();
  setSender(sendFile);
});

function setup(extra: Record<string, unknown> = {}) {
  const api = mockApi({ "GET /api/accounts": [ACCOUNT], ...extra });
  renderApp(
    <>
      <NewMenu />
      <UploadTray />
    </>,
    { withAccount: true, route: "/drive?path=Work", path: "/drive" },
  );
  return api;
}

async function openMenu() {
  await waitFor(() => expect(screen.getByRole("button", { name: "New" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "New" }));
  return screen.getByRole("menu", { name: "New" });
}

describe("+ New", () => {
  it("offers the Drive-style entries with Google and Office choices", async () => {
    setup();
    const menu = await openMenu();
    for (const name of ["New folder", "Upload files", "Upload folder"]) {
      expect(within(menu).getByRole("menuitem", { name })).toBeInTheDocument();
    }
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Google Docs" }));
    const sub = screen.getByRole("menu", { name: "Google Docs" });
    expect(within(sub).getByRole("menuitem", { name: "Blank Google Doc" })).toBeInTheDocument();
    expect(within(sub).getByRole("menuitem", { name: "Word file (.docx)" })).toBeInTheDocument();
  });

  it("makes a folder in the folder being viewed", async () => {
    const { calls } = setup({ "POST /api/files/mkdir": { paths: ["Work/Plans"], syncs: {} } });
    await userEvent.click(within(await openMenu()).getByRole("menuitem", { name: "New folder" }));
    const input = screen.getByLabelText("Folder name");
    await userEvent.clear(input);
    await userEvent.type(input, "Plans");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/files/mkdir")?.body).toEqual({ account_id: 1, path: "Work", name: "Plans" }));
  });

  it("makes an Office file locally and says when it reaches Drive", async () => {
    const { calls } = setup({ "POST /api/files/new": { path: "Work/Untitled spreadsheet.xlsx", google_url: null, syncs: {} } });
    await userEvent.click(within(await openMenu()).getByRole("menuitem", { name: "Google Sheets" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Excel file (.xlsx)" }));
    expect(await screen.findByText(/Created Untitled spreadsheet.xlsx/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/files/new")?.body).toEqual({ account_id: 1, path: "Work", kind: "xlsx" });
  });

  it("opens a new Google Doc in a tab opened during the click", async () => {
    const tab = { location: { href: "" }, opener: {}, close: vi.fn() };
    const open = vi.fn(() => tab);
    vi.stubGlobal("open", open);
    setup({ "POST /api/files/new": { path: null, google_url: "https://docs.google.com/document/d/N1/edit", syncs: {} } });
    await userEvent.click(within(await openMenu()).getByRole("menuitem", { name: "Google Docs" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Blank Google Doc" }));
    expect(open).toHaveBeenCalledWith("", "_blank");
    await waitFor(() => expect(tab.location.href).toBe("https://docs.google.com/document/d/N1/edit"));
    expect(tab.opener).toBeNull();
  });

  it("closes the waiting tab and explains when Drive refuses", async () => {
    const tab = { location: { href: "" }, opener: {}, close: vi.fn() };
    vi.stubGlobal("open", vi.fn(() => tab));
    setup({
      "POST /api/files/new": () =>
        new Response(JSON.stringify({ detail: "This folder isn't on Google Drive yet. Sync it, then try again." }), { status: 409 }),
    });
    await userEvent.click(within(await openMenu()).getByRole("menuitem", { name: "Google Slides" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Blank Google Slides" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("isn't on Google Drive yet");
    expect(tab.close).toHaveBeenCalled();
  });

  it("uploads picked files into the current folder, two at a time, with progress", async () => {
    const started: { relative: string; folder: string }[] = [];
    const finish: (() => void)[] = [];
    setSender((job, onProgress) => {
      started.push({ relative: job.relative, folder: job.folder });
      onProgress(job.file.size / 2);
      return { xhr: { abort: vi.fn() } as unknown as XMLHttpRequest, done: new Promise<void>((r) => finish.push(r)) };
    });
    setup();
    const input = screen.getByLabelText("Files to upload");
    const files = ["a.txt", "b.txt", "c.txt"].map((n) => new File(["hello"], n));
    await userEvent.upload(input, files);
    expect(started.map((s) => s.relative)).toEqual(["a.txt", "b.txt"]);
    expect(started[0].folder).toBe("Work");
    expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("Uploading 3 items");
    await act(async () => finish[0]());
    expect(started.map((s) => s.relative)).toEqual(["a.txt", "b.txt", "c.txt"]);
    await act(async () => {
      finish[1]();
      finish[2]();
    });
    expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("Uploads done");
  });

  it("shows why an upload failed and lets the person cancel one", async () => {
    setSender((job) => ({
      xhr: { abort: vi.fn() } as unknown as XMLHttpRequest,
      done: job.relative === "bad.bin" ? Promise.reject(new Error("the server disk is full")) : new Promise<void>(() => undefined),
    }));
    setup();
    await userEvent.upload(screen.getByLabelText("Files to upload"), [new File(["x"], "bad.bin"), new File(["y"], "slow.bin")]);
    expect(await screen.findByText("the server disk is full")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText(/Cancelled/)).toBeInTheDocument();
  });

  it("says so when the server saved a file under a different name", async () => {
    setSender((job) => ({
      xhr: { abort: vi.fn() } as unknown as XMLHttpRequest,
      done: Promise.resolve(`Work/${job.relative.replace(".txt", " (1).txt")}`),
    }));
    setup();
    await userEvent.upload(screen.getByLabelText("Files to upload"), [new File(["x"], "a.txt")]);
    expect(await screen.findByText(/Saved as a \(1\)\.txt/)).toBeInTheDocument();
  });

  it("sends the CSRF header with each upload", () => {
    const sent: Record<string, string> = {};
    class FakeXhr {
      upload = {} as XMLHttpRequest["upload"];
      status = 200;
      responseText = JSON.stringify({ path: "Work/a.txt" });
      onload: () => void = () => undefined;
      open() {}
      setRequestHeader(k: string, v: string) {
        sent[k] = v;
      }
      send() {}
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    setCsrfToken("tok");
    sendFile({ file: new File(["x"], "a.txt"), accountId: 1, folder: "Work", relative: "a.txt" }, () => undefined);
    expect(sent["X-CSRF-Token"]).toBe("tok");
  });
});
