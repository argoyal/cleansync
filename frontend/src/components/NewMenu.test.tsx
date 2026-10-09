import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AUTH_EVENT, setCsrfToken } from "../api/client";
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
  const view = renderApp(
    <>
      <NewMenu />
      <UploadTray />
    </>,
    { withAccount: true, route: "/drive?path=Work", path: "/drive" },
  );
  return { ...api, client: view.client };
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
    expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("All uploads done");
  });

  it("shows why an upload failed and lets the person cancel one", async () => {
    setSender((job) => ({
      xhr: { abort: vi.fn() } as unknown as XMLHttpRequest,
      done: job.relative === "bad.bin" ? Promise.reject(new Error("the server disk is full")) : new Promise<void>(() => undefined),
    }));
    setup();
    await userEvent.upload(screen.getByLabelText("Files to upload"), [new File(["x"], "bad.bin"), new File(["y"], "slow.bin")]);
    expect(await screen.findByText("the server disk is full")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel upload of slow.bin" }));
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

  it("offers a link when the browser blocks the new Google tab", async () => {
    const open = vi.fn().mockReturnValueOnce(null).mockReturnValue({});
    vi.stubGlobal("open", open);
    setup({ "POST /api/files/new": { path: null, google_url: "https://docs.google.com/document/d/N1/edit", syncs: {} } });
    await userEvent.click(within(await openMenu()).getByRole("menuitem", { name: "Google Docs" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Blank Google Doc" }));
    expect(await screen.findByText("Your new Google Doc is ready.")).toBeInTheDocument();
    expect(screen.queryByText(/Created/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Open it" }));
    expect(open).toHaveBeenLastCalledWith("https://docs.google.com/document/d/N1/edit", "_blank", "noopener");
  });

  it("aborts a request in flight and drops a queued one without starting it", async () => {
    const abort = vi.fn();
    const started: string[] = [];
    setSender((job) => {
      started.push(job.relative);
      return { xhr: { abort } as unknown as XMLHttpRequest, done: new Promise<void>(() => undefined) };
    });
    setup();
    await userEvent.upload(screen.getByLabelText("Files to upload"), ["a", "b", "c"].map((n) => new File(["x"], n)));
    await userEvent.click(screen.getByRole("button", { name: "Cancel upload of c" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel upload of a" }));
    expect(abort).toHaveBeenCalledTimes(1);
    expect(started).toEqual(["a", "b"]);
    expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("Uploading 1 item");
  });

  it("uses the folder-relative path as the upload name", async () => {
    const started: string[] = [];
    setSender((job) => {
      started.push(job.relative);
      return { xhr: { abort: vi.fn() } as unknown as XMLHttpRequest, done: new Promise<void>(() => undefined) };
    });
    setup();
    const f = new File(["x"], "p.jpg");
    Object.defineProperty(f, "webkitRelativePath", { value: "Trip/day 1/p.jpg" });
    await userEvent.upload(screen.getByLabelText("Folder to upload"), [f]);
    expect(started).toEqual(["Trip/day 1/p.jpg"]);
    expect(screen.getByText("Trip/day 1/p.jpg")).toBeInTheDocument();
  });

  it("says a picked folder is empty", async () => {
    setup();
    await waitFor(() => expect(screen.getByRole("button", { name: "New" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Folder to upload"), { target: { files: [] } });
    expect(await screen.findByText(/folder is empty/)).toBeInTheDocument();
  });

  it("frees the slot when the sender throws and summarises the outcome", async () => {
    setSender((job) => {
      if (job.relative === "bad") throw new Error("could not start");
      return { xhr: { abort: vi.fn() } as unknown as XMLHttpRequest, done: Promise.resolve() };
    });
    setup();
    await userEvent.upload(screen.getByLabelText("Files to upload"), ["bad", "ok1", "ok2"].map((n) => new File(["x"], n)));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not start");
    await waitFor(() => expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("Finished: 1 failed"));
  });

  it("refreshes the list once when the queue drains, not per file", async () => {
    setSender(() => ({ xhr: { abort: vi.fn() } as unknown as XMLHttpRequest, done: Promise.resolve() }));
    const { client } = setup();
    await waitFor(() => expect(screen.getByRole("button", { name: "New" })).toBeEnabled());
    const refresh = vi.spyOn(client, "invalidateQueries");
    const before = refresh.mock.calls.length;
    await act(async () => uploads.add(["a", "b", "c"].map((n) => new File(["x"], n)), 1, "Work"));
    await waitFor(() => expect(screen.getByRole("region", { name: "Uploads" })).toHaveTextContent("All uploads done"));
    // One refresh per file-list query key, once, not once per file.
    const per = refresh.mock.calls.length - before;
    expect(per).toBeGreaterThan(0);
    expect(per).toBeLessThanOrEqual(6);
  });
});

describe("the real upload request", () => {
  function fakeXhr(status: number, body: unknown) {
    const sent: Record<string, string> = {};
    class FakeXhr {
      upload = {} as XMLHttpRequest["upload"];
      status = status;
      responseText = JSON.stringify(body);
      onload: () => void = () => undefined;
      onerror: () => void = () => undefined;
      onabort: () => void = () => undefined;
      open() {}
      setRequestHeader(k: string, v: string) {
        sent[k] = v;
      }
      send() {
        queueMicrotask(() => this.onload());
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    return sent;
  }
  const job = () => ({ file: new File(["x"], "a.txt"), accountId: 1, folder: "", relative: "a.txt" });

  it.each([
    [409, "That name can't be used on Windows."],
    [503, "Syncing is switched off."],
    [507, "the server doesn't have enough free disk space for this file"],
    [422, "Field required"],
  ])("shows the server's message for %i", async (status, detail) => {
    fakeXhr(status, { detail: status === 422 ? [{ msg: detail }] : detail });
    await expect(sendFile(job(), () => undefined).done).rejects.toThrow(detail);
  });

  it("sends the CSRF header on a request that completes", async () => {
    setCsrfToken("tok2");
    const sent = fakeXhr(200, { path: "a.txt" });
    await expect(sendFile(job(), () => undefined).done).resolves.toBe("a.txt");
    expect(sent["X-CSRF-Token"]).toBe("tok2");
  });

  it("signals a lost session like the rest of the app", async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(AUTH_EVENT, on);
    fakeXhr(401, { detail: "session_expired" });
    await expect(sendFile(job(), () => undefined).done).rejects.toThrow();
    window.removeEventListener(AUTH_EVENT, on);
    expect(seen).toEqual(["login"]);
  });
});

describe("+ New in a Drive-to-here folder", () => {
  it("only offers Google files, which are made on Drive itself", async () => {
    setup({
      "GET /api/browse": {
        account_id: 1,
        path: "Work",
        pair: { id: 7, name: "Down", status: "HEALTHY", initialized: true, local_subpath: "Work", direction: "REMOTE_TO_LOCAL" },
        items: [],
      },
    });
    const menu = await openMenu();
    await waitFor(() => expect(within(menu).getByRole("menuitem", { name: "New folder" })).toBeDisabled());
    expect(within(menu).getByRole("menuitem", { name: "Upload files" })).toBeDisabled();
    expect(within(menu).getByRole("menuitem", { name: "Upload folder" })).toBeDisabled();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Google Docs" }));
    const sub = screen.getByRole("menu", { name: "Google Docs" });
    expect(within(sub).getByRole("menuitem", { name: "Blank Google Doc" })).toBeEnabled();
    expect(within(sub).getByRole("menuitem", { name: "Word file (.docx)" })).toBeDisabled();
  });

  it("is off outside synced folders and says why", async () => {
    setup({ "GET /api/browse": { account_id: 1, path: "Work", pair: null, items: [] } });
    const menu = await openMenu();
    expect(await within(menu).findByText("This folder isn't synced with Google Drive. Open a synced folder to add files.")).toBeInTheDocument();
    for (const name of ["New folder", "Upload files", "Upload folder"]) {
      expect(within(menu).getByRole("menuitem", { name })).toBeDisabled();
    }
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Google Docs" }));
    const sub = screen.getByRole("menu", { name: "Google Docs" });
    expect(within(sub).getByRole("menuitem", { name: "Blank Google Doc" })).toBeDisabled();
    expect(within(sub).getByRole("menuitem", { name: "Word file (.docx)" })).toBeDisabled();
  });
});
