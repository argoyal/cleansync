import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
import { DeleteForeverDialog, ForeverButton } from "./DeleteForeverDialog";

const JOB = {
  id: 7,
  account_id: 1,
  filter_text: "Inbox · from:shop.example.com",
  state: "RUNNING",
  total: 1284,
  done: 330,
  skipped: 10,
  not_done: 944,
  bytes: 0,
  reason: null,
  created_at: "2026-10-13T10:00:00Z",
  finished_at: null,
  trash_until: "2026-11-12",
  google_emptied: false,
  kind: "delete_forever",
  removed: 330,
  trashed: 0,
  stopped: false,
  skipped_items: [],
};

describe("Delete forever on mail in Gmail", () => {
  it("is offered disabled, with the reason, when the mailbox doesn't allow permanent delete", () => {
    renderApp(<ForeverButton account={ACCOUNT} onClick={() => {}} />);
    const button = screen.getByRole("button", { name: "Delete forever…" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Turn on Allow permanent delete in Settings → Accounts.");
  });

  it("is offered when it does", async () => {
    const onClick = vi.fn();
    renderApp(<ForeverButton account={{ ...ACCOUNT, permanent_delete: true }} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete forever…" }));
    expect(onClick).toHaveBeenCalled();
  });

  it("starts a background job after the typed count, and shows its progress with Stop", async () => {
    const { calls } = mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": JOB,
      "POST /api/mail/offloads/7/stop": {},
    });
    renderApp(
      <DeleteForeverDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ view: "inbox", q: "from:shop.example.com" }} count={1284} onClose={() => {}} />,
    );
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("1,284 mails in Gmail.");
    expect(dialog).toHaveTextContent("They are deleted from Gmail for good, and CleanSync's copy is deleted too.");
    await userEvent.type(within(dialog).getByLabelText("Type 1,284 to delete forever"), "1284");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    expect(await screen.findByText("Deleting… 340 of 1,284")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toMatchObject({
      account_id: 1,
      view: "inbox",
      q: "from:shop.example.com",
      confirm: 1284,
    });
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/mail/offloads/7/stop")).toBe(true));
  });

  it("says what was deleted and what stayed when the job is done", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": { ...JOB, state: "DONE_WITH_SKIPS", done: 1283, skipped: 1, not_done: 0 },
    });
    renderApp(<DeleteForeverDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ view: "all", message_ids: [9] }} count={1} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("Type 1 to delete forever"), "1");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Delete forever" })).getByRole("button", { name: "Delete forever" }));
    expect(await screen.findByText("Deleted 1,283 mails forever.")).toBeInTheDocument();
    expect(screen.getByText("1 mail wasn't deleted and stays in Gmail, with CleanSync's copy.")).toBeInTheDocument();
  });
  const open = async () => {
    renderApp(<DeleteForeverDialog account={{ ...ACCOUNT, permanent_delete: true }} start={{ view: "all", message_ids: [9] }} count={1} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("Type 1 to delete forever"), "1");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Delete forever" })).getByRole("button", { name: "Delete forever" }));
  };

  it("shows the error, with Close, when the job can't be read", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": json(404, { detail: "That offload doesn't exist." }),
    });
    await open();
    expect(await screen.findByText("That offload doesn't exist.")).toBeInTheDocument();
    expect(screen.queryByText(/Deleting…/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("reads a job the owner stopped as a calm note, not an error", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": {
        ...JOB,
        state: "FAILED",
        done: 330,
        skipped: 0,
        stopped: true,
        reason: "Stopped by you. What wasn't deleted yet stays in Gmail, with CleanSync's copy.",
      },
    });
    await open();
    expect(await screen.findByText("Stopped. Deleted 330 mails forever; the rest are still in Gmail.")).toBeInTheDocument();
    expect(screen.queryByText(/Stopped by you/)).not.toBeInTheDocument();
  });

  it("reads the stopped flag, not the wording of the reason", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": { ...JOB, state: "FAILED", done: 330, skipped: 0, stopped: true, reason: "You pressed Stop. The rest stays in Gmail." },
    });
    await open();
    expect(await screen.findByText("Stopped. Deleted 330 mails forever; the rest are still in Gmail.")).toBeInTheDocument();
  });

  it("shows a failure as a failure even when its reason begins with the same words", async () => {
    mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 0, job_id: 7 },
      "GET /api/mail/offloads/7": { ...JOB, state: "FAILED", done: 330, skipped: 0, stopped: false, reason: "Stopped by you is not what happened: Gmail refused." },
    });
    await open();
    expect(await screen.findByText(/Gmail refused/)).toBeInTheDocument();
    expect(screen.queryByText(/the rest are still in Gmail/)).not.toBeInTheDocument();
  });

  it("treats a missing job_id like null: the copy was removed at once", async () => {
    mockApi({ "POST /api/mail/delete-forever": { deleted: 2, left: 0 } });
    const onClose = vi.fn();
    renderApp(<DeleteForeverDialog account={ACCOUNT} start={{ view: "offloaded", message_ids: [1, 2] }} count={2} onClose={onClose} />);
    await userEvent.type(screen.getByLabelText("Type 2 to delete forever"), "2");
    await userEvent.click(within(screen.getByRole("dialog", { name: "Delete forever" })).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
