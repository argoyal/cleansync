import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../test/utils";
import { MailUnsubscribePage } from "./MailUnsubscribePage";

const MAILBOX = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const }, mail_permission: true };
const SENDER = {
  address: "news@shop.example",
  name: "Shop News",
  count: 12,
  read_pct: 8,
  inbox_pct: 50,
  last_at: "2026-10-10T08:00:00Z",
  bytes: 2_400_000,
  status: null,
  method: null,
  note: null,
  newest_unsubscribe: "<https://shop.example/u>",
  newest_unsubscribe_post: null,
  newest_mail_id: 9,
};
const OTHER = { ...SENDER, address: "deals@store.example", name: "Store Deals", status: "kept" };
const page = (items: unknown[], extra: Record<string, unknown> = {}) => ({ items, total: items.length, suggested: 2, scan: { scanned: 10, total: 10 }, ...extra });
const JOB = { id: 5, account_id: 1, kind: "unsubscribe", state: "RUNNING", total: 1, done: 0, stop_asked: false, reason: null, created_at: "2026-10-14T08:00:00Z", finished_at: null, items: [] };

function setUp(routes: Record<string, unknown> = {}, account = MAILBOX) {
  return mockApi({
    "GET /api/accounts": [account],
    "GET /api/mail/unsubscribe/senders": page([SENDER, OTHER]),
    "GET /api/mail/unsubscribe/jobs": null,
    "POST /api/mail/unsubscribe/jobs": json(202, { job_id: 5 }),
    "GET /api/mail/unsubscribe/jobs/5": JOB,
    ...routes,
  });
}

const sendersCalls = (calls: { method: string; path: string; url: URL }[]) =>
  calls.filter((c) => c.method === "GET" && c.path === "/api/mail/unsubscribe/senders");

