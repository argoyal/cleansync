import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
import { allPickedText, MailReader } from "./MailReader";

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
        total: null,
        total_bytes: null,
      },
      "GET /api/mail/totals": { total: 5, total_bytes: 6.2 * 1024 ** 3 },
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

  it("shows the list without waiting for the totals", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "Quick one" })], next_cursor: null, total: null, total_bytes: null },
      "GET /api/mail/totals": () => new Promise(() => {}), // never answers
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    expect(await screen.findByText("Quick one")).toBeInTheDocument();
    expect(screen.queryByText(/mails ·/)).not.toBeInTheDocument();
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
    // The first page carries no empty cursor, label or search.
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

  it("says the date of each mail in its spoken header", async () => {
    open([msg({ id: 1, subject: "Plan", date: "2026-10-01T10:00:00Z" }), msg({ id: 2, subject: "Re: Plan" })]);
    expect(await screen.findByRole("button", { name: /^Plan, from Shop <shop@example\.com>, .*Oct/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Re: Plan, from Shop <shop@example\.com>, .*Oct/ })).toBeInTheDocument();
  });

  it("on a phone the open mail replaces the list and Back returns to it", async () => {
    open([msg({ id: 1 })], "/mail?view=inbox&thread=t1");
    expect(screen.getByRole("region", { name: "Mail list" })).toHaveClass("hidden", "xl:block");
    await userEvent.click(await screen.findByRole("link", { name: "Back to the list" }));
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail\?view=inbox$/);
  });

  describe("on a phone", () => {
    const narrow = (wide = false) =>
      vi.stubGlobal("matchMedia", (query: string) => ({ matches: wide, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));

    it("says Opening… while the thread loads, then moves focus to its heading", async () => {
      narrow();
      let release = () => undefined as void;
      const ready = new Promise<void>((r) => (release = r));
      open([], "/mail?thread=t1", {
        "GET /api/mail/threads/t1": async () => {
          await ready;
          return [msg({ id: 1 })];
        },
      });
      expect(await screen.findByRole("heading", { name: "Opening…" })).toBeInTheDocument();
      release();
      const heading = await screen.findByRole("heading", { name: "Your order has shipped" });
      await waitFor(() => expect(heading).toHaveFocus());
    });

    it("Back to the list puts focus back on the row the mail was opened from", async () => {
      narrow();
      mockApi({
        "GET /api/accounts": [ACTIVE],
        "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "First" }), thread({ thread_id: "t2", subject: "Second" })], next_cursor: null, total: 2, total_bytes: 2 },
        "GET /api/mail/threads/t2": [msg({ id: 2, subject: "Second" })],
      });
      renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=inbox" });
      await userEvent.click(await screen.findByRole("link", { name: /Second/ }));
      await waitFor(() => expect(screen.getByRole("heading", { name: "Second" })).toHaveFocus());
      await userEvent.click(screen.getByRole("link", { name: "Back to the list" }));
      await waitFor(() => expect(screen.getByRole("link", { name: /Second/ })).toHaveFocus());
    });

    it("leaves focus alone on a wide screen, where the list stays in view", async () => {
      narrow(true);
      open([msg({ id: 1 })]);
      await screen.findByRole("heading", { name: "Your order has shipped" });
      expect(screen.getByRole("heading", { name: "Your order has shipped" })).not.toHaveFocus();
    });
  });
});

