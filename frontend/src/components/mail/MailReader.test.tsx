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

function msg(over: Record<string, unknown> & { id: number }) {
  return {
    account_id: 1,
    provider_id: `p${over.id}`,
    thread_id: "t1",
    from_addr: "Shop <shop@example.com>",
    to_addrs: "alice@example.com",
    cc_addrs: "",
    subject: "Your order has shipped",
    date: "2026-10-05T10:00:00Z",
    size: 2000,
    file_size: 2100,
    labels: [],
    state: "in_mail",
    offload_id: null,
    attachments: [],
    has_remote_images: false,
    gmail_url: "https://mail.google.com/mail/u/alice%40example.com/#all/p1",
    file_missing: false,
    ...over,
  };
}

describe("reading a mail", () => {
  const open = (messages: unknown[], route = "/mail?thread=t1", more: Record<string, unknown> = {}) => {
    const mocked = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1" })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": messages,
      ...more,
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route });
    return mocked;
  };

  it("shows the mail in a sandboxed frame with no scripts, and images only when asked", async () => {
    open([msg({ id: 1, has_remote_images: true, labels: [{ id: "Label_2", name: "Receipts" }] })]);
    const frame = await screen.findByTitle("Mail: Your order has shipped");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    expect(frame).toHaveAttribute("src", "/api/mail/messages/1/body");
    expect(frame.getAttribute("sandbox")).not.toMatch(/allow-scripts|allow-same-origin/);
    expect(screen.getByText("Images from the internet are hidden, so the sender can't tell you opened this mail.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show images" }));
    expect(screen.getByTitle("Mail: Your order has shipped")).toHaveAttribute("src", "/api/mail/messages/1/body?images=1");
    expect(screen.getByText("Receipts")).toBeInTheDocument();
  });

  it("frames the mail with no referrer and never puts the mail's HTML in the page", async () => {
    open([msg({ id: 1 })]);
    const frame = await screen.findByTitle("Mail: Your order has shipped");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(screen.queryByRole("button", { name: "Show images" })).not.toBeInTheDocument();
  });

  it("asks the server about remote images when the thread list doesn't know", async () => {
    // The real thread route leaves has_remote_images null; only the one-mail route reads the file.
    const { calls } = open([msg({ id: 1, has_remote_images: null })], "/mail?thread=t1", {
      "GET /api/mail/messages/1": msg({ id: 1, has_remote_images: true }),
    });
    expect(await screen.findByRole("button", { name: "Show images" })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/mail/messages/1")).toBe(true);
  });

  it("shows images only for the mail where they were asked for", async () => {
    open([
      msg({ id: 1, subject: "Plan", date: "2026-10-01T10:00:00Z", has_remote_images: true }),
      msg({ id: 2, subject: "Re: Plan", has_remote_images: true }),
    ]);
    await screen.findByTitle("Mail: Re: Plan");
    await userEvent.click(screen.getByRole("button", { name: "Show images" }));
    expect(screen.getByTitle("Mail: Re: Plan")).toHaveAttribute("src", "/api/mail/messages/2/body?images=1");
    await userEvent.click(screen.getByRole("button", { name: /^Plan, from Shop/ }));
    expect(screen.getByTitle("Mail: Plan")).toHaveAttribute("src", "/api/mail/messages/1/body");
  });

  it("offers attachments to preview or download, the .eml, and Open in Gmail", async () => {
    open([
      msg({
        id: 1,
        attachments: [
          { index: 0, name: "invoice.pdf", type: "application/pdf", size: 1234, preview: true },
          { index: 1, name: "data.bin", type: "application/octet-stream", size: 2, preview: false },
        ],
      }),
    ]);
    const files = await screen.findByRole("list", { name: "Attachments" });
    expect(within(files).getByRole("link", { name: "Preview invoice.pdf" })).toHaveAttribute("href", "/api/mail/messages/1/attachments/0");
    expect(within(files).getByRole("link", { name: "Preview invoice.pdf" })).toHaveAttribute("target", "_blank");
    expect(within(files).getByRole("link", { name: "Download invoice.pdf" })).toHaveAttribute("href", "/api/mail/messages/1/attachments/0?download=1");
    expect(within(files).queryByRole("link", { name: "Preview data.bin" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download .eml" })).toHaveAttribute("href", "/api/mail/messages/1/eml");
    expect(screen.getByRole("link", { name: "Open in Gmail" })).toHaveAttribute("href", "https://mail.google.com/mail/u/alice%40example.com/#all/p1");
  });

  it("opens the newest mail of a thread and folds the older ones", async () => {
    open([msg({ id: 1, subject: "Plan", date: "2026-10-01T10:00:00Z" }), msg({ id: 2, subject: "Re: Plan", state: "offloaded", gmail_url: null })]);
    expect(await screen.findByTitle("Mail: Re: Plan")).toBeInTheDocument();
    expect(screen.queryByTitle("Mail: Plan")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open in Gmail" })).not.toBeInTheDocument(); // not in Gmail now
    expect(screen.getByText("Offloaded")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Plan, from Shop/ }));
    expect(screen.getByTitle("Mail: Plan")).toBeInTheDocument();
  });

  it("says when a mail's file is missing", async () => {
    open([msg({ id: 1, file_missing: true })]);
    expect(await screen.findByText("This mail's file is missing on the storage disk.")).toBeInTheDocument();
    expect(screen.queryByTitle(/^Mail:/)).not.toBeInTheDocument();
  });

  it("on a phone the open mail replaces the list and Back returns to it", async () => {
    open([msg({ id: 1 })], "/mail?view=inbox&thread=t1");
    expect(screen.getByRole("region", { name: "Mail list" })).toHaveClass("hidden", "xl:block");
    await userEvent.click(await screen.findByRole("link", { name: "Back to the list" }));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail\?view=inbox$/);
  });
});