describe("Mail → Unsubscribe", () => {
  it("lists senders from the server with their counts and status", async () => {
    setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    expect(await screen.findByRole("heading", { name: "Unsubscribe" })).toBeInTheDocument();
    expect(screen.getByText("Senders whose mail has an unsubscribe link.")).toBeInTheDocument();
    const row = (await screen.findByText("Shop News")).closest("tr")!;
    expect(row).toHaveTextContent("news@shop.example");
    expect(row).toHaveTextContent("12");
    expect(row).toHaveTextContent("8%");
    expect(row).toHaveTextContent("50%");
    expect(screen.getByText("Store Deals").closest("tr")).toHaveTextContent("Kept");
  });

  it("asks the server for the tab picked", async () => {
    const { calls } = setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await screen.findByText("Shop News");
    expect(sendersCalls(calls)[0].url.searchParams.get("tab")).toBe("suggested");
    await userEvent.click(screen.getByRole("tab", { name: "All" }));
    await waitFor(() => expect(sendersCalls(calls).some((c) => c.url.searchParams.get("tab") === "all")).toBe(true));
  });

  it("waits for typing to stop before searching", async () => {
    const { calls } = setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await screen.findByText("Shop News");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search senders" }), "news");
    await waitFor(() => expect(sendersCalls(calls).some((c) => c.url.searchParams.get("q") === "news")).toBe(true));
    const searched = sendersCalls(calls).map((c) => c.url.searchParams.get("q") ?? "").filter(Boolean);
    expect(searched.every((q) => q === "news")).toBe(true);
  });

  it("says old mail is still being scanned", async () => {
    setUp({ "GET /api/mail/unsubscribe/senders": page([SENDER], { scan: { scanned: 1200, total: 45000 } }) });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    expect(await screen.findByText("Scanning old mail: 1,200 of 45,000…")).toBeInTheDocument();
  });

  it("Pick all unsubscribes from every sender in the tab and search", async () => {
    const { calls } = setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await screen.findByText("Shop News");
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick all" }));
    const bar = screen.getByRole("region", { name: "Picked senders" });
    expect(bar).toHaveTextContent("All 2 senders picked");
    await userEvent.click(within(bar).getByRole("button", { name: "Unsubscribe" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/mail/unsubscribe/jobs")?.body).toEqual({
        account_id: 1,
        kind: "unsubscribe",
        all: { tab: "suggested", q: "" },
      }),
    );
    expect(await screen.findByRole("dialog", { name: "Unsubscribe" })).toBeInTheDocument();
  });

  it("unsubscribes from one sender from its row", async () => {
    const { calls } = setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Unsubscribe from news@shop.example" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/mail/unsubscribe/jobs")?.body).toEqual({
        account_id: 1,
        kind: "unsubscribe",
        addresses: ["news@shop.example"],
      }),
    );
  });

  it("shows the server's message when an unsubscribe is already running", async () => {
    setUp({ "POST /api/mail/unsubscribe/jobs": json(409, { detail: "An unsubscribe is already running for this mailbox." }) });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Unsubscribe from news@shop.example" }));
    expect(await screen.findByText("An unsubscribe is already running for this mailbox.")).toBeInTheDocument();
  });

  it("offers Delete forever only when the mailbox allows permanent delete", async () => {
    setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick news@shop.example" }));
    const bar = screen.getByRole("region", { name: "Picked senders" });
    expect(within(bar).getByRole("button", { name: "Offload their mail" })).toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: "Delete forever their mail" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "More actions for news@shop.example" }));
    expect(screen.getByRole("menuitem", { name: "Offload their mail" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Delete forever their mail" })).not.toBeInTheDocument();
  });

  it("opens Delete forever with the sender's search and the count of their mail", async () => {
    const { calls } = setUp({ "GET /api/mail/totals": { total: 12, total_bytes: 2_400_000 } }, { ...MAILBOX, permanent_delete: true });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "More actions for news@shop.example" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete forever their mail" }));
    expect(await screen.findByRole("dialog", { name: /Delete forever/ })).toBeInTheDocument();
    const totals = calls.find((c) => c.path === "/api/mail/totals");
    expect(totals?.url.searchParams.get("q")).toBe("from:news@shop.example");
  });

  it("offers Offload and Delete forever on the bar only for one sender picked by hand", async () => {
    setUp({}, { ...MAILBOX, permanent_delete: true });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick news@shop.example" }));
    const bar = screen.getByRole("region", { name: "Picked senders" });
    expect(within(bar).getByRole("button", { name: "Offload their mail" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Delete forever their mail" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick deals@store.example" }));
    expect(within(bar).queryByRole("button", { name: "Offload their mail" })).not.toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: "Delete forever their mail" })).not.toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Unsubscribe" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pick all" }));
    expect(bar).toHaveTextContent("All 2 senders picked");
    expect(within(bar).queryByRole("button", { name: "Offload their mail" })).not.toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: "Delete forever their mail" })).not.toBeInTheDocument();
  });

  it("offloads one sender's mail with a from: search the dialog shows", async () => {
    const { calls } = setUp({ "POST /api/mail/offloads/preview": { filter_text: "", count: 0, bytes: 0, oldest: [], largest: [], excluded: [] } });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick news@shop.example" }));
    await userEvent.click(screen.getByRole("button", { name: "Offload their mail" }));
    const dialog = await screen.findByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).getByLabelText("Filter")).toHaveValue("from:news@shop.example");
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/offloads/preview")?.body).toMatchObject({ q: "from:news@shop.example" }));
  });

  it("Open their mail goes to a search for the sender", async () => {
    setUp();
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "More actions for news@shop.example" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Open their mail" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(`/mail?q=${encodeURIComponent("from:news@shop.example")}`));
  });

  it("offers Open page and Mark unsubscribed for a sender the last job couldn't confirm", async () => {
    const { calls } = setUp({
      "GET /api/mail/unsubscribe/jobs": {
        ...JOB,
        state: "DONE",
        done: 1,
        items: [{ address: "news@shop.example", outcome: "not_confirmed", method: null, note: "No model to check the page. Open it to finish.", link: "https://shop.example/u" }],
      },
      "POST /api/mail/unsubscribe/mark": { changed: 1 },
    });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    const row = (await screen.findByText("No model to check the page. Open it to finish.")).closest("tr")!;
    const open = within(row).getByRole("link", { name: "Open page" });
    expect(open).toHaveAttribute("href", "https://shop.example/u");
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    await userEvent.click(within(row).getByRole("button", { name: "Mark unsubscribed" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/mail/unsubscribe/mark")?.body).toEqual({ account_id: 1, addresses: ["news@shop.example"], status: "unsubscribed" }),
    );
  });

  it("shows a running job as a chip that opens its window", async () => {
    setUp({ "GET /api/mail/unsubscribe/jobs": { ...JOB, total: 4, done: 1 }, "GET /api/mail/unsubscribe/jobs/5": { ...JOB, total: 4, done: 1 } });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    await userEvent.click(await screen.findByRole("button", { name: "Running: 1 of 4" }));
    expect(await screen.findByRole("dialog", { name: "Unsubscribe" })).toBeInTheDocument();
  });

  it("says when there is nothing to suggest, and when a tab is empty", async () => {
    setUp({ "GET /api/mail/unsubscribe/senders": page([]) });
    renderApp(<MailUnsubscribePage />, { withAccount: true });
    expect(await screen.findByText("Nothing to suggest. Senders you rarely read show up here.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Kept" }));
    expect(await screen.findByText("No senders here.")).toBeInTheDocument();
  });
});