describe("offload and restore in the reader", () => {
  it("offers Offload mail… starting from the search, and Restore for offloaded mail", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1" })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": [msg({ id: 1, state: "offloaded", gmail_url: null })],
      "POST /api/mail/restore": { queued: "queued", count: 1 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?q=from%3Abank&thread=t1" });
    await userEvent.click(await screen.findByRole("button", { name: "Restore to Gmail" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, message_ids: [1] }));
    expect(await screen.findByText("Putting 1 mail back in Gmail. Sync activity shows when it's done.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Offload mail…" }));
    expect(screen.getByLabelText("Filter")).toHaveValue("from:bank");
  });

  it("picks threads in the list and offloads or restores them together", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "One" }), thread({ thread_id: "t2", subject: "Two" })], next_cursor: null, total: 2, total_bytes: 2 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Two" }));
    const bar = screen.getByRole("region", { name: "Picked mail" });
    expect(within(bar).getByText("2 threads picked")).toBeInTheDocument();
    await userEvent.click(within(bar).getByRole("button", { name: "Offload" }));
    expect(screen.getByText("The picked threads.")).toBeInTheDocument();
  });

  it("restores picked threads together, then clears the picks", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "One", offloaded: true }), thread({ thread_id: "t2", subject: "Two", offloaded: true })], next_cursor: null, total: 2, total_bytes: 2 },
      "POST /api/mail/restore": { queued: "queued", count: 2 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=offloaded" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick Two" }));
    await userEvent.click(within(screen.getByRole("region", { name: "Picked mail" })).getByRole("button", { name: "Restore to Gmail" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, thread_ids: ["t1", "t2"], view: "offloaded" }));
    expect(await screen.findByText("Putting 2 mails back in Gmail. Sync activity shows when it's done.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Picked mail" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Pick One" })).not.toBeChecked();
  });

  it("keeps a picked row's link and its spoken name", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", unread: true })], next_cursor: null, total: 1, total_bytes: 1 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick Your invoice for September" }));
    const link = screen.getByRole("link", { name: /Your invoice for September/ });
    expect(link).toHaveAccessibleName(/unread.*Oct/);
    expect(link).toHaveAttribute("data-thread", "t1");
    expect(link.querySelector("input")).toBeNull(); // the checkbox sits beside the link, not in it
  });

  it("tells the offload dialog which label isn't part of the filter", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 3 }] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1" })], next_cursor: null, total: 1, total_bytes: 1 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?label=Label_2" });
    await screen.findByRole("heading", { name: "Receipts" });
    await userEvent.click(screen.getByRole("button", { name: "Offload mail…" }));
    expect(screen.getByText(/not only Receipts/)).toBeInTheDocument();
  });
});


describe("AI rules in the reader", () => {
  const CHIP = { id: 3, rule_name: "Newsletters", status: "suggested", how: "ai", why: "a weekly digest" };

  it("shows a rule chip beside the row's link and says it in the row's name", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", decision: CHIP })], next_cursor: null, total: 1, total_bytes: 1 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail" });
    const link = await screen.findByRole("link", { name: /Your invoice for September/ });
    expect(link).toHaveAccessibleName(/suggested: Newsletters/);
    const chip = screen.getByRole("button", { name: /Newsletters · suggested/ });
    expect(link.contains(chip)).toBe(false); // a button inside a link isn't valid HTML
  });

  it("approves every suggestion shown in the Suggested view", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": {
        threads: [thread({ thread_id: "t1", decision: CHIP }), thread({ thread_id: "t2", decision: { ...CHIP, id: 4 } }), thread({ thread_id: "t3", decision: { ...CHIP, id: 5, status: "applied" } })],
        next_cursor: null,
        total: 3,
        total_bytes: 3,
      },
      "POST /api/rule-decisions/approve": { applied: 2 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=suggested" });
    await userEvent.click(await screen.findByRole("button", { name: "Approve all" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-decisions/approve")?.body).toEqual({ account_id: 1, ids: [3, 4] }));
  });
});

describe("the CleanSync views", () => {
  const OFF = {
    id: 7, account_id: 1, filter_text: "older_than:1y", state: "DONE", total: 3, done: 3, skipped: 0, not_done: 0,
    bytes: 3000, reason: null, created_at: "2026-10-10T09:00:00Z", finished_at: "2026-10-10T09:20:00Z",
    trash_until: "2026-11-09", google_emptied: false,
  };

  it("opens Offloaded for an old Local archive link", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [], next_cursor: null, total: 0, total_bytes: 0 },
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=archive" });
    expect(await screen.findByRole("heading", { name: "Offloaded" })).toBeInTheDocument();
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/threads")?.url.searchParams.get("view")).toBe("offloaded"));
  });

  it("shows the offload batches in a tab of Offloaded", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [], next_cursor: null, total: 0, total_bytes: 0 },
      "GET /api/mail/offloads": [OFF],
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=offloaded&tab=batches" });
    const tabs = await screen.findByRole("navigation", { name: "Offloaded" });
    expect(within(tabs).getByRole("link", { name: "Batches" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Mail" })).toHaveAttribute("href", "/mail?view=offloaded");
    expect(await screen.findByRole("link", { name: "older_than:1y" })).toHaveAttribute("href", "/mail/offloads/7");
    expect(screen.queryByRole("list", { name: "Mail" })).not.toBeInTheDocument();
  });

  it("opens a thread with the mails of the view it is in", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", offloaded: true })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": [msg({ id: 2, state: "offloaded", gmail_url: null })],
    });
    renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route: "/mail?view=offloaded&thread=t1" });
    await screen.findByRole("region", { name: "Open mail" });
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/threads/t1")?.url.searchParams.get("view")).toBe("offloaded"));
  });
});

