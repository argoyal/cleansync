import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Sender, UnsubscribeJobItem } from "../../api/unsubscribe";
import { renderApp } from "../../test/utils";
import { SenderTable } from "./SenderTable";

const SENDER: Sender = {
  address: "a@x.example",
  name: "A",
  count: 1234,
  read_pct: 10,
  inbox_pct: 90,
  last_at: "2026-10-10T08:00:00Z",
  bytes: 1_048_576,
  status: null,
  method: null,
  note: null,
  newest_unsubscribe: "<https://x.example/u>",
  newest_unsubscribe_post: null,
  newest_mail_id: 1,
};

const table = (senders: Sender[], latest = new Map<string, UnsubscribeJobItem>()) =>
  renderApp(
    <SenderTable
      accountId={1}
      senders={senders}
      latest={latest}
      forever={false}
      pickState="none"
      isPicked={() => false}
      onToggle={() => undefined}
      onPickAll={() => undefined}
      onAction={() => undefined}
    />,
  );

describe("the sender table", () => {
  it("has the columns, and says each status in words", () => {
    table([
      SENDER,
      { ...SENDER, address: "b@x.example", status: "unsubscribed" },
      { ...SENDER, address: "c@x.example", status: "kept" },
      { ...SENDER, address: "d@x.example", status: "auto_archived" },
    ]);
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["", "Sender", "Mails", "Read", "In Inbox", "Last mail", "Size", "Status", "Actions"]);
    const status = (address: string) => within(screen.getByText(address).closest("tr")!).getAllByRole("cell")[7];
    expect(status("a@x.example")).toHaveTextContent(/^$/);
    expect(status("b@x.example")).toHaveTextContent("Unsubscribed");
    expect(status("c@x.example")).toHaveTextContent("Kept");
    expect(status("d@x.example")).toHaveTextContent("Auto-archived");
  });

  it("shows the latest job's note under the status, and Write to it for a mail address", () => {
    table(
      [SENDER],
      new Map([["a@x.example", { address: "a@x.example", outcome: "needs_you", method: null, note: "Write to this address to unsubscribe, or turn on Allow Gmail filters.", link: "mailto:off@x.example" }]]),
    );
    expect(screen.getByText("Write to this address to unsubscribe, or turn on Allow Gmail filters.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Write to it" })).toHaveAttribute("href", "mailto:off@x.example");
    expect(screen.getByRole("button", { name: "Mark unsubscribed" })).toBeInTheDocument();
  });
});
