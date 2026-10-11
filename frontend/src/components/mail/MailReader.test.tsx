import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
import { MailReader } from "./MailReader";

const ACTIVE = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const }, mail_permission: true };

export function thread(over: Record<string, unknown> & { thread_id: string }) {
  return {
    count: 1,
    last_date: "2026-10-03T10:00:00Z",
    message_id: 1,
    from_addr: "Google Cloud <billing@example.com>",
    subject: "Your invoice for September",
    snippet: "invoice.pdf attached",
    has_attachments: false,
    unread: false,
    offloaded: false,
    deleted: false,
    ...over,
  };
}

describe("message list", () => {
  it("shows threads with sender, subject, snippet, a paperclip and marks", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": {
        threads: [
          thread({ thread_id: "t1", has_attachments: true, unread: true, count: 3 }),
          thread({ thread_id: "t2", subject: "Statement for August", offloaded: true }),
          thread({ thread_id: "t3", subject: "Weekly digest #112", deleted: true }),
        ],
        next_cursor: null,
        total: 5,
        total_bytes: 6.2 * 1024 ** 3,
      },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    expect(await screen.findByRole("heading", { name: "All mail" })).toBeInTheDocument();
    expect(await screen.findByText("5 mails · 6.2 GB")).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Mail" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const first = within(rows[0]).getByRole("link");
    expect(first).toHaveAccessibleName(/Google Cloud.*Your invoice for September.*3 mails/);
    expect(first).toHaveAttribute("href", "/mail?thread=t1");
    expect(within(rows[0]).getByRole("img", { name: "Has attachments" })).toBeInTheDocument();
    expect(within(rows[1]).getByText("Offloaded")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Deleted in Gmail")).toBeInTheDocument();
  });

  it("puts the marks and date in a row's spoken name", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", unread: true, has_attachments: true, offloaded: true, deleted: true })], next_cursor: null, total: 1, total_bytes: 10 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    const link = await screen.findByRole("link", { name: /Your invoice/ });
    expect(link).toHaveAccessibleName(/unread.*has attachments.*offloaded.*deleted in Gmail/);
    expect(link).toHaveAccessibleName(/Oct/);
  });

  it("keeps loaded rows when Show more fails", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": ({ url }: { url: URL }) =>
        url.searchParams.get("cursor") === "c2"
          ? json(500, { detail: "Could not load more." })
          : { threads: [thread({ thread_id: "t1", subject: "First" })], next_cursor: "c2", total: 2, total_bytes: 10 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Could not load more.")).toBeInTheDocument();
    expect(screen.getByText("First")).toBeInTheDocument();
  });

  it("loads more a page at a time", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": ({ url }: { url: URL }) =>
        url.searchParams.get("cursor") === "c2"
          ? { threads: [thread({ thread_id: "t3", subject: "Third" })], next_cursor: null, total: null, total_bytes: null }
          : { threads: [thread({ thread_id: "t1", subject: "First" }), thread({ thread_id: "t2", subject: "Second" })], next_cursor: "c2", total: 3, total_bytes: 3000 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=inbox" });
    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
    await screen.findByText("Second");
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Third")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    const asked = calls.filter((c) => c.path === "/api/mail/threads").map((c) => c.url.searchParams.get("view"));
    expect(asked).toEqual(["inbox", "inbox"]);
    // The first page carries no empty cursor, label or search, so the server counts the totals.
    const firstUrl = calls.find((c) => c.path === "/api/mail/threads")!.url;
    expect(firstUrl.searchParams.has("cursor")).toBe(false);
    expect(firstUrl.searchParams.has("label")).toBe(false);
    expect(firstUrl.searchParams.has("q")).toBe(false);
  });

  it("searches, and keeps the search when opening a thread", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t9", subject: "Statement for June" })], next_cursor: null, total: 1, total_bytes: 100 },
      "GET /api/mail/threads/t9": [],
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?q=from%3Abank" });
    expect(await screen.findByRole("heading", { name: "Search results" })).toBeInTheDocument();
    expect(screen.getByText(/for from:bank/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/api/mail/threads")?.url.searchParams.get("q")).toBe("from:bank");
    expect(await screen.findByRole("link", { name: /Statement for June/ })).toHaveAttribute("href", "/mail?q=from%3Abank&thread=t9");
  });

  it("shows a search the parser can't read as a plain message", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": () => json(422, { detail: '"abc" is not a size. Use a number with K, M or G, like 5M.' }),
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?q=larger%3Aabc" });
    expect(await screen.findByText('"abc" is not a size. Use a number with K, M or G, like 5M.')).toBeInTheDocument();
  });

  it("shows a too-long search as a plain message", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": () => json(400, { detail: "That search is too long. Keep it under 1,000 characters." }),
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: `/mail?q=${"a".repeat(1001)}` });
    expect(await screen.findByText("That search is too long. Keep it under 1,000 characters.")).toBeInTheDocument();
  });

  it("says when a view is empty", async () => {
    mockApi({ "GET /api/accounts": [ACTIVE], "GET /api/mail/threads": { threads: [], next_cursor: null, total: 0, total_bytes: 0 } });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=deleted" });
    expect(await screen.findByText("No mail here.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("heading", { name: "Deleted in Gmail" })).toBeInTheDocument());
  });
});