describe("Pick all and the actions of each view", () => {
  const TWO = { threads: [thread({ thread_id: "t1", subject: "One" }), thread({ thread_id: "t2", subject: "Two" })], next_cursor: null, total: null, total_bytes: null };
  const api = (more: Record<string, unknown> = {}, total = 12430) =>
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": TWO,
      "GET /api/mail/totals": { total, total_bytes: 1 },
      ...more,
    });
  const open = (route: string) => renderApp(<MailReader account={ACTIVE} />, { withAccount: true, route });
  const bar = () => screen.getByRole("region", { name: "Picked mail" });

  it("has three states, and says what is picked", async () => {
    api();
    open("/mail?view=inbox&q=from%3Ashop");
    const all = await screen.findByRole("checkbox", { name: "Pick all" });
    expect(all).not.toBeChecked();
    await screen.findByText(/12,430 mails/);
    await userEvent.click(all);
    expect(all).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick One" })).toBeChecked();
    expect(within(bar()).getByText("All 12,430 mails in this search are picked.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick One" }));
    expect(all).toBePartiallyChecked();
    expect(within(bar()).getByText("1 thread picked")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Pick Two" })).toBeChecked();
    await userEvent.click(all);
    expect(all).toBeChecked();
    await userEvent.click(all);
    expect(all).not.toBeChecked();
    expect(screen.queryByRole("region", { name: "Picked mail" })).not.toBeInTheDocument();
  });

  it("says this view when there is no search, and The 1 mail for one", () => {
    expect(allPickedText(1204, false)).toBe("All 1,204 mails in this view are picked.");
    expect(allPickedText(1, true)).toBe("The 1 mail in this search is picked.");
    expect(allPickedText(undefined, true)).toBe("All mails in this search are picked.");
  });

  it("offers Offload and Make a rule… in Gmail views, and offloads a pick-all as the view and search", async () => {
    const { calls } = api({ "POST /api/mail/offloads/preview": { filter_text: "Inbox · from:shop", count: 2, bytes: 10, largest: [], oldest: [], excluded: [] } });
    open("/mail?view=inbox&q=from%3Ashop");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    expect(within(bar()).queryByRole("button", { name: "Restore to Gmail" })).not.toBeInTheDocument();
    expect(within(bar()).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(within(bar()).getByRole("button", { name: "Make a rule…" })).toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Offload" }));
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).getByText("Every mail of the list you picked them in.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/offloads/preview")?.body).toEqual({ account_id: 1, view: "inbox", q: "from:shop" }));
  });

  it("restores a pick-all of Offloaded as the view and search", async () => {
    const { calls } = api({ "POST /api/mail/restore": { queued: "queued", count: 2 } });
    open("/mail?view=offloaded&label=Label_2");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    expect(within(bar()).queryByRole("button", { name: "Offload" })).not.toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Restore to Gmail" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/restore")?.body).toEqual({ account_id: 1, view: "offloaded", label: "Label_2", q: "" }));
  });

  it("deletes a pick-all of Deleted in Gmail forever after the typed count", async () => {
    const { calls } = api({ "POST /api/mail/delete-forever": { deleted: 7, left: 0 } }, 7);
    open("/mail?view=deleted");
    await screen.findByText(/7 mails/);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    const dialog = screen.getByRole("dialog", { name: "Delete forever" });
    expect(dialog).toHaveTextContent("7 mails from Deleted in Gmail.");
    await userEvent.type(within(dialog).getByLabelText("Type 7 to delete forever"), "7");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "deleted", q: "", confirm: 7 }));
    expect(await screen.findByText("Deleted 7 mails forever.")).toBeInTheDocument();
  });

  it("deletes picked Offloaded threads forever by their offloaded mails, and keeps the pick on Cancel", async () => {
    const { calls } = api({
      "GET /api/mail/threads/t1": [msg({ id: 4, state: "offloaded" }), msg({ id: 5, state: "offloaded" })],
      "POST /api/mail/delete-forever": { deleted: 2, left: 0 },
    });
    open("/mail?view=offloaded");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick One" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("checkbox", { name: "Pick One" })).toBeChecked();
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.find((c) => c.path === "/api/mail/threads/t1")?.url.searchParams.get("view")).toBe("offloaded");
    await userEvent.type(within(dialog).getByLabelText("Type 2 to delete forever"), "2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete forever" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/delete-forever")?.body).toEqual({ account_id: 1, view: "offloaded", message_ids: [4, 5], confirm: 2 }));
  });

  it("has no Pick all in Suggested, and approves the picked suggestions", async () => {
    const CHIP = { id: 3, rule_name: "Newsletters", status: "suggested", how: "ai", why: "a digest" };
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1", subject: "One", decision: CHIP }), thread({ thread_id: "t2", subject: "Two", decision: { ...CHIP, id: 4 } })], next_cursor: null, total: 2, total_bytes: 2 },
      "POST /api/rule-decisions/approve": { applied: 1 },
    });
    open("/mail?view=suggested");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick Two" }));
    expect(screen.queryByRole("checkbox", { name: "Pick all" })).not.toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/rule-decisions/approve")?.body).toEqual({ account_id: 1, ids: [4] }));
  });

  // One mounted reader whose address changes, like a search typed in the panel.
  const withLink = (to: string) => (
    <>
      <MailReader account={ACTIVE} />
      <Link to={to}>Go elsewhere</Link>
    </>
  );

  it("clears Pick all when the search changes", async () => {
    api();
    renderApp(withLink("/mail?view=inbox&q=b"), { withAccount: true, route: "/mail?view=inbox&q=a" });
    const all = await screen.findByRole("checkbox", { name: "Pick all" });
    await userEvent.click(all);
    expect(all).toBeChecked();
    expect(screen.getByRole("region", { name: "Picked mail" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Go elsewhere" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Picked mail" })).not.toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: "Pick all" })).not.toBeChecked();
  });

  it("closes an open Offload or Delete forever dialog when the search changes", async () => {
    api();
    renderApp(withLink("/mail?view=inbox&q=b"), { withAccount: true, route: "/mail?view=inbox&q=a" });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Offload" }));
    expect(screen.getByRole("dialog", { name: "Offload mail" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Go elsewhere"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes an open Delete forever dialog when the view changes", async () => {
    api({}, 7);
    renderApp(withLink("/mail?view=offloaded"), { withAccount: true, route: "/mail?view=deleted" });
    await screen.findByText(/7 mails/);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick all" }));
    await userEvent.click(within(bar()).getByRole("button", { name: "Delete forever…" }));
    expect(screen.getByRole("dialog", { name: "Delete forever" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Go elsewhere"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers Offload and Make a rule… for a mail in Gmail, and Restore or Delete forever… for one out of Gmail", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "GET /api/mail/threads": { threads: [thread({ thread_id: "t1" })], next_cursor: null, total: 1, total_bytes: 1 },
      "GET /api/mail/threads/t1": ({ url }: { url: URL }) =>
        url.searchParams.get("view") === "offloaded" ? [msg({ id: 2, state: "offloaded", gmail_url: null })] : [msg({ id: 1 })],
      "POST /api/mail/messages/1/offload": { offloaded: true, reason: null },
    });
    const first = open("/mail?thread=t1");
    const reader = await screen.findByRole("region", { name: "Open mail" });
    expect(await within(reader).findByRole("button", { name: "Make a rule…" })).toBeInTheDocument();
    expect(within(reader).queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
    await userEvent.click(within(reader).getByRole("button", { name: "Offload" }));
    const ask = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(within(ask).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(within(ask).getByRole("button", { name: "Offload" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/messages/1/offload")?.body).toEqual({ account_id: 1 }));
    expect(await screen.findByText("Offloaded. CleanSync keeps its copy in Offloaded.")).toBeInTheDocument();
    first.unmount();
    open("/mail?view=offloaded&thread=t1");
    const out = await screen.findByRole("region", { name: "Open mail" });
    expect(await within(out).findByRole("button", { name: "Delete forever…" })).toBeInTheDocument();
    expect(within(out).getByRole("button", { name: "Restore to Gmail" })).toBeInTheDocument();
    expect(within(out).queryByRole("button", { name: "Offload" })).not.toBeInTheDocument();
  });
});
