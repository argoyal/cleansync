import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { MailMessageInfo } from "../../api/types";
import { ACCOUNT, mockApi, renderApp } from "../../test/utils";
import { MessageMailActions, OffloadOneDialog } from "./MailActions";

const MESSAGE: MailMessageInfo = {
  id: 9, account_id: 1, provider_id: "p9", thread_id: "t1", from_addr: "Shop <shop@example.com>", to_addrs: "alice@example.com", cc_addrs: "",
  subject: "Your order", date: "2026-10-05T10:00:00Z", size: 2000, file_size: 2100, labels: [], state: "in_mail", offload_id: null, attachments: [],
  has_remote_images: false, gmail_url: null, file_missing: false, decision: null,
};

describe("Offload on one mail", () => {
  it("says the mail goes to Gmail's trash", () => {
    mockApi({});
    renderApp(<OffloadOneDialog account={ACCOUNT} messageId={1} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(dialog).toHaveTextContent("moves the mail to Gmail's trash");
  });

  it("says the mail is deleted from Gmail for good when the mailbox allows it", () => {
    mockApi({});
    renderApp(<OffloadOneDialog account={{ ...ACCOUNT, permanent_delete: true }} messageId={1} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(dialog).toHaveTextContent("deletes the mail from Gmail for good");
    expect(dialog).not.toHaveTextContent("trash");
  });
});

describe("one mail in the reader", () => {
  it("says why when Offload finds the mail isn't in Gmail any more", async () => {
    mockApi({ "POST /api/mail/messages/9/offload": { offloaded: false, reason: "This mail is no longer in Gmail." } });
    renderApp(<OffloadOneDialog account={ACCOUNT} messageId={9} onClose={() => {}} />);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Offload" }));
    expect(await screen.findByText("It wasn't offloaded: This mail is no longer in Gmail.")).toBeInTheDocument();
  });

  it("deletes it forever with permanent delete on: its view, its id and the typed 1", async () => {
    const { calls } = mockApi({
      "POST /api/mail/delete-forever": { deleted: 0, left: 1, job_id: 3 },
      "GET /api/mail/offloads/3": { id: 3, account_id: 1, filter_text: "Inbox", state: "RUNNING", total: 1, done: 0, skipped: 0, not_done: 1, bytes: 0, reason: null, created_at: "2026-10-10T09:00:00Z", finished_at: null, kind: "delete_forever", removed: 0, trashed: 0, trash_until: "2026-11-09", google_emptied: false, stopped: false, skipped_items: [] },
    });
    renderApp(<MessageMailActions account={{ ...ACCOUNT, permanent_delete: true }} message={MESSAGE} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete forever…" }));
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    await userEvent.type(within(dialog).getByLabelText("Type 1 to delete forever"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "all", message_ids: [9], confirm: 1 }));
  });
});
