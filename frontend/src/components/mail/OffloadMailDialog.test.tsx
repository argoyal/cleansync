import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, json, mockApi, renderApp } from "../../test/utils";
import { buildQuery, EMPTY_FIELDS, OffloadMailDialog, parseTyped } from "./OffloadMailDialog";

const ACTIVE = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const }, mail_permission: true };
const brief = (id: number, subject: string, size: number, date: string) => ({ id, subject, from_addr: "bank@example.com", date, size });
const PREVIEW = {
  filter_text: "older_than:1y from:bank@example.com",
  count: 1234,
  bytes: 850 * 1024 ** 2,
  largest: [brief(1, "Big statement", 20 * 1024 ** 2, "2023-02-01T00:00:00Z")],
  oldest: [brief(2, "First statement", 10_000, "2015-01-01T00:00:00Z")],
  excluded: [{ reason: "too_new", count: 4, text: "4 mails left out: newer than 30 days. Recent mail always stays in Gmail." }],
};

describe("the offload filter", () => {
  it("builds the same search language the reader uses", () => {
    expect(buildQuery({ ...EMPTY_FIELDS, age: "1", unit: "y", from: "bank@example.com", larger: "5", attachments: true, label: "Old receipts" })).toBe(
      'older_than:1y from:bank@example.com label:"Old receipts" larger:5M has:attachment',
    );
    expect(buildQuery(EMPTY_FIELDS)).toBe("");
    expect(parseTyped("1,234")).toBe(1234);
    expect(parseTyped(" 12 ")).toBe(12);
    expect(parseTyped("12a")).toBeNull();
  });
});

describe("Offload mail…", () => {
  it("previews, asks for the typed count, starts, and opens the offload", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [{ id: "Label_2", name: "Receipts", count: 3 }] },
      "POST /api/mail/offloads/preview": PREVIEW,
      "POST /api/mail/offloads": { offload_id: 7, queued: "queued" },
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    await userEvent.type(within(dialog).getByLabelText("Older than"), "1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Unit"), "y");
    await userEvent.type(within(dialog).getByLabelText("From"), "bank@example.com");
    expect(within(dialog).getByLabelText("Filter")).toHaveValue("older_than:1y from:bank@example.com");
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/offloads/preview")?.body).toEqual({ account_id: 1, q: "older_than:1y from:bank@example.com" }));
    expect(await within(dialog).findByText("1,234 mails, 850 MB. The space is freed in Gmail once Gmail's trash is emptied.")).toBeInTheDocument();
    expect(within(dialog).getByText("4 mails left out: newer than 30 days. Recent mail always stays in Gmail.")).toBeInTheDocument();
    expect(within(within(dialog).getByRole("list", { name: "Largest" })).getByText(/Big statement/)).toBeInTheDocument();
    expect(within(within(dialog).getByRole("list", { name: "Oldest" })).getByText(/First statement/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "See them all in the mail list" })).toHaveAttribute("href", "/mail?q=older_than%3A1y+from%3Abank%40example.com");
    const start = within(dialog).getByRole("button", { name: "Start offload" });
    expect(start).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("To start, type 1,234"), "1,234");
    await userEvent.click(start);
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/mail/offloads")?.body).toEqual({ account_id: 1, q: "older_than:1y from:bank@example.com", confirm: 1234 }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/mail/offloads/7"));
  });

  it("starts from a search, and from picked threads with a fixed list", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "POST /api/mail/offloads/preview": { ...PREVIEW, filter_text: "2 picked mails", count: 2 },
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ thread_ids: ["t1", "t2"] }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).queryByLabelText("Filter")).not.toBeInTheDocument();
    expect(within(dialog).getByText("The picked threads.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/offloads/preview")?.body).toEqual({ account_id: 1, thread_ids: ["t1", "t2"] }));
  });

  it("says why it can't offload, and changes nothing", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "POST /api/mail/offloads/preview": () => json(409, { detail: "The first mail backup hasn't finished yet. Offload works once every mail is backed up." }),
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "older_than:1y" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("The first mail backup hasn't finished yet. Offload works once every mail is backed up.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start offload" })).not.toBeInTheDocument();
  });

  it("shows the backend's own message when it refuses to start, and lets you preview again", async () => {
    const CHECKING = "Wait until the mail backup has finished checking your mailbox, then try again.";
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "POST /api/mail/offloads/preview": { ...PREVIEW, count: 3 },
      "POST /api/mail/offloads": () => json(409, { detail: CHECKING }),
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "older_than:1y" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await userEvent.type(await screen.findByLabelText("To start, type 3"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Start offload" }));
    expect(await screen.findByText(CHECKING)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start offload" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();
    expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail$/);
  });

  it("keeps Start offload off until the typed number matches", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "POST /api/mail/offloads/preview": PREVIEW,
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "older_than:1y" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    const typed = await screen.findByLabelText("To start, type 1,234");
    await userEvent.type(typed, "1,233");
    expect(screen.getByRole("button", { name: "Start offload" })).toBeDisabled();
    await userEvent.clear(typed);
    await userEvent.type(typed, "1234");
    expect(screen.getByRole("button", { name: "Start offload" })).toBeEnabled();
  });

  it("offers no start when nothing matches", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/counts": { views: {}, labels: [] },
      "POST /api/mail/offloads/preview": { ...PREVIEW, count: 0, bytes: 0, largest: [], oldest: [] },
    });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "older_than:1y" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/^0 mails, 0 B\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start offload" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/To start, type/)).not.toBeInTheDocument();
  });

  it("closes on Escape and keeps Tab inside", async () => {
    mockApi({ "GET /api/accounts": [ACTIVE], "GET /api/mail/counts": { views: {}, labels: [] } });
    const onClose = vi.fn();
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "older_than:1y" }} onClose={onClose} />, { withAccount: true, route: "/mail" });
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).getByLabelText("Older than")).toHaveFocus();
    const all = Array.from(dialog.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input:not([disabled]), select, textarea"));
    all[all.length - 1].focus();
    await userEvent.tab();
    expect(all[0]).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(all[all.length - 1]).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("never offers to empty Gmail's trash: Gmail does that by itself after 30 days", () => {
    mockApi({ "GET /api/accounts": [ACTIVE], "GET /api/mail/counts": { views: {}, labels: [] } });
    renderApp(<OffloadMailDialog account={ACTIVE} start={{ q: "" }} onClose={() => undefined} />, { withAccount: true, route: "/mail" });
    const dialog = screen.getByRole("dialog", { name: "Offload mail" });
    expect(within(dialog).getByText(/Gmail empties its trash on its own after 30 days; that's when the space comes back\./)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Empty from Gmail trash/i)).not.toBeInTheDocument();
  });
});
