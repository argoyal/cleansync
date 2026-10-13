import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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

  it("hands Delete forever to the reader with the mail's view and id, and opens no dialog itself", async () => {
    mockApi({});
    const onForever = vi.fn();
    renderApp(<MessageMailActions account={{ ...ACCOUNT, permanent_delete: true }} message={MESSAGE} onForever={onForever} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete forever…" }));
    expect(onForever).toHaveBeenCalledWith({ view: "all", message_ids: [9] });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
